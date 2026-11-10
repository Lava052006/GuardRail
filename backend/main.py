from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os
from pydantic import BaseModel, Field, ConfigDict

try:
    from .financial.models import OperationRequest
    from .financial.budget_manager import BudgetManager, RateLimitExceeded
    from .financial.cost_estimator import CostEstimator
except ImportError:
    from financial.models import OperationRequest
    from financial.budget_manager import BudgetManager, RateLimitExceeded
    from financial.cost_estimator import CostEstimator

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not configured")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

budget_manager = BudgetManager(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    budget_manager.initialize()
    yield


app = FastAPI(
    title="GuardRail API",
    description="Agentic Spend Firewall Backend",
    version="2.0.0",
    lifespan=lifespan,
)


class BudgetRequest(BaseModel):
    scope: str
    user_id: str = Field(min_length=1, max_length=100)
    project_id: Optional[str] = Field(default=None, max_length=100)
    service: Optional[str] = Field(default=None, max_length=100)

    daily_limit: Decimal = Field(gt=0)
    monthly_limit: Decimal = Field(gt=0)
    operation_limit: Decimal = Field(ge=0)


class AuthorizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=100)
    project_id: str = Field(min_length=1, max_length=100)
    service: str = Field(min_length=1, max_length=100)
    operation: str = Field(min_length=1, max_length=150)

    # EC2 inputs
    instance_type: Optional[str] = None
    hours: Optional[Decimal] = Field(default=None, gt=0)

    # S3 request inputs
    requests: Optional[int] = Field(default=None, ge=0)

    # S3 storage inputs
    size_gb: Optional[Decimal] = Field(default=None, ge=0)
    months: Optional[Decimal] = Field(default=None, ge=0)

    requires_approval: bool = False

class CommitRequest(BaseModel):
    actual_cost: Decimal = Field(ge=0)


@app.get("/")
def home():
    return {"message": "GuardRail API is running"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/db-check")
def database_check():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return {
            "database": "connected",
            "status": "healthy",
        }

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database connection failed: {str(exc)}",
        )


@app.get("/budgets")
def get_budgets(
    user_id: str,
    project_id: str,
    service: str,
):
    try:
        budgets = budget_manager.get_budgets(
            user_id,
            project_id,
            service,
        )

        return {
            "budgets": budgets,
            "configured_scopes": [
                budget["scope"] for budget in budgets
            ],
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.put("/budgets")
def create_or_update_budget(payload: BudgetRequest):
    try:
        budget = budget_manager.set_budget(
            scope=payload.scope,
            user_id=payload.user_id,
            project_id=payload.project_id,
            service=payload.service,
            daily_limit=payload.daily_limit,
            monthly_limit=payload.monthly_limit,
            operation_limit=payload.operation_limit,
        )

        return {
            "message": "Budget saved successfully",
            "budget": budget,
        }

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

def calculate_operation_cost(payload: AuthorizeRequest) -> Decimal:
    service = payload.service.upper()
    operation = payload.operation.upper()

    if service == "EC2":
        if not payload.instance_type or payload.hours is None:
            raise ValueError(
                "EC2 requires instance_type and hours"
            )

        return CostEstimator.estimate_ec2(
            payload.instance_type,
            payload.hours,
        )

    elif service == "S3":
        if operation in {"PUT", "GET", "DELETE"}:
            if payload.requests is None:
                raise ValueError(
                    "S3 request operations require requests"
                )

            return CostEstimator.estimate_s3_request(
                operation,
                payload.requests,
            )

        elif operation == "STORAGE":
            if payload.size_gb is None or payload.months is None:
                raise ValueError(
                    "S3 STORAGE requires size_gb and months"
                )

            return CostEstimator.estimate_s3_storage(
                payload.size_gb,
                payload.months,
            )

        else:
            raise ValueError(
                f"Unsupported S3 operation: {operation}"
            )

    else:
        raise ValueError(
            f"Unsupported service: {service}"
        )


@app.post("/authorize")
def authorize_operation(payload: AuthorizeRequest):
    try:
        try:
            estimated_cost = calculate_operation_cost(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        request = OperationRequest(
            user_id=payload.user_id,
            project_id=payload.project_id,
            service=payload.service.upper(),
            operation=payload.operation,
            estimated_cost=estimated_cost,
            requires_approval=payload.requires_approval,
        )

        
        result = budget_manager.authorize(request)

        return result

    except HTTPException:
            raise

    except RateLimitExceeded as exc:
       raise HTTPException(
        status_code=429,
        detail=str(exc),
        headers={
            "Retry-After": str(exc.retry_after)
        },
    )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

            

   

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/reservations/{reservation_id}/commit")
def commit_reservation(
    reservation_id: str,
    payload: CommitRequest,
):
    try:
        return budget_manager.commit(
            reservation_id,
            payload.actual_cost,
        )

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/reservations/{reservation_id}/release")
def release_reservation(reservation_id: str):
    try:
        return budget_manager.release(reservation_id)

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
