from decimal import Decimal

from .models import (
    BudgetLimits,
    BudgetSnapshot,
    OperationRequest,
    PolicyDecision,
    PolicyResult,
)


class PolicyEngine:

    def __init__(self, throttle_threshold=Decimal("0.80")):
        if not Decimal("0") < throttle_threshold < Decimal("1"):
            raise ValueError("Throttle threshold must be between 0 and 1")

        self.throttle_threshold = throttle_threshold

    def evaluate(
        self,
        request: OperationRequest,
        limits: BudgetLimits,
        usage: BudgetSnapshot,
    ) -> PolicyResult:

        cost = request.estimated_cost

        if cost < 0:
            raise ValueError("Estimated cost cannot be negative")

        if usage.daily_spent < 0 or usage.monthly_spent < 0:
            raise ValueError("Existing spending cannot be negative")

        if limits.daily_limit <= 0 or limits.monthly_limit <= 0:
            raise ValueError("Daily and monthly limits must be positive")

        if limits.operation_limit < 0:
            raise ValueError("Operation limit cannot be negative")

        # 1. Per-operation hard limit
        if cost > limits.operation_limit:
            return PolicyResult(
                PolicyDecision.DENY,
                "Operation cost exceeds the per-operation limit",
                cost,
            )

        # 2. Daily hard limit
        if usage.daily_spent + cost > limits.daily_limit:
            return PolicyResult(
                PolicyDecision.DENY,
                "Operation would exceed the daily budget",
                cost,
            )

        # 3. Monthly hard limit
        if usage.monthly_spent + cost > limits.monthly_limit:
            return PolicyResult(
                PolicyDecision.DENY,
                "Operation would exceed the monthly budget",
                cost,
            )

        # 4. Human approval
        if request.requires_approval:
            return PolicyResult(
                PolicyDecision.ESCALATE,
                "Human approval is required",
                cost,
            )

        # 5. Throttle when spending reaches configured threshold
        daily_ratio = (
            usage.daily_spent + cost
        ) / limits.daily_limit

        monthly_ratio = (
            usage.monthly_spent + cost
        ) / limits.monthly_limit

        if (
            daily_ratio >= self.throttle_threshold
            or monthly_ratio >= self.throttle_threshold
        ):
            return PolicyResult(
                PolicyDecision.THROTTLE,
                "Budget usage reached the configured threshold",
                cost,
            )

        # 6. All checks passed
        return PolicyResult(
            PolicyDecision.ALLOW,
            "Operation is within all financial limits",
            cost,
        )
