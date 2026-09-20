# Reliable LLM Job Platform on AWS

An asynchronous LLM platform for requests that may take time or fail. The API returns a job ID immediately; the AWS mode persists job state and skips redelivered jobs already marked complete.

## Product at a glance

| | |
| --- | --- |
| **Users** | Application teams that need to submit and track long-running LLM work |
| **Problem** | A synchronous model call is not enough when jobs must survive retries, deployments, and provider failures |
| **Core experience** | Submit a job, receive an ID immediately, and check its durable status later |
| **Local mode** | Deterministic provider and in-memory jobs for development and tests; jobs do not survive a restart |
| **AWS mode** | Durable job state in DynamoDB, work distribution through SQS, and Bedrock inference on ECS Fargate |

The repository shows the engineering around the model call: durable processing, repeatable evaluation, infrastructure as code, deployment gates, observability, privacy boundaries, and rollback paths.

The project runs with a deterministic local provider by default, so its behaviour can be tested without AWS credentials or model calls.

## What a client does

1. Submit a prompt to `POST /v1/jobs` and receive HTTP 202 with a `job_id`.
2. Poll `GET /v1/jobs/{job_id}` for `pending`, `running`, `succeeded`, or `failed` status.
3. Read the output or a bounded error code from the final job record.

The same API works in both modes. Local in-memory jobs are a demonstration path; SQS and DynamoDB provide the durable path when AWS mode is configured.

## Architecture

```text
client -> WAF / ALB -> FastAPI -> DynamoDB
                         |
                         +-> SQS -> worker -> Amazon Bedrock

GitHub Actions -> ECR -> ECS Fargate
Terraform      -> networking, IAM, data, monitoring, and deployment resources
```

## Engineering decisions

- Jobs use conditional DynamoDB updates so duplicate SQS deliveries are idempotent.
- Retryable failures stay on the queue; exhausted messages move to a dead-letter queue.
- Prompts travel in the queue payload but are excluded from logs, traces, and stored job records.
- API and worker images run as non-root users with read-only root filesystems.
- GitHub Actions uses AWS OIDC instead of long-lived access keys.
- Staging and production promote the same scanned image digest instead of rebuilding it.
- CloudWatch metrics, OpenTelemetry traces, request IDs, and structured errors make failures traceable across the API and worker.

## Run locally

```bash
cp .env.example .env
docker compose up --build
```

Check the service at `http://localhost:8000/health`. With `LLM_PROVIDER=local`, generation is deterministic and does not call an external model.

Submit an asynchronous job:

```bash
curl -X POST http://localhost:8000/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"prompt":"hello"}'
```

Use the returned job ID with `GET /v1/jobs/{job_id}`.

## Tests and evaluation

```bash
python -m pytest services/api/tests services/worker/tests evals loadtests
python -m evals.run_eval
```

The test suite covers API behaviour, authentication, provider selection, durable job transitions, duplicate delivery, retry handling, telemetry, and deployment-policy checks.

## Evidence behind project claims

The [FastAPI endpoints](services/api/app/main.py) and
[job service](services/api/app/job_service.py) implement submission and status
retrieval. The [SQS/DynamoDB adapters](services/worker/app/aws_jobs.py) and
[worker](services/worker/app/worker.py) implement the AWS-mode processing path;
[local tests](services/worker/tests/test_durable_worker.py) check that success is
saved before acknowledgement, retries are left unacknowledged, and a duplicate
completed job does not invoke the model twice.

[Trace propagation](services/common/observability/tracing.py) connects the API
request to worker processing without attaching prompts to spans. The
[Terraform environments](terraform/environments/) and
[deployment workflows](.github/workflows/) define AWS infrastructure and
release gates. These files document an implementation, not a measured claim of
live AWS uptime or production traffic.

## AWS mode

Set `LLM_PROVIDER=bedrock` and choose a model with `BEDROCK_MODEL_ID`. In ECS, the task role supplies credentials. Setting `JOB_BACKEND=aws` moves job state to DynamoDB and work distribution to SQS.

Terraform is split into reusable modules and `dev`, `staging`, and `production` environments:

```bash
terraform -chdir=terraform/environments/dev init
terraform -chdir=terraform/environments/dev validate
terraform -chdir=terraform/environments/dev plan
```

See [`terraform/README.md`](terraform/README.md) for state, cost, and environment details.

## Delivery flow

1. Pull requests run formatting, tests, evaluation, and dependency checks.
2. After successful `master` CI, the configured development workflow builds immutable API and worker images and deploys them; it is skipped without the AWS deployment role variable.
3. Staging promotion reuses those image digests and runs integration and performance checks.
4. Production uses a protected environment, an error-budget gate, and blue/green deployment.
5. A failed rollout restores the previous task definitions.

Operational procedures live in [`docs/runbooks`](docs/runbooks/README.md). They cover queue backlog, model degradation, deployment regression, infrastructure drift, backup recovery, and supply-chain verification.

## Repository layout

| Path | Purpose |
| --- | --- |
| `services/api` | FastAPI endpoints and job submission |
| `services/worker` | SQS worker and durable job processing |
| `services/common` | Shared LLM and observability code |
| `evals` | Deterministic local and remote evaluation |
| `loadtests` | Bounded staging load generator |
| `terraform` | AWS modules and environment stacks |
| `.github/workflows` | CI, deployment, monitoring, and drift checks |

This repository is a portfolio implementation, not a claim that the included defaults fit every production workload. Thresholds, retention, capacity, and cost controls are exposed as configuration because they depend on actual traffic and operating requirements.
