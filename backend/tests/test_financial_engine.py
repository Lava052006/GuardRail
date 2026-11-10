from decimal import Decimal

from financial.models import (
    BudgetLimits,
    BudgetSnapshot,
    OperationRequest,
    PolicyDecision,
)

from financial.cost_estimator import CostEstimator
from financial.policy_engine import PolicyEngine

def create_request(cost="0.05", approval=False):
    return OperationRequest(
        user_id="user-1",
        project_id="project-1",
        service="EC2",
        operation="RunInstances",
        estimated_cost=Decimal(cost),
        requires_approval=approval,
    )


def create_limits():
    return BudgetLimits(
        daily_limit=Decimal("10"),
        monthly_limit=Decimal("100"),
        operation_limit=Decimal("5"),
    )


def create_usage(daily="2", monthly="20"):
    return BudgetSnapshot(
        daily_spent=Decimal(daily),
        monthly_spent=Decimal(monthly),
    )


def test_cost_estimation_ec2():
    cost = CostEstimator.estimate_ec2(
        "t2.micro", Decimal("1")
    )
    assert cost == Decimal("0.011600")


def test_cost_estimation_s3():
    cost = CostEstimator.estimate_s3_request("PUT", 1000)
    assert cost == Decimal("0.005000")


def test_policy_allows_operation():
    result = PolicyEngine().evaluate(
        create_request(),
        create_limits(),
        create_usage(),
    )
    assert result.decision == PolicyDecision.ALLOW


def test_policy_denies_operation_limit():
    result = PolicyEngine().evaluate(
        create_request("6"),
        create_limits(),
        create_usage(),
    )
    assert result.decision == PolicyDecision.DENY


def test_policy_denies_daily_budget():
    result = PolicyEngine().evaluate(
        create_request("1"),
        create_limits(),
        create_usage(daily="9.5"),
    )
    assert result.decision == PolicyDecision.DENY


def test_policy_denies_monthly_budget():
    result = PolicyEngine().evaluate(
        create_request("1"),
        create_limits(),
        create_usage(monthly="99.5"),
    )
    assert result.decision == PolicyDecision.DENY


def test_policy_escalates_for_approval():
    result = PolicyEngine().evaluate(
        create_request(approval=True),
        create_limits(),
        create_usage(),
    )
    assert result.decision == PolicyDecision.ESCALATE


def test_policy_throttles_at_threshold():
    result = PolicyEngine().evaluate(
        create_request("0.5"),
        create_limits(),
        create_usage(daily="7.6"),
    )
    assert result.decision == PolicyDecision.THROTTLE

import pytest


def test_policy_throttles_at_monthly_threshold():
    result = PolicyEngine().evaluate(
        create_request("1"),
        create_limits(),
        create_usage(monthly="79"),
    )
    assert result.decision == PolicyDecision.THROTTLE


def test_policy_allows_cost_equal_to_operation_limit():
    result = PolicyEngine().evaluate(
        create_request("5"),
        create_limits(),
        create_usage(),
    )
    assert result.decision == PolicyDecision.ALLOW


def test_policy_throttles_when_daily_budget_reaches_exact_limit():
    result = PolicyEngine().evaluate(
        create_request("1"),
        create_limits(),
        create_usage(daily="9"),
    )
    assert result.decision == PolicyDecision.THROTTLE


def test_policy_prioritizes_deny_over_approval():
    result = PolicyEngine().evaluate(
        create_request("6", approval=True),
        create_limits(),
        create_usage(),
    )
    assert result.decision == PolicyDecision.DENY


def test_policy_rejects_negative_estimated_cost():
    with pytest.raises(ValueError, match="Estimated cost cannot be negative"):
        PolicyEngine().evaluate(
            create_request("-1"),
            create_limits(),
            create_usage(),
        )


def test_policy_rejects_negative_daily_usage():
    with pytest.raises(ValueError, match="Existing spending cannot be negative"):
        PolicyEngine().evaluate(
            create_request(),
            create_limits(),
            create_usage(daily="-1"),
        )


def test_policy_rejects_zero_daily_limit():
    limits = BudgetLimits(
        daily_limit=Decimal("0"),
        monthly_limit=Decimal("100"),
        operation_limit=Decimal("5"),
    )

    with pytest.raises(ValueError, match="Daily and monthly limits must be positive"):
        PolicyEngine().evaluate(
            create_request(),
            limits,
            create_usage(),
        )


def test_policy_rejects_negative_operation_limit():
    limits = BudgetLimits(
        daily_limit=Decimal("10"),
        monthly_limit=Decimal("100"),
        operation_limit=Decimal("-1"),
    )

    with pytest.raises(ValueError, match="Operation limit cannot be negative"):
        PolicyEngine().evaluate(
            create_request(),
            limits,
            create_usage(),
        )


def test_policy_rejects_invalid_throttle_threshold():
    with pytest.raises(ValueError, match="Throttle threshold must be between 0 and 1"):
        PolicyEngine(throttle_threshold=Decimal("1"))