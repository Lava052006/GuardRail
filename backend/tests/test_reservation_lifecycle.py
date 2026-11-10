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
def reservation(client):
    """Create isolated test records and remove them after the test."""

    user_id = f"guardrail-test-{uuid4()}"
    project_id = "test-project"
    service = "AWS"

    with engine.begin() as connection:
        budget_id = connection.execute(
            text("""
                INSERT INTO budgets (
                    scope, user_id, daily_limit,
                    monthly_limit, operation_limit
                )
                VALUES ('USER', :user_id, 100, 1000, 50)
                RETURNING id
            """),
            {"user_id": user_id}
        ).scalar_one()

        reservation_id = uuid4()

        connection.execute(
            text("""
                INSERT INTO budget_reservations (
                    id, user_id, project_id, service,
                    operation, estimated_cost, status
                )
                VALUES (
                    :id, :user_id, :project_id, :service,
                    'EC2_RUN', 0.75, 'RESERVED'
                )
            """),
            {
                "id": reservation_id,
                "user_id": user_id,
                "project_id": project_id,
                "service": service
            }
        )

        connection.execute(
            text("""
                INSERT INTO budget_reservation_allocations (
                    reservation_id, budget_id, amount
                )
                VALUES (:reservation_id, :budget_id, 0.75)
            """),
            {
                "reservation_id": reservation_id,
                "budget_id": budget_id
            }
        )

    yield str(reservation_id), budget_id

    # Clean up only records created by this test.
    with engine.begin() as connection:
        connection.execute(
            text("""
                DELETE FROM budget_reservation_allocations
                WHERE reservation_id = :id
            """),
            {"id": reservation_id}
        )
        connection.execute(
            text("""
                DELETE FROM budget_reservations
                WHERE id = :id
            """),
            {"id": reservation_id}
        )
        connection.execute(
            text("DELETE FROM budgets WHERE id = :id"),
            {"id": budget_id}
        )


def get_active_usage(budget_id):
    with engine.connect() as connection:
        return connection.execute(
            text("""
                SELECT COALESCE(SUM(a.amount), 0)
                FROM budget_reservation_allocations a
                JOIN budget_reservations r
                    ON r.id = a.reservation_id
                WHERE a.budget_id = :budget_id
                AND r.status IN ('RESERVED', 'COMMITTED')
            """),
            {"budget_id": budget_id}
        ).scalar_one()


def test_release_reservation(client, reservation):
    reservation_id, budget_id = reservation

    assert float(get_active_usage(budget_id)) == 0.75

    response = client.post(
        f"/reservations/{reservation_id}/release"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "RELEASED"

    assert float(get_active_usage(budget_id)) == 0.0


def test_cannot_release_same_reservation_twice(client, reservation):
    reservation_id, _ = reservation

    first_response = client.post(
        f"/reservations/{reservation_id}/release"
    )
    second_response = client.post(
        f"/reservations/{reservation_id}/release"
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 409


def test_cannot_release_committed_reservation(client, reservation):
    reservation_id, _ = reservation

    with engine.begin() as connection:
        connection.execute(
            text("""
                UPDATE budget_reservations
                SET status = 'COMMITTED'
                WHERE id = :id
            """),
            {"id": reservation_id}
        )

    response = client.post(
        f"/reservations/{reservation_id}/release"
    )

    assert response.status_code == 409


def test_release_nonexistent_reservation(client):
    response = client.post(
        f"/reservations/{uuid4()}/release"
    )

    assert response.status_code == 409