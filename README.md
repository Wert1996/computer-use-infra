# Cuseinfra — Ephemeral Computer Use Agent Infrastructure

A scalable, production-grade backend for provisioning and managing ephemeral environments that execute "Computer Use" agent tasks. Built with AWS CDK (Python), deployed with a single command.

The system accepts job requests via HTTP API, schedules them across priority-tiered SQS FIFO queues with per-tenant fair queuing, spins up isolated Fargate containers running a headless browser agent, records the entire session as video, and tears everything down automatically after completion or timeout.

---

## Table of Contents

- [Architecture](#architecture)
- [Deep-Dives Implemented](#deep-dives-implemented)
- [Networking & Security](#networking--security)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Deploy](#deploy)
- [Usage](#usage)
- [Testing](#testing)
- [Stack Choices & Tradeoffs](#stack-choices--tradeoffs)
- [Failure Modes](#failure-modes)
- [Implementation Checklist](#implementation-checklist)
- [Future Improvements](#future-improvements)

---

## Architecture

```
                  ┌──────────────┐
  Client ────────>│ API Gateway  │
                  └──────┬───────┘
                         │ POST /jobs
                  ┌──────▼───────┐
                  │  Ingest λ    │  validates, writes to DynamoDB, routes by priority
                  └──┬───┬───┬───┘
                     │   │   │
          ┌──────────▼┐ ┌▼────────┐ ┌▼──────────┐
          │ HIGH.fifo │ │ MED.fifo│ │ LOW.fifo  │   SQS FIFO Fair Queues
          │ MaxConc:30│ │MaxConc:15│ │ MaxConc:5 │   MessageGroupId=tenant_id
          └─────┬─────┘ └────┬────┘ └─────┬─────┘
                │            │             │
                └────────────▼─────────────┘
                      ┌──────▼───────┐
                      │  Worker λ    │  single function, 3 event source mappings
                      │              │  generates presigned S3 POST for container
                      └──────┬───────┘
                             │ ecs:RunTask
                  ┌──────────▼──────────┐
                  │  Fargate Task       │  (isolated subnet, no S3 credentials)
                  │  ┌────────────────┐ │
                  │  │ Xvfb + Browser │──────▶ FFmpeg → presigned POST (recording)
                  │  │ Agent Script   │──────▶ presigned POST (output + screenshots)
                  │  └────────────────┘ │
                  └──────────┬──────────┘
                             │ task stops (success or crash)
                  ┌──────────▼──────────┐
                  │  ECS State Change   │  EventBridge rule
                  │  → Completion λ     │  reads output from S3, updates DynamoDB
                  └─────────────────────┘
                  ┌─────────────────────┐
                  │  Reaper (TTL-based) │  DynamoDB TTL → Stream → Destroy λ
                  │  + EventBridge sweep│  belt-and-suspenders fallback
                  └─────────────────────┘
```

### Request Flow

```
1.  Client ─── POST /jobs ──────────────────────────────────────────────▶ API GW
2.  API GW ─── invoke ──────────────────────────────────────────────────▶ Ingest λ
3.  Ingest λ ─── PutItem ──────────────────────────────────────────────▶ DynamoDB (PENDING)
4.  Ingest λ ─── SendMessage(GroupId=tenant) ──────────────────────────▶ SQS FIFO (by priority)
5.  SQS ─── ESM trigger ──────────────────────────────────────────────▶ Worker λ
6.  Worker λ ─── Query GSI (tenant RUNNING count) ────────────────────▶ DynamoDB (rate limit check)
7.  Worker λ ─── generate_presigned_post (scoped to jobs/{jobId}/) ───▶ S3
8.  Worker λ ─── RunTask (passes presigned POST as env override) ─────▶ ECS Fargate
9.  Worker λ ─── UpdateItem ──────────────────────────────────────────▶ DynamoDB (RUNNING)
10. Fargate ─── entrypoint.sh starts Xvfb → FFmpeg → agent.py ───────▶ (isolated subnet)
11. agent.py ─── presigned POST (screenshots, result.json) ──────────▶ S3
12. entrypoint.sh ─── presigned POST (recording.mp4) ────────────────▶ S3
13. Fargate task exits ──────────────────────────────────────────────▶ ECS State Change Event
14. EventBridge ─── rule match ──────────────────────────────────────▶ Completion λ
15. Completion λ ─── GetObject (result.json) ────────────────────────▶ S3
16. Completion λ ─── UpdateItem ─────────────────────────────────────▶ DynamoDB (COMPLETED/FAILED)

Parallel: DynamoDB TTL expires ──▶ Stream REMOVE ──▶ Reaper λ ──▶ ecs:StopTask (if still RUNNING)
Parallel: EventBridge schedule ──▶ Sweep λ ──▶ scan for orphans ──▶ ecs:StopTask
```

---

## Deep-Dives Implemented

### 1. High-Performance Networking

The agent has full internet access but is completely isolated from internal infrastructure.

```
VPC (10.0.0.0/16)
├── Public Subnet — NAT Gateway (sole internet egress for agents)
└── Agent Isolated Subnet — Fargate tasks ONLY
    ├── NACL: DENY 10.0.0.0/16 outbound, DENY 169.254.169.254/32, ALLOW 80/443
    └── Security Group: no inbound, outbound 80/443 only
```

- **VPC isolation**: Agent tasks run in a dedicated isolated subnet (`PRIVATE_WITH_EGRESS`) with routes only to the NAT Gateway — no routes to any other VPC resource
- **NACL enforcement** (stateless firewall):
  - Rule 100: **DENY** all outbound to `10.0.0.0/16` — blocks access to every VPC resource (other subnets, ENIs, endpoints)
  - Rule 101: **DENY** all outbound to `169.254.169.254/32` — blocks EC2 Instance Metadata Service (IMDS), preventing credential theft
  - Rule 200-201: **ALLOW** outbound TCP 80/443 — internet HTTP/HTTPS only
  - Rule 100 inbound: **ALLOW** TCP 1024-65535 — ephemeral ports for return traffic (NACLs are stateless)
- **Security group**: No inbound rules at all. Outbound restricted to ports 80 and 443
- **ECS Task Metadata Endpoint** (169.254.170.2): Cannot be blocked at the network level without breaking IAM role assumption. Mitigated by the minimal task role — even if credentials are exfiltrated, they can only read scoped secrets (no S3, no DynamoDB, no other AWS services)
- **Not implemented**: IP rotation proxy layer for geographic simulation (listed in future improvements)

### 2. Concurrency & Scheduling

The system handles 50+ simultaneous requests through priority-tiered queuing with fair scheduling.

- **3 priority tiers**: High/Medium/Low via separate SQS FIFO queues with different `MaximumConcurrency` on event source mappings (30/15/5). Under load, high-priority jobs get 6x the throughput of low-priority
- **Fair queuing**: `MessageGroupId = tenantId` on every SQS message. SQS Fair Queues automatically detect noisy message groups and deprioritize them, ensuring one tenant's burst doesn't starve others
- **Per-tenant rate limiting**: Worker Lambda queries the `tenantId-status-index` GSI to count RUNNING tasks for the tenant. If count >= max (default 5), the Lambda throws an exception, the message returns to the queue via visibility timeout, and is retried later
- **Single Worker Lambda**: One function with 3 event source mappings — priority is expressed through capacity allocation, not code duplication. Total system concurrency: 50 simultaneous tasks (30 + 15 + 5)

### 3. Observability & "The Flight Recorder"

Full session replay capability — video recording, step-by-step screenshots, and structured logs.

- **Session recording**: Xvfb virtual display (`:99`, 1024x768) -> FFmpeg captures at 5 FPS -> fragmented MP4 (`-movflags frag_keyframe+empty_moov`) uploaded to S3. Fragmented MP4 ensures the recording is valid even if the container crashes mid-session
- **Step screenshots**: Agent takes screenshots at key milestones (homepage loaded, data extracted, detail page) and uploads each immediately via presigned POST
- **Structured JSON logs**: Every agent action emits `{"step": N, "action": "...", "timestamp": "..."}` to stdout, captured by the `awslogs` driver into CloudWatch with 30-day retention
- **Startup health check**: `entrypoint.sh` polls for Xvfb readiness by checking `/tmp/.X11-unix/X99` with a 30-second timeout. If the virtual display fails to initialize, the container exits immediately with error code 1 — no agent work is attempted on a broken environment
- **Pre-signed URLs**: `GET /jobs/{id}` returns time-limited S3 URLs for the recording MP4 and all screenshots, allowing direct browser playback

### 4. Cost Control & Efficiency

Automated reaper with belt-and-suspenders redundancy — no environment can be orphaned.

- **DynamoDB TTL reaper** (primary): Every job record has an `expiresAt` attribute set to `now + MAX_TASK_DURATION` (15 minutes). When DynamoDB TTL deletes the record, a Stream REMOVE event triggers the Reaper Lambda, which calls `ecs:StopTask` on any still-running Fargate task
- **EventBridge sweep** (fallback): Every 10 minutes, the Sweep Lambda scans DynamoDB for jobs where `status = RUNNING` and `createdAt` exceeds the threshold. It also calls `ecs:ListTasks` to find any Fargate tasks that exist but have no matching DynamoDB record (true orphans). Both are terminated
- **S3 lifecycle**: Output bucket has a 7-day expiration rule — recordings, screenshots, and results are automatically cleaned up
- **CloudWatch log retention**: 30-day retention on log groups — no unbounded log growth
- **No idle resources**: Fargate tasks are fully ephemeral (no EC2 fleet, no warm pools). Lambdas scale to zero. SQS queues have no base cost. DynamoDB is PAY_PER_REQUEST
- **Not implemented**: Spot Instances and Warm Pools for cost/latency optimization (listed in future improvements)

### 5. Security & Multi-Tenancy

Zero-trust credential injection with hardware-level isolation between jobs.

- **Firecracker microVM isolation**: Each Fargate task runs in its own Firecracker microVM with a dedicated kernel. Job A and Job B have completely separate filesystem, memory, and network stacks — there is no shared kernel or container runtime between tasks
- **Secrets Manager injection**: Tenant credentials are stored in AWS Secrets Manager under `cuseinfra/tenants/{tenantId}/website-credentials`. The agent calls `secretsmanager:GetSecretValue` at runtime and loads credentials directly into application memory. Credentials are never passed as environment variables, never written to disk, and never visible in `docker inspect` or process listings
- **Presigned POST (zero-trust S3 upload)**: The Fargate task role has **no S3 permissions at all**. Instead:
  1. The Worker Lambda generates a scoped presigned POST for `jobs/{jobId}/` with a 100 MB size limit and 1-hour expiry
  2. The presigned POST data is passed to the container as a JSON environment variable
  3. The agent uploads files using `urllib.request` (stdlib) — no AWS SDK needed for uploads
  4. Even if the task's IAM credentials are exfiltrated, they cannot write to S3 or access any AWS service other than scoped secrets
- **Minimal task role**: The ECS task role has only `secretsmanager:GetSecretValue` on `cuseinfra/tenants/*` — no S3, no DynamoDB, no ECS, no other AWS service access
- **Known limitation**: Per-tenant secret isolation is not yet enforced — the task role can read all tenant secrets under the prefix. Fix with ABAC (attribute-based access control) using session tags. See [Future Improvements](#future-improvements)

---

## Project Structure

```
cuseinfra/
├── app.py                        # CDK app entry point
├── stacks/
│   └── cuseinfra_stack.py        # Main stack wiring all constructs
├── infra/
│   ├── networking.py             # VPC, NACLs, security groups
│   ├── job_ingestion.py          # API GW, SQS queues, Lambdas, EventBridge
│   ├── job_executor.py           # ECS cluster, task def, DynamoDB
│   ├── observability.py          # S3 bucket, CloudWatch log group
│   └── reaper.py                 # TTL reaper, sweep schedule
├── lambda/
│   ├── ingest/handler.py         # POST /jobs — validate, write DDB, send SQS
│   ├── worker/handler.py         # SQS → presigned POST → ecs:RunTask
│   ├── completion/handler.py     # ECS state change → read S3 → update DDB
│   ├── reaper/handler.py         # DynamoDB TTL stream → ecs:StopTask
│   ├── sweep/handler.py          # Scheduled orphan cleanup
│   └── get_job/handler.py        # GET /jobs/{id} — status + output + presigned URLs
├── agent/
│   ├── Dockerfile                # Xvfb + Chromium + FFmpeg (no awscli)
│   ├── entrypoint.sh             # Start Xvfb, FFmpeg, run agent, upload recording
│   └── agent.py                  # Selenium agent + presigned POST upload
├── tests/
│   ├── test_cuseinfra_stack.py   # 17 CDK assertion tests
│   └── integration.sh            # End-to-end integration test
├── ARCHITECTURE.md               # Detailed CloudFormation resource breakdown
├── FUTURE_IMPROVEMENTS.md        # Items removed for demo + future work
├── PLAN.md                       # Original implementation plan with task checklist
├── cdk.json
└── requirements.txt
```

---

## Prerequisites

- AWS account with CDK bootstrapped (`cdk bootstrap`)
- Node.js >= 20 (for CDK CLI)
- Python >= 3.11
- Docker (for building the agent container image)

---

## Deploy

```bash
# Create virtual environment
python3 -m virtualenv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Deploy (builds Docker image, pushes to ECR, creates all resources)
npx cdk deploy --require-approval never
```

The deploy outputs:
- `ApiUrl` — the HTTP API endpoint
- `OutputBucket` — the S3 bucket for job outputs and recordings

---

## Usage

### Submit a job

```bash
curl -X POST "$API_URL/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "taskDescription": "search for AWS Lambda pricing",
    "tenantId": "tenant-1",
    "priority": "high"
  }'
```

Response:
```json
{"jobId": "abc-123", "status": "PENDING"}
```

### Check job status

```bash
curl "$API_URL/jobs/abc-123"
```

Response (completed):
```json
{
  "jobId": "abc-123",
  "status": "COMPLETED",
  "tenantId": "tenant-1",
  "priority": "high",
  "createdAt": 1740268800,
  "startedAt": 1740268802,
  "completedAt": 1740268830,
  "exitCode": 0,
  "output": {
    "status": "success",
    "books_found": 20,
    "books": [{"title": "A Light in the Attic", "price": "£51.77", "rating": "Three"}],
    "screenshots": ["screenshots/step1_homepage.png", "screenshots/step2_books_extracted.png"]
  }
}
```

---

## Testing

### Unit tests (CDK assertions)

```bash
python -m pytest tests/ -v
```

Runs 17 assertion tests covering VPC, subnets, NACLs, security groups, task role permissions, SQS queues, DynamoDB table/GSIs, ECS cluster, EventBridge rules, S3 lifecycle, API Gateway routes, and more.

### Integration test (end-to-end)

```bash
./tests/integration.sh "$API_URL"
```

Submits a job, polls for completion, verifies recording and screenshot URLs are available, and checks that job output is present in the response.

---

## Stack Choices & Tradeoffs

| Decision | Choice | Why |
|----------|--------|-----|
| IaC | AWS CDK (Python) | Single language with agent code, reusable constructs, `cdk deploy` does everything |
| Compute | ECS on Fargate | Firecracker microVM isolation per task, no fleet management, `AWSVPC` mode |
| Queue | SQS FIFO | Fair queuing built-in, content-based dedup, no self-managed infrastructure |
| Priority | Multi-queue | 3 separate FIFO queues with different `MaximumConcurrency` on event source mappings |
| Recording | FFmpeg -> S3 | Zero-disk pattern, fragmented MP4 survives container crashes |
| Output | Presigned POST | Agent has zero S3 credentials — uploads via scoped presigned POST from Worker Lambda |
| Secrets | AWS Secrets Manager | IAM-controlled runtime injection, never exposed as static env vars |
| Reaper | DynamoDB TTL + sweep | TTL triggers stream -> Lambda for primary cleanup; EventBridge cron sweep as fallback |

**Tradeoff: Priority is capacity-based, not preemptive.** High-priority gets 30 concurrency slots vs 5 for low. Under load, high-priority jobs get 6x the throughput. But if the system is idle, a low-priority job starts just as fast. True preemption would require a single ordered consumer, adding latency.

**Tradeoff: NACLs are stateless.** We must allow ephemeral port inbound for return traffic. This is standard for NACLs but less intuitive than security groups.


**Tradeoff: Presigned POST expiry.** The presigned POST expires after 1 hour. For tasks running longer than that, uploads would fail. The current max task duration is 15 minutes, so this is well within bounds.

---

## Failure Modes

| Failure | Mitigation |
|---------|-----------|
| Fargate task fails to start | SQS visibility timeout returns message to queue for retry; DynamoDB status stays PENDING |
| Agent hangs / infinite loop | Reaper kills task after TTL (15 min max); EventBridge sweep as fallback |
| FFmpeg/recording fails | Agent still completes; recording marked as unavailable, logs still in CloudWatch |
| Agent crashes mid-execution | ECS state change event fires -> Completion Lambda marks job FAILED |
| Tenant floods system | Fair Queue deprioritizes noisy tenant; per-tenant concurrency limit enforced |
| Reaper Lambda fails | EventBridge sweep catches orphaned tasks every 10 min |
| S3 output missing | Completion Lambda checks for result.json; if absent, marks job FAILED |
| Presigned POST expires | 1-hour expiry covers 15-min max task duration with wide margin |

---

## Implementation Checklist

### Phase 1: Project Scaffolding

- [x] Bootstrap CDK Python project (`app.py`, `cdk.json`, `requirements.txt`)
- [x] Create directory structure: `stacks/`, `infra/`, `lambda/*/`, `agent/`, `tests/`
- [x] Write `agent/agent.py` — Selenium script (navigate, extract data, screenshot, upload)
- [x] Write `agent/entrypoint.sh` — Xvfb, FFmpeg recording, agent execution, upload
- [x] Write `agent/Dockerfile` — Xvfb + Chromium + FFmpeg + Selenium + boto3
- [ ] Local Docker build and test (not done — tested via `cdk deploy` which builds automatically)

### Phase 2: Core Infrastructure (CDK)

- [x] VPC with public + agent isolated subnets
- [x] NACLs: DENY VPC CIDR, DENY IMDS, ALLOW 80/443 outbound, ALLOW ephemeral inbound
- [x] Security group: no inbound, outbound 80/443 only
- [x] ECS Cluster (Fargate-only)
- [x] Fargate Task Definition: 1 vCPU, 2 GB, awsvpc mode
- [x] Task role: minimal (secrets read only, no S3 write)
- [x] S3 bucket: SSE-S3, 7-day lifecycle, public access blocked
- [x] CloudWatch Log Group: 30-day retention

### Phase 3: Job Ingestion & Scheduling

- [x] DynamoDB Jobs table: PK `jobId`, GSI `tenantId-status`, GSI `taskArn`, TTL `expiresAt`, Streams
- [x] 3 SQS FIFO queues (`high`, `medium`, `low`) with content-based dedup
- [x] API Gateway HTTP API with `POST /jobs` and `GET /jobs/{id}`
- [x] Ingest Lambda: validate, write DDB, route to priority queue
- [x] Worker Lambda: rate limit check, presigned POST generation, `ecs:RunTask`, update DDB
- [x] Event source mappings with `MaximumConcurrency`: 30/15/5
- [x] Per-tenant concurrency limiting (max 5 concurrent per tenant)

### Phase 4: Observability & Flight Recorder

- [x] Xvfb virtual display + FFmpeg session recording (5 FPS, fragmented MP4)
- [x] Startup health check: poll for Xvfb readiness with 30s timeout
- [x] Structured JSON logging to CloudWatch
- [x] Step screenshots uploaded at key agent milestones
- [x] GetJob Lambda returns presigned URLs for recordings and screenshots

### Phase 5: Cost Control — The Reaper

- [x] DynamoDB TTL -> Stream -> Reaper Lambda (stops expired tasks)
- [x] EventBridge sweep every 10 minutes (catches orphaned tasks)
- [x] Belt-and-suspenders: both mechanisms for reliability

### Phase 6: Completion Handler

- [x] EventBridge ECS Task State Change rule -> Completion Lambda
- [x] Completion Lambda: read result.json from S3, update DDB with status + output
- [x] Handle both success (exit 0) and failure cases

### Phase 7: Security Hardening

- [x] Presigned POST: Worker Lambda generates scoped presigned POST, passes to container
- [x] Task role has zero S3 write permissions (removed `s3:PutObject` grant)
- [x] Worker Lambda has `s3:PutObject` for presigned POST generation
- [x] Agent uploads via `urllib.request` multipart POST (stdlib, no AWS SDK for uploads)
- [x] Removed `awscli` from Docker image (no longer needed)
- [x] Secrets Manager for credential injection (IAM-controlled, runtime only)

### Phase 8: Testing & Documentation

- [x] 17 CDK assertion tests (all passing)
- [x] Integration test script (`tests/integration.sh`)
- [x] README with architecture, deploy instructions, usage examples
- [x] ARCHITECTURE.md with full CloudFormation resource breakdown
- [x] FUTURE_IMPROVEMENTS.md with removed items and future work
- [ ] Deploy to AWS and run integration test (not done — requires AWS account)

### Items Simplified for Demo

- [ ] Dead Letter Queues (removed to reduce resource count)
- [ ] Multi-AZ (single AZ for demo simplicity)
- [ ] Private subnets (removed — nothing placed there)
- [ ] S3 auto-delete custom resource (removed — lifecycle rules handle expiry)
- [ ] Separate Presign Lambda (merged into GetJob Lambda)

---

## Future Improvements

### High Priority (Production Readiness)

| Item | Description | Complexity |
|------|-------------|------------|
| **Multi-AZ** | Change `max_azs=1` to `max_azs=2` in networking.py. Single AZ is a single point of failure. | Low |
| **Dead Letter Queues** | Add 3 DLQ FIFO queues with `max_receive_count=3`. Without DLQs, poison messages retry indefinitely and block their MessageGroupId. | Low |
| **API Authentication** | Add tenant auth (API keys, JWT, or Cognito). Currently the API is open. | Medium |
| **API Rate Limiting** | Add rate limiting at the API Gateway level. Currently only per-tenant concurrency is limited at the worker level. | Low |
| **Per-Tenant Secret Isolation** | Currently the task role can read all secrets under `cuseinfra/tenants/*`. Fix with ABAC: tag secrets with `tenantId`, pass session tags via ECS task, add IAM condition `secretsmanager:ResourceTag/tenantId == ${aws:PrincipalTag/tenantId}`. | Medium |
| **Non-Retryable Failure Handling** | On non-retryable failures (validation errors, bad input), discard the message instead of retrying. Currently messages get infinitely retried. | Low |

### Medium Priority (Operations & Scale)

| Item | Description | Complexity |
|------|-------------|------------|
| **VPC Endpoints** | Add gateway/interface endpoints for S3, DynamoDB, CloudWatch, Secrets Manager to avoid NAT Gateway data transfer costs. | Low |
| **CI/CD Pipeline** | GitHub Actions for automated deploy on push. Each Lambda and the agent in separate repos with independent CI, triggering infra redeployment via `repository_dispatch`. | Medium |
| **Warm Pools** | Pre-initialized Fargate tasks for sub-5s job start. Cold Fargate start takes 30-60s. | High |
| **Priority Queue (RabbitMQ)** | Replace SQS multi-queue with RabbitMQ for true priority ordering (0-255 range). Current multi-queue approach is capacity-based, not preemptive. | High |
| **ECS Service for APIs** | Move API handlers from Lambda to long-running ECS services for lower latency at scale. | Medium |
| **Consistency Tuning** | Use strong consistency for writes and eventual consistency for reads where appropriate. Add DynamoDB DAX for caching high-throughput reads. | Medium |

### Low Priority

| Item | Description | Complexity |
|------|-------------|------------|
| **Spot Instances** | ECS Capacity Providers with Spot for 60-90% cost reduction. Requires Spot-aware graceful degradation. | High |
| **IP Rotation Proxy** | Egress through residential proxy provider (NetNut, Browserbase) for geographic simulation. | Medium |
| **Live Session Streaming** | WebSocket-based VNC relay for real-time viewing of agent sessions. | High |
| **Multi-Region** | Deploy to multiple AWS regions for geographic redundancy and lower latency. | High |
| **Credential Rotation** | Vault sidecar for automatic secret rotation with short-lived access tokens. | Medium |
| **Agent Prompt Injection Defense** | Once secrets are loaded into agent memory, prevent exfiltration via prompt injection and agent phishing attacks. Application-level guardrails needed. | High |

### Operational Improvements

- **Separate Git Repos**: Each Lambda and the agent code in separate repos with independent CI. Infra repo uses `latest` tag so new deployments automatically pick up latest images.
- **Monitoring & Alerting**: CloudWatch alarms on DLQ depth, task failure rate, and queue age. SNS notifications for operational events.
- **Cost Attribution**: Tag all resources with `tenantId` for per-tenant cost tracking via AWS Cost Explorer.

