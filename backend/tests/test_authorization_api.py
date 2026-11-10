
import pytest
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from main import app, engine


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def budget_case(client):
    """Create isolated USER, PROJECT and SERVICE budgets."""

    user_id = f"authorization-test-{uuid4()}"
    project_id = "test-project"
    service = "S3"

    with engine.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO budgets (
                    scope, user_id, daily_limit,
                    monthly_limit, operation_limit
                )
                VALUES ('USER', :user_id, 100, 1000, 50)
            """),
            {"user_id": user_id}
        )

        connection.execute(
            text("""
                INSERT INTO budgets (
                    scope, user_id, project_id,
                    daily_limit, monthly_limit, operation_limit
                )
                VALUES ('PROJECT', :user_id, :project_id, 100, 1000, 50)
            """),
            {
                "user_id": user_id,
                "project_id": project_id
            }
        )

        connection.execute(
            text("""
                INSERT INTO budgets (
                    scope, user_id, project_id, service,
                    daily_limit, monthly_limit, operation_limit
                )
                VALUES ('SERVICE', :user_id, :project_id, :service,
                        100, 1000, 50)
            """),
            {
                "user_id": user_id,
                "project_id": project_id,
                "service": service
            }
        )

    yield {
        "user_id": user_id,
        "project_id": project_id,
        "service": service
    }

    # Clean up only records belonging to this test.
    with engine.begin() as connection:
        connection.execute(
            text("""
                DELETE FROM budget_reservation_allocations
                WHERE reservation_id IN (
                    SELECT id FROM budget_reservations
                    WHERE user_id = :user_id
                )
            """),
            {"user_id": user_id}
        )

        connection.execute(
            text("""
                DELETE FROM budget_reservations
                WHERE user_id = :user_id
            """),
            {"user_id": user_id}
        )

        connection.execute(
            text("""
                DELETE FROM budgets
                WHERE user_id = :user_id
            """),
            {"user_id": user_id}
        )


def update_budget_limits(user_id, **limits):
    """Apply the same test limits to all three budget levels."""

    allowed_columns = {
        "daily_limit",
        "monthly_limit",
        "operation_limit"
    }

    if not set(limits).issubset(allowed_columns):
        raise ValueError("Invalid budget limit field")

    assignments = ", ".join(
        f"{column} = :{column}" for column in limits
    )

    with engine.begin() as connection:
        connection.execute(
            text(f"""
                UPDATE budgets
                SET {assignments}
                WHERE user_id = :user_id
            """),
            {**limits, "user_id": user_id}
        )


def get_reservation_count(user_id):
    with engine.connect() as connection:
        return connection.execute(
            text("""
                SELECT COUNT(*)
                FROM budget_reservations
                WHERE user_id = :user_id
            """),
            {"user_id": user_id}
        ).scalar_one()


def authorize(client, budget_case, requires_approval=False):
    return client.post(
        "/authorize",
        json={
            "user_id": budget_case["user_id"],
            "project_id": budget_case["project_id"],
            "service": "S3",
            "operation": "PUT",
            "requests": 1000,
            "requires_approval": requires_approval
        }
    )


def test_authorize_allow_creates_reservation(client, budget_case):
    response = authorize(client, budget_case)

    assert response.status_code == 200

    data = response.json()

    assert data["decision"] == "ALLOW"
    assert data["reservation_id"] is not None
    assert data["estimated_cost"] == "0.005000"

    assert get_reservation_count(budget_case["user_id"]) == 1


@pytest.mark.parametrize(
    "limit_field",
    ["daily_limit", "monthly_limit"]
)
def test_authorize_throttle_creates_reservation(
    client, budget_case, limit_field
):
    # $0.005 operation reaches exactly 80% of a $0.00625 limit.
    update_budget_limits(
        budget_case["user_id"],
        **{limit_field: "0.00625"}
    )

    response = authorize(client, budget_case)

    assert response.status_code == 200

    data = response.json()

    assert data["decision"] == "THROTTLE"
    assert data["reservation_id"] is not None

    assert get_reservation_count(budget_case["user_id"]) == 1


def test_authorize_escalate_does_not_create_reservation(
    client, budget_case
):
    response = authorize(
        client,
        budget_case,
        requires_approval=True
    )

    assert response.status_code == 200

    data = response.json()

    assert data["decision"] == "ESCALATE"
    assert data["reservation_id"] is None

    assert get_reservation_count(budget_case["user_id"]) == 0


@pytest.mark.parametrize(
    ("limit_field", "limit_value"),
    [
        ("operation_limit", "0.001"),
        ("daily_limit", "0.001"),
        ("monthly_limit", "0.001")
    ]
)
def test_authorize_deny_does_not_create_reservation(
    client, budget_case, limit_field, limit_value
):
    update_budget_limits(
        budget_case["user_id"],
        **{limit_field: limit_value}
    )

    response = authorize(client, budget_case)

    assert response.status_code == 200

    data = response.json()

    assert data["decision"] == "DENY"
    assert data["reservation_id"] is None

    assert get_reservation_count(budget_case["user_id"]) == 0
