from datetime import datetime

from pydantic import BaseModel, Field

from services.worker.app.jobs import JobRecord, JobStatus


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)


class GenerateResponse(BaseModel):
    output: str
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None


class CreateJobRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    output: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None
    error_code: str | None = None

    @classmethod
    def from_record(cls, record: JobRecord) -> "JobResponse":
        return cls(
            job_id=record.job_id,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
            output=record.output,
            model=record.model_id,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            estimated_cost=record.estimated_cost,
            error_code=record.error_code,
        )


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None


class ErrorResponse(BaseModel):
    error: ErrorDetail
