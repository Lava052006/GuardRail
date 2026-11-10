from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_CEILING
import os
import math

from sqlalchemy import text

from .models import (
    BudgetLimits,
    BudgetSnapshot,
    OperationRequest,
    PolicyDecision,
)
from .policy_engine import PolicyEngine

class RateLimitExceeded(Exception):
    def __init__(self, retry_after):
        self.retry_after = max(1, int(retry_after))
        super().__init__(
            f"Throttle rate limit exceeded. Retry after "
            f"{self.retry_after} seconds."
        )
class BudgetManager:

    ACTIVE_STATUSES = ("RESERVED", "COMMITTED")

    def __init__(self, engine):
        self.engine = engine
        self.policy_engine = PolicyEngine()

        self.throttle_rate_limit = int(
        os.getenv("THROTTLE_RATE_LIMIT", "5")
    )

        self.throttle_window_seconds = int(
        os.getenv("THROTTLE_WINDOW_SECONDS", "60")
    )

        if self.throttle_rate_limit < 1:
             raise ValueError("THROTTLE_RATE_LIMIT must be positive")

        if self.throttle_window_seconds < 1:
            raise ValueError("THROTTLE_WINDOW_SECONDS must be positive")
    def initialize(self):
        """Create persistent budget and reservation tables."""

        statements = [

            """
            CREATE TABLE IF NOT EXISTS budgets (
                id BIGSERIAL PRIMARY KEY,
                scope VARCHAR(20) NOT NULL,
                user_id VARCHAR(100) NOT NULL,
                project_id VARCHAR(100),
                service VARCHAR(100),
                daily_limit NUMERIC(18,6) NOT NULL,
                monthly_limit NUMERIC(18,6) NOT NULL,
                operation_limit NUMERIC(18,6) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CHECK (scope IN ('USER', 'PROJECT', 'SERVICE')),
                CHECK (daily_limit > 0),
                CHECK (monthly_limit > 0),
                CHECK (operation_limit >= 0)
            )
            """,

            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_user
            ON budgets(user_id)
            WHERE scope = 'USER'
            """,

            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_project
            ON budgets(user_id, project_id)
            WHERE scope = 'PROJECT'
            """,

            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_service
            ON budgets(user_id, project_id, service)
            WHERE scope = 'SERVICE'
            """,

            """
            CREATE TABLE IF NOT EXISTS budget_reservations (
                id UUID PRIMARY KEY,
                user_id VARCHAR(100) NOT NULL,
                project_id VARCHAR(100) NOT NULL,
                service VARCHAR(100) NOT NULL,
                operation VARCHAR(150) NOT NULL,
                estimated_cost NUMERIC(18,6) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'RESERVED',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CHECK (estimated_cost >= 0),
                CHECK (status IN ('RESERVED', 'COMMITTED', 'RELEASED'))
            )
            """,

            """
            CREATE TABLE IF NOT EXISTS budget_reservation_allocations (
                reservation_id UUID NOT NULL
                    REFERENCES budget_reservations(id),
                budget_id BIGINT NOT NULL REFERENCES budgets(id),
                amount NUMERIC(18,6) NOT NULL,
                PRIMARY KEY (reservation_id, budget_id),
                CHECK (amount >= 0)
            )
            """,

            """
            CREATE INDEX IF NOT EXISTS idx_reservation_usage
            ON budget_reservations(created_at, status)
            """,

            """
            CREATE INDEX IF NOT EXISTS idx_allocation_budget
            ON budget_reservation_allocations(budget_id)
            """,
            """
CREATE TABLE IF NOT EXISTS throttle_rate_limits (
    user_id VARCHAR(100) NOT NULL,
    project_id VARCHAR(100) NOT NULL,
    service VARCHAR(100) NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (
        user_id,
        project_id,
        service,
        window_start
    ),

    CHECK (request_count >= 0)
)
"""
        ]

        with self.engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))

    
    def _consume_throttle_slot(self, connection, request):
        now = datetime.now(timezone.utc)

        window_number = int(
            now.timestamp() // self.throttle_window_seconds
        )

        window_start = datetime.fromtimestamp(
            window_number * self.throttle_window_seconds,
            tz=timezone.utc,
        )

        result = connection.execute(
            text("""
                INSERT INTO throttle_rate_limits (
                    user_id,
                    project_id,
                    service,
                    window_start,
                    request_count
                )
                VALUES (
                    :user_id,
                    :project_id,
                    :service,
                    :window_start,
                    1
                )
                ON CONFLICT (
                    user_id,
                    project_id,
                    service,
                    window_start
                )
                DO UPDATE SET
                    request_count =
                        throttle_rate_limits.request_count + 1,
                    updated_at = NOW()
                WHERE
                    throttle_rate_limits.request_count < :rate_limit
                RETURNING request_count
            """),
            {
                "user_id": request.user_id,
                "project_id": request.project_id,
                "service": request.service.upper(),
                "window_start": window_start,
                "rate_limit": self.throttle_rate_limit,
            }
        ).first()

        if result is None:
            window_end = window_start + timedelta(
                seconds=self.throttle_window_seconds
            )

            retry_after = math.ceil(
                (window_end - now).total_seconds()
            )

            raise RateLimitExceeded(retry_after)

        return result.request_count


    def set_budget(
        self,
        scope,
        user_id,
        project_id,
        service,
        daily_limit,
        monthly_limit,
        operation_limit,
    ):
        scope = scope.upper()

        if scope not in ("USER", "PROJECT", "SERVICE"):
            raise ValueError("Invalid budget scope")

        if not user_id:
            raise ValueError("user_id is required")

        if scope == "USER":
            project_id = None
            service = None

        elif scope == "PROJECT":
            if not project_id:
                raise ValueError("project_id is required for PROJECT budget")
            service = None

        elif scope == "SERVICE":
            if not project_id or not service:
                raise ValueError(
                    "project_id and service are required for SERVICE budget"
                )

        daily_limit = Decimal(str(daily_limit))
        monthly_limit = Decimal(str(monthly_limit))
        operation_limit = Decimal(str(operation_limit))

        if daily_limit <= 0 or monthly_limit <= 0:
            raise ValueError("Daily and monthly limits must be positive")

        if operation_limit < 0:
            raise ValueError("Operation limit cannot be negative")

        values = {
            "user_id": user_id,
            "project_id": project_id,
            "service": service,
            "daily_limit": daily_limit,
            "monthly_limit": monthly_limit,
            "operation_limit": operation_limit,
        }

        if scope == "USER":
            conflict = "(user_id) WHERE scope = 'USER'"
        elif scope == "PROJECT":
            conflict = (
                "(user_id, project_id) WHERE scope = 'PROJECT'"
            )
        else:
            conflict = (
                "(user_id, project_id, service) WHERE scope = 'SERVICE'"
            )

        query = f"""
            INSERT INTO budgets (
                scope, user_id, project_id, service,
                daily_limit, monthly_limit, operation_limit
            )
            VALUES (
                :scope, :user_id, :project_id, :service,
                :daily_limit, :monthly_limit, :operation_limit
            )
            ON CONFLICT {conflict}
            DO UPDATE SET
                daily_limit = EXCLUDED.daily_limit,
                monthly_limit = EXCLUDED.monthly_limit,
                operation_limit = EXCLUDED.operation_limit,
                updated_at = NOW()
            RETURNING *
        """

        with self.engine.begin() as connection:
            result = connection.execute(
                text(query),
                {"scope": scope, **values}
            ).mappings().one()

            return dict(result)

    def get_budgets(self, user_id, project_id, service):
        with self.engine.connect() as connection:
            rows = connection.execute(
                text("""
                    SELECT *
                    FROM budgets
                    WHERE user_id = :user_id
                    AND (
                        (scope = 'USER')
                        OR (scope = 'PROJECT' AND project_id = :project_id)
                        OR (
                            scope = 'SERVICE'
                            AND project_id = :project_id
                            AND service = :service
                        )
                    )
                    ORDER BY
                        CASE scope
                            WHEN 'USER' THEN 1
                            WHEN 'PROJECT' THEN 2
                            WHEN 'SERVICE' THEN 3
                        END
                """),
                {
                    "user_id": user_id,
                    "project_id": project_id,
                    "service": service.upper(),
                }
            ).mappings().all()

        return [dict(row) for row in rows]

    def _get_usage(
        self,
        connection,
        budget_id,
        day_start,
        month_start,
        exclude_reservation=None,
    ):
        result = connection.execute(
            text("""
                SELECT
                    COALESCE(
                        SUM(
                            CASE
                                WHEN r.created_at >= :day_start
                                THEN a.amount
                                ELSE 0
                            END
                        ), 0
                    ) AS daily_spent,

                    COALESCE(
                        SUM(
                            CASE
                                WHEN r.created_at >= :month_start
                                THEN a.amount
                                ELSE 0
                            END
                        ), 0
                    ) AS monthly_spent

                FROM budget_reservation_allocations a
                JOIN budget_reservations r
                    ON r.id = a.reservation_id

                WHERE a.budget_id = :budget_id
                AND r.status IN ('RESERVED', 'COMMITTED')
                AND r.created_at >= :month_start
                AND (
                    CAST(:exclude_id AS UUID) IS NULL
                    OR r.id != CAST(:exclude_id AS UUID)
                )
            """),
            {
                "budget_id": budget_id,
                "day_start": day_start,
                "month_start": month_start,
                "exclude_id": exclude_reservation,
            }
        ).mappings().one()

        return BudgetSnapshot(
            daily_spent=Decimal(str(result["daily_spent"])),
            monthly_spent=Decimal(str(result["monthly_spent"])),
        )

    def _get_hierarchy(self, connection, request):
        rows = connection.execute(
            text("""
                SELECT *
                FROM budgets
                WHERE user_id = :user_id
                AND (
                    scope = 'USER'
                    OR (
                        scope = 'PROJECT'
                        AND project_id = :project_id
                    )
                    OR (
                        scope = 'SERVICE'
                        AND project_id = :project_id
                        AND service = :service
                    )
                )
                ORDER BY id
            """),
            {
                "user_id": request.user_id,
                "project_id": request.project_id,
                "service": request.service.upper(),
            }
        ).mappings().all()

        budgets = [dict(row) for row in rows]

        scopes = {budget["scope"] for budget in budgets}

        required = {"USER", "PROJECT", "SERVICE"}

        if scopes != required:
            missing = required - scopes
            raise ValueError(
                f"Missing configured budget scopes: {', '.join(sorted(missing))}"
            )

        return budgets

    def authorize(self, request):
        now = datetime.now(timezone.utc)

        day_start = now.replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        month_start = day_start.replace(day=1)

        with self.engine.begin() as connection:

            budgets = self._get_hierarchy(connection, request)

            # Lock all applicable budget rows in a consistent order.
            locked = connection.execute(
                text("""
                    SELECT *
                    FROM budgets
                    WHERE id = ANY(:budget_ids)
                    ORDER BY id
                    FOR UPDATE
                """),
                {"budget_ids": [b["id"] for b in budgets]}
            ).mappings().all()

            if len(locked) != 3:
                raise ValueError("Budget hierarchy changed during authorization")

            decisions = []

            for budget in locked:

                usage = self._get_usage(
                    connection,
                    budget["id"],
                    day_start,
                    month_start,
                )

                limits = BudgetLimits(
                    daily_limit=Decimal(str(budget["daily_limit"])),
                    monthly_limit=Decimal(str(budget["monthly_limit"])),
                    operation_limit=Decimal(str(budget["operation_limit"])),
                )

                result = self.policy_engine.evaluate(
                    request,
                    limits,
                    usage,
                )

                decisions.append({
                    "scope": budget["scope"],
                    "result": result,
                    "budget_id": budget["id"],
                })

            # A hard denial takes precedence over all other decisions.
            denied = [
                item for item in decisions
                if item["result"].decision == PolicyDecision.DENY
            ]

            if denied:
                return {
                    "decision": "DENY",
                    "reason": denied[0]["result"].reason,
                    "scope": denied[0]["scope"],
                    "reservation_id": None,
                }

            escalated = [
                item for item in decisions
                if item["result"].decision == PolicyDecision.ESCALATE
            ]

            if escalated:
                return {
                    "decision": "ESCALATE",
                    "reason": escalated[0]["result"].reason,
                    "scope": escalated[0]["scope"],
                    "reservation_id": None,
                }

            throttled = [
                item for item in decisions
                if item["result"].decision == PolicyDecision.THROTTLE
            ]

            decision = "THROTTLE" if throttled else "ALLOW"

# Consume a rate-limit slot only for THROTTLE decisions.
# This must happen before creating the reservation.
            self._consume_throttle_slot(connection, request)

# ALLOW and accepted THROTTLE requests both reserve the amount.
            reservation_id = uuid4()

            connection.execute(
                text("""
                    INSERT INTO budget_reservations (
                        id, user_id, project_id, service,
                        operation, estimated_cost, status
                    )
                    VALUES (
                        :id, :user_id, :project_id, :service,
                        :operation, :estimated_cost, 'RESERVED'
                    )
                """),
                {
                    "id": reservation_id,
                    "user_id": request.user_id,
                    "project_id": request.project_id,
                    "service": request.service.upper(),
                    "operation": request.operation,
                    "estimated_cost": request.estimated_cost,
                }
            )

            for item in decisions:
                connection.execute(
                    text("""
                        INSERT INTO budget_reservation_allocations (
                            reservation_id, budget_id, amount
                        )
                        VALUES (:reservation_id, :budget_id, :amount)
                    """),
                    {
                        "reservation_id": reservation_id,
                        "budget_id": item["budget_id"],
                        "amount": request.estimated_cost,
                    }
                )

            return {
                "decision": decision,
                "reason": (
                    "Operation reserved successfully"
                    if decision == "ALLOW"
                    else "Operation reserved with budget throttling"
                ),
                "reservation_id": str(reservation_id),
                "estimated_cost": str(request.estimated_cost),
            }

    def commit(self, reservation_id, actual_cost):
        reservation_id = UUID(str(reservation_id))
        actual_cost = Decimal(str(actual_cost))

        if actual_cost < 0:
            raise ValueError("Actual cost cannot be negative")

        now = datetime.now(timezone.utc)

        day_start = now.replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        month_start = day_start.replace(day=1)

        with self.engine.begin() as connection:

            reservation = connection.execute(
                text("""
                    SELECT *
                    FROM budget_reservations
                    WHERE id = :id
                    FOR UPDATE
                """),
                {"id": reservation_id}
            ).mappings().first()

            if not reservation:
                raise ValueError("Reservation not found")

            if reservation["status"] != "RESERVED":
                raise ValueError("Only RESERVED operations can be committed")

            allocations = connection.execute(
                text("""
                    SELECT budget_id
                    FROM budget_reservation_allocations
                    WHERE reservation_id = :id
                    ORDER BY budget_id
                """),
                {"id": reservation_id}
            ).mappings().all()

            budget_ids = [row["budget_id"] for row in allocations]

            budgets = connection.execute(
                text("""
                    SELECT *
                    FROM budgets
                    WHERE id = ANY(:ids)
                    ORDER BY id
                    FOR UPDATE
                """),
                {"ids": budget_ids}
            ).mappings().all()

            request = OperationRequest(
                user_id=reservation["user_id"],
                project_id=reservation["project_id"],
                service=reservation["service"],
                operation=reservation["operation"],
                estimated_cost=actual_cost,
            )

            for budget in budgets:

                usage = self._get_usage(
                    connection,
                    budget["id"],
                    day_start,
                    month_start,
                    exclude_reservation=reservation_id,
                )

                limits = BudgetLimits(
                    daily_limit=Decimal(str(budget["daily_limit"])),
                    monthly_limit=Decimal(str(budget["monthly_limit"])),
                    operation_limit=Decimal(str(budget["operation_limit"])),
                )

                result = self.policy_engine.evaluate(
                    request,
                    limits,
                    usage,
                )

                if result.decision == PolicyDecision.DENY:
                    raise ValueError(
                        f"Actual cost exceeds {budget['scope']} budget: "
                        f"{result.reason}"
                    )

            # Replace estimated allocations with actual cost.
            connection.execute(
                text("""
                    UPDATE budget_reservation_allocations
                    SET amount = :amount
                    WHERE reservation_id = :id
                """),
                {
                    "amount": actual_cost,
                    "id": reservation_id,
                }
            )

            connection.execute(
                text("""
                    UPDATE budget_reservations
                    SET estimated_cost = :amount,
                        status = 'COMMITTED',
                        updated_at = NOW()
                    WHERE id = :id
                """),
                {
                    "amount": actual_cost,
                    "id": reservation_id,
                }
            )

        return {
            "reservation_id": str(reservation_id),
            "status": "COMMITTED",
            "actual_cost": str(actual_cost),
        }

    def release(self, reservation_id):
        reservation_id = UUID(str(reservation_id))

        with self.engine.begin() as connection:

            result = connection.execute(
                text("""
                    UPDATE budget_reservations
                    SET status = 'RELEASED',
                        updated_at = NOW()
                    WHERE id = :id
                    AND status = 'RESERVED'
                    RETURNING id
                """),
                {"id": reservation_id}
            ).first()

            if not result:
                raise ValueError(
                    "Reservation not found or is not in RESERVED state"
                )

        return {
            "reservation_id": str(reservation_id),
            "status": "RELEASED",
        }
