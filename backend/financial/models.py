from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional


class BudgetScope(str, Enum):
    USER = "USER"
    PROJECT = "PROJECT"
    SERVICE = "SERVICE"


class PolicyDecision(str, Enum):
    ALLOW = "ALLOW"
    THROTTLE = "THROTTLE"
    ESCALATE = "ESCALATE"
    DENY = "DENY"


@dataclass
class BudgetLimits:
    daily_limit: Decimal
    monthly_limit: Decimal
    operation_limit: Decimal


@dataclass
class BudgetSnapshot:
    daily_spent: Decimal
    monthly_spent: Decimal


@dataclass
class OperationRequest:
    user_id: str
    project_id: str
    service: str
    operation: str
    estimated_cost: Decimal
    requires_approval: bool = False


@dataclass
class PolicyResult:
    decision: PolicyDecision
    reason: str
    estimated_cost: Decimal
    scope: Optional[str] = None
