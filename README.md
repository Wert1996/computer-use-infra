# Cuseinfra — Ephemeral Computer Use Agent Infrastructure

A scalable, production-ready backend for provisioning and managing ephemeral environments that execute "Computer Use" agent tasks. Built with AWS CDK (Python), deployed with a single command.

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
                      └──────┬───────┘
                             │ ecs:RunTask
                  ┌──────────▼──────────┐
                  │  Fargate Task       │  (isolated subnet, minimal task role)
                  │  ┌────────────────┐ │
                  │  │ Xvfb + Browser │──────▶ FFmpeg → S3 (recording)
                  │  │ Agent Script   │──────▶ S3 prefix (output + screenshots)
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

## Deep-Dives Implemented

### 1. Concurrency & Scheduling

- **3 priority tiers**: High/Medium/Low via separate SQS FIFO queues with different `MaximumConcurrency` (30/15/5)
- **Fair queuing**: `MessageGroupId = tenantId` ensures SQS automatically deprioritizes noisy tenants
- **Per-tenant rate limiting**: Worker Lambda checks active task count in DynamoDB before launching (default max: 5 per tenant)
- **Single Worker Lambda**: 3 event source mappings — priority is expressed through capacity allocation, not strict ordering

### 2. Observability & "The Flight Recorder"

- **Session recording**: Xvfb virtual display → FFmpeg captures at 5 FPS → fragmented MP4 uploaded to S3
- **Step screenshots**: Agent uploads screenshots at key steps (Google homepage, search results, final state)
- **Structured JSON logs**: Every agent action logged as `{"step": N, "action": "...", "timestamp": "..."}` to CloudWatch
- **Startup health check**: Entrypoint polls for Xvfb readiness with 30s timeout — exits immediately if environment is broken
- **Pre-signed URLs**: `GET /jobs/{id}/recording` returns time-limited S3 URLs for recording + screenshots

## Networking & Security

```
VPC (10.0.0.0/16)
├── Public Subnet — NAT Gateway
├── Private Subnet — Platform services
└── Agent Isolated Subnet — Fargate tasks ONLY
    ├── NACL: DENY 10.0.0.0/16 outbound, DENY 169.254.169.254/32, ALLOW 80/443
    └── Security Group: no inbound, outbound 80/443 only
```

- **Task role is minimal**: `s3:PutObject` on `s3://bucket/jobs/{jobId}/*` — nothing else
- Agent cannot reach internal VPC resources, IMDS, or any AWS service except its scoped S3 prefix
- ECS Task Metadata Endpoint (169.254.170.2) cannot be blocked without breaking IAM — mitigated by minimal task role

## Prerequisites

- AWS account with CDK bootstrapped (`cdk bootstrap`)
- Node.js >= 20 (for CDK CLI)
- Python >= 3.11
- Docker (for building the agent container image)

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

Response:
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
    "query": "search for AWS Lambda pricing",
    "results": ["AWS Lambda Pricing - Amazon Web Services", "..."]
  }
}
```

### Get session recording

```bash
curl "$API_URL/jobs/abc-123/recording"
```

Response:
```json
{
  "jobId": "abc-123",
  "recording": "https://s3...presigned-url/recording.mp4",
  "screenshots": [
    "https://s3...presigned-url/screenshots/step1_google_home.png",
    "https://s3...presigned-url/screenshots/step2_search_results.png"
  ]
}
```

### Run integration tests

```bash
./tests/integration.sh "$API_URL"
```

## Stack Choices & Tradeoffs

| Decision | Choice | Why |
|----------|--------|-----|
| IaC | AWS CDK (Python) | Single language with agent code, reusable constructs, `cdk deploy` does everything |
| Compute | ECS on Fargate | Firecracker microVM isolation per task, no fleet management, `AWSVPC` mode |
| Queue | SQS FIFO | Fair queuing built-in, content-based dedup, no self-managed infrastructure |
| Priority | Multi-queue | 3 separate FIFO queues with different `MaximumConcurrency` on event source mappings |
| Recording | FFmpeg → S3 | Zero-disk pattern, fragmented MP4 survives container crashes, pre-signed URLs for access |
| Output | S3 write-only | Agent task role is `s3:PutObject` on a scoped prefix — minimal blast radius |
| Reaper | DynamoDB TTL + sweep | TTL triggers stream → Lambda for primary cleanup; EventBridge cron sweep as fallback |

**Tradeoff: Priority is capacity-based, not preemptive.** High-priority gets 30 concurrency slots vs 5 for low. Under load, high-priority jobs get 6x the throughput. But if the system is idle, a low-priority job starts just as fast. True preemption would require a single ordered consumer, adding latency.

**Tradeoff: NACLs are stateless.** We must allow ephemeral port inbound for return traffic. This is standard for NACLs but less intuitive than security groups.

**Tradeoff: Task metadata endpoint is reachable.** We can't block `169.254.170.2` without breaking IAM. The minimal task role is the guardrail.

## Failure Modes

| Failure | Mitigation |
|---------|-----------|
| Fargate task fails to start | SQS visibility timeout returns message to queue for retry; DynamoDB status stays PENDING |
| Agent hangs / infinite loop | Reaper kills task after TTL (15 min max); EventBridge sweep as fallback |
| FFmpeg/recording fails | Agent still completes; recording marked as unavailable, logs still in CloudWatch |
| Agent crashes mid-execution | ECS state change event fires → Completion Lambda marks job FAILED |
| Tenant floods system | Fair Queue deprioritizes noisy tenant; per-tenant concurrency limit enforced |
| Reaper Lambda fails | EventBridge sweep catches orphaned tasks every 10 min |
| S3 output missing | Completion Lambda checks for result.json; if absent, marks job FAILED |

## What I'd Build Next

- **Warm Pools** — Pre-initialized Fargate tasks for sub-5s job start
- **Spot Instances** — ECS Capacity Providers with Spot for 60-90% cost reduction
- **IP Rotation Proxy** — Egress through residential proxy provider for geo-simulation
- **Live Session Streaming** — WebSocket-based VNC relay for real-time viewing
- **Multi-region** — Deploy to multiple AWS regions for geographic redundancy
- **CI/CD Pipeline** — GitHub Actions for automated deploy on push
- **Credential Rotation** — Vault sidecar for automatic secret rotation

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
│   ├── ingest/handler.py         # POST /jobs
│   ├── worker/handler.py         # SQS → ecs:RunTask
│   ├── completion/handler.py     # ECS state change → update DynamoDB
│   ├── reaper/handler.py         # DynamoDB TTL stream → ecs:StopTask
│   ├── sweep/handler.py          # Scheduled orphan cleanup
│   └── get_job/handler.py        # GET /jobs/{id}
├── agent/
│   ├── Dockerfile
│   ├── entrypoint.sh
│   └── agent.py
├── tests/
│   ├── test_cuseinfra_stack.py   # 17 CDK assertion tests
│   └── integration.sh            # End-to-end integration test
├── cdk.json
└── requirements.txt
```
