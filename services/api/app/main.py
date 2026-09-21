import time
from typing import Annotated

from fastapi import Depends, FastAPI, Request, status
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.error_handlers import register_error_handlers
from app.job_service import JobService, get_job_service
from app.logging import configure_logging
from app.metrics import Metrics, MetricsSnapshot, get_metrics
from app.middleware import request_context_middleware
from app.providers.base import LLMProvider
from app.providers.factory import get_provider
from app.schemas import (
    CreateJobRequest,
    ErrorResponse,
    GenerateRequest,
    GenerateResponse,
    JobResponse,
)
from services.common.observability.emf import (
    configure_emf_logging,
    emit_inference_metrics,
)
from services.common.observability.tracing import (
    configure_tracing,
    inference_span,
    mark_current_span_error,
)

settings = get_settings()
configure_logging(settings.log_level)
configure_emf_logging()
app = FastAPI(title="LLMOps Inference API", version="0.1.0")
app.middleware("http")(request_context_middleware)
tracer_provider = configure_tracing(
    service_name="llmops-api",
    environment=settings.app_env,
    endpoint=settings.otel_exporter_otlp_endpoint,
    sample_ratio=settings.otel_trace_sample_ratio,
)
if tracer_provider is not None:
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        excluded_urls=".*/health,.*/ready",
    )
register_error_handlers(app)


@app.get("/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}


@app.get("/ready")
def ready(
    job_service: Annotated[JobService, Depends(get_job_service)],
) -> dict[str, str]:
    job_service.check_ready()
    return {"status": "ready"}


@app.get("/metrics", dependencies=[Depends(require_api_key)])
def metrics_snapshot(
    metrics: Annotated[Metrics, Depends(get_metrics)],
) -> MetricsSnapshot:
    return metrics.snapshot()


@app.post(
    "/v1/generate",
    dependencies=[Depends(require_api_key)],
    response_model=GenerateResponse,
    responses={
        502: {
            "model": ErrorResponse,
            "description": "The model returned an invalid response.",
        },
        503: {
            "model": ErrorResponse,
            "description": "The model provider is temporarily unavailable.",
        },
    },
)
def generate(
    payload: GenerateRequest,
    request: Request,
    provider: Annotated[LLMProvider, Depends(get_provider)],
    metrics: Annotated[Metrics, Depends(get_metrics)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GenerateResponse:
    request.state.model_id = provider.model_id
    started = time.perf_counter()
    result = None
    model_error = False
    provider_error: Exception | None = None
    try:
        with inference_span(provider.model_id):
            try:
                result = provider.generate(payload.prompt)
            except Exception as error:  # noqa: BLE001 - telemetry redaction boundary
                mark_current_span_error(error)
                provider_error = error
        if provider_error is not None:
            raise provider_error
    except Exception:
        model_error = True
        metrics.record_model_error()
        raise
    finally:
        llm_latency_ms = (time.perf_counter() - started) * 1000
        metrics.record_llm_latency(llm_latency_ms)
        emit_inference_metrics(
            service="api",
            environment=settings.app_env,
            model=provider.model_id,
            latency_ms=llm_latency_ms,
            model_error=model_error,
            input_tokens=result.input_tokens if result else None,
            output_tokens=result.output_tokens if result else None,
            estimated_cost_usd=result.estimated_cost if result else None,
        )

    if result.input_tokens is not None and result.output_tokens is not None:
        metrics.record_token_usage(result.input_tokens, result.output_tokens)
    if result.estimated_cost is not None:
        metrics.record_estimated_cost(result.estimated_cost)

    return GenerateResponse(
        output=result.output,
        model=result.model_id,
        latency_ms=round(llm_latency_ms),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost=result.estimated_cost,
    )


@app.post(
    "/v1/jobs",
    dependencies=[Depends(require_api_key)],
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        503: {
            "model": ErrorResponse,
            "description": "The bounded local job executor is at capacity.",
        }
    },
)
def create_job(
    payload: CreateJobRequest,
    request: Request,
    provider: Annotated[LLMProvider, Depends(get_provider)],
    job_service: Annotated[JobService, Depends(get_job_service)],
) -> JobResponse:
    request.state.model_id = provider.model_id
    return JobResponse.from_record(job_service.submit(payload.prompt, provider))


@app.get(
    "/v1/jobs/{job_id}",
    dependencies=[Depends(require_api_key)],
    response_model=JobResponse,
    responses={404: {"model": ErrorResponse, "description": "The job does not exist."}},
)
def get_job(
    job_id: str,
    job_service: Annotated[JobService, Depends(get_job_service)],
) -> JobResponse:
    return JobResponse.from_record(job_service.get(job_id))
