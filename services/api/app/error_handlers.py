from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.providers.base import LLMProviderError
from app.providers.bedrock import BedrockResponseError
from services.worker.app.aws_jobs import DurableJobStoreError
from services.worker.app.jobs import JobCapacityError, JobNotFoundError


def _error_response(
    request: Request,
    exc: Exception,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request.state.error_type = type(exc).__name__
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )


async def handle_provider_response_error(
    request: Request,
    exc: BedrockResponseError,
) -> JSONResponse:
    return _error_response(
        request=request,
        exc=exc,
        status_code=502,
        code="invalid_model_response",
        message="The model service returned an invalid response.",
    )


async def handle_provider_error(
    request: Request,
    exc: LLMProviderError,
) -> JSONResponse:
    return _error_response(
        request=request,
        exc=exc,
        status_code=503,
        code="llm_provider_unavailable",
        message="The model service is temporarily unavailable.",
        headers={"Retry-After": "5"},
    )


async def handle_job_not_found(
    request: Request,
    exc: JobNotFoundError,
) -> JSONResponse:
    return _error_response(
        request=request,
        exc=exc,
        status_code=404,
        code="job_not_found",
        message="The requested job does not exist.",
    )


async def handle_job_capacity(
    request: Request,
    exc: JobCapacityError,
) -> JSONResponse:
    return _error_response(
        request=request,
        exc=exc,
        status_code=503,
        code="job_capacity_exceeded",
        message="The job service is temporarily at capacity.",
        headers={"Retry-After": "1"},
    )


async def handle_job_store_error(
    request: Request,
    exc: DurableJobStoreError,
) -> JSONResponse:
    return _error_response(
        request=request,
        exc=exc,
        status_code=503,
        code="job_service_unavailable",
        message="The durable job service is temporarily unavailable.",
        headers={"Retry-After": "5"},
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(BedrockResponseError, handle_provider_response_error)
    app.add_exception_handler(LLMProviderError, handle_provider_error)
    app.add_exception_handler(JobNotFoundError, handle_job_not_found)
    app.add_exception_handler(JobCapacityError, handle_job_capacity)
    app.add_exception_handler(DurableJobStoreError, handle_job_store_error)
