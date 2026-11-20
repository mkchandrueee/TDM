from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from tigr.config import get_settings
from tigr.connections import router as connections_router
from tigr.contracts import ErrorResponse, HealthStatus
from tigr.dashboard import router as dashboard_router
from tigr.dataset_api import router as datasets_router
from tigr.db import engine
from tigr.development_auth import router as development_auth_router
from tigr.delivery_api import router as delivery_router
from tigr.jobs_api import router as jobs_router
from tigr.middleware import CorrelationIdMiddleware
from tigr.platform_access import router as access_router
from tigr.privacy_api import router as privacy_router
from tigr.reservations_api import router as reservations_router
from tigr.scenario_api import router as scenario_router
from tigr.source_to_data import router as source_to_data_router

settings = get_settings()
app = FastAPI(title="TIGR API", version="0.1.0", description="Test data generation and management")
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID", "X-Dev-Subject"],
)
app.include_router(access_router)
app.include_router(development_auth_router)
app.include_router(dashboard_router)
app.include_router(connections_router)
app.include_router(datasets_router)
app.include_router(delivery_router)
app.include_router(jobs_router)
app.include_router(scenario_router)
app.include_router(source_to_data_router)
app.include_router(privacy_router)
app.include_router(reservations_router)


def error_body(request: Request, code: str, message: str, retryable: bool = False) -> dict:
    return ErrorResponse(code=code, message=message, request_id=request.state.request_id, retryable=retryable).model_dump()


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]} for e in exc.errors()]
    body = ErrorResponse(code="validation_error", message="The request is invalid", field_details=details, request_id=request.state.request_id)
    return JSONResponse(status_code=422, content=body.model_dump())


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    code = "not_found" if exc.status_code == 404 else "request_error"
    message = (
        "The requested resource was not found" if exc.status_code == 404
        else "Authentication is required" if exc.status_code == 401
        else str(exc.detail) if exc.status_code in (403, 409) and isinstance(exc.detail, str)
        else str(exc.detail) if exc.status_code in (422, 503) and isinstance(exc.detail, str)
        else "The request could not be completed"
    )
    return JSONResponse(status_code=exc.status_code, content=error_body(request, code, message))


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content=error_body(request, "internal_error", "An unexpected error occurred"))


@app.get(f"{settings.api_prefix}/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get(f"{settings.api_prefix}/health/ready", response_model=HealthStatus, tags=["health"])
def ready(request: Request) -> JSONResponse | HealthStatus:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content=error_body(request, "not_ready", "A required service is unavailable", retryable=True))
    return HealthStatus(status="ready", database="ready", graph="not_configured", environment=settings.env)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"name": "TIGR API", "docs": f"{settings.api_prefix}/docs"}
