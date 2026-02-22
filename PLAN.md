# Bravebird Take Home — Implementation Plan

## Stack Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| IaC | **AWS CDK (Python)** | Reusable constructs, imperative logic for dynamic resources, single `cdk deploy` to spin everything up |
| Compute | **ECS on Fargate** | Each task runs in its own Firecracker microVM — hardware-level isolation out of the box, no EC2 fleet to manage |
| Queue | **SQS FIFO (Fair Queues)** | Built-in noisy-neighbor protection via `MessageGroupId`, no Redis/self-managed infra needed |
| API | **API Gateway (HTTP API) + Lambda** | Serverless ingestion, scales to zero, handles auth/validation |
| Observability | **FFmpeg → S3 streaming + CloudWatch Logs** | Zero-disk session recording, pre-signed URLs for playback |
| Secrets | **AWS Secrets Manager** | IAM-controlled injection into task at runtime, never exposed as env vars |
| Language | **Python** throughout (CDK, Lambda handlers, agent) | Single language across infra, Lambdas, and agent code |

## Deep-Dives Chosen (2 of 5)

1. **Concurrency & Scheduling** — SQS Fair Queues, multi-queue priority, per-tenant rate limiting
2. **Observability & "The Flight Recorder"** — Session recording (Xvfb + FFmpeg → S3), real-time log streaming, startup health checks

These two give the best signal on systems thinking without requiring real proxy provider accounts or Spot Instance complexity that's hard to demo locally.

---

## Architecture Overview

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

## Networking & Security Architecture

```
         VPC (10.0.0.0/16)
         │
         ├── Public Subnet (10.0.1.0/24)
         │   └── NAT Gateway (internet egress for agent tasks)
         │
         ├── Private Subnet (10.0.10.0/24) — platform services
         │   └── ECS cluster control plane, Lambda VPC endpoints, etc.
         │
         └── Agent Isolated Subnet (10.0.200.0/24) — Fargate tasks ONLY
             ├── Route table: 0.0.0.0/0 → NAT Gateway (internet only, no local VPC routes)
             ├── NACL:
             │   ├── DENY outbound to 10.0.0.0/16 (block all VPC traffic)
             │   ├── DENY outbound to 169.254.169.254/32 (block EC2 IMDS)
             │   └── ALLOW outbound to 0.0.0.0/0 on 80/443
             └── Security Group:
                 ├── Inbound: NONE
                 └── Outbound: 80/443 to 0.0.0.0/0 only
```

**Task role (minimal):** `s3:PutObject` on `s3://bucket/jobs/{jobId}/*` — nothing else. The agent is a write-only black box into its scoped S3 prefix. It has no access to DynamoDB, no API endpoints, no internal resources.

**ECS Task Metadata Endpoint (169.254.170.2):** Cannot be blocked without breaking IAM role assumption. Mitigated by the minimal task role — even if credentials are exfiltrated, they can only write to one S3 prefix.

## Project Structure

```
cuseinfra/
├── app.py                        # CDK app entry point
├── stacks/
│   └── cuseinfra_stack.py        # Main stack (all resources)
├── infra/
│   ├── networking.py             # VPC, isolated subnets, NACLs, security groups
│   ├── job_ingestion.py          # API GW + Ingest Lambda + SQS queues (3 priority tiers)
│   ├── job_executor.py           # ECS Cluster + Fargate Task Def + Worker Lambda
│   ├── observability.py          # S3 bucket + pre-signed URL Lambda + log config
│   └── reaper.py                 # DynamoDB TTL table + Destroy Lambda + EventBridge sweep
├── lambda/
│   ├── ingest/
│   │   └── handler.py            # Validate input, write job to DDB, route to priority queue
│   ├── worker/
│   │   └── handler.py            # Consume SQS, call RunTask, tag with expiry
│   ├── completion/
│   │   └── handler.py            # ECS state change handler — read S3 output, update DDB
│   ├── reaper/
│   │   └── handler.py            # DDB Stream handler — stop expired tasks
│   ├── sweep/
│   │   └── handler.py            # EventBridge scheduled sweep for orphaned tasks
│   ├── presign/
│   │   └── handler.py            # Generate pre-signed S3 URLs for recordings/output
│   └── get_job/
│       └── handler.py            # GET /jobs/{id} — return job status + output
├── agent/
│   ├── Dockerfile                # Xvfb + Chromium + FFmpeg + agent script
│   ├── entrypoint.sh             # Start Xvfb, FFmpeg recording, run agent, upload output
│   └── agent.py                  # Placeholder: open browser → search → screenshot
├── tests/
│   └── test_cuseinfra_stack.py   # CDK assertion tests
├── cdk.json
├── requirements.txt
└── README.md
```

---

## Phase-by-Phase Build Plan

### Phase 1: Project Scaffolding
- `cdk init app --language typescript`
- Set up project structure (dirs, tsconfig, dependencies)
- Create the placeholder agent: `agent/agent.py` + `agent/Dockerfile`
  - Xvfb virtual display (creates a fake screen for the browser)
  - Chromium headless
  - Simple script: open browser → Google search → take screenshot → save to `/output/`
  - FFmpeg captures Xvfb display and streams to S3 via entrypoint
- Build and test the Docker image locally

### Phase 2: Core Infrastructure (CDK)
- **Networking construct** (`lib/constructs/networking.ts`):
  - VPC with 3 subnet tiers: public (NAT GW), private (platform), isolated (agent tasks)
  - Agent subnet route table: only `0.0.0.0/0 → NAT Gateway`
  - NACLs on agent subnet: DENY `10.0.0.0/16`, DENY `169.254.169.254/32`, ALLOW `0.0.0.0/0:80/443`
  - Security group for Fargate tasks: no inbound, outbound 80/443 only
- **ECS Cluster**: Fargate-only, `AWSVPC` networking mode
- **Fargate Task Definition**:
  - Container image from ECR
  - Task role: `s3:PutObject` scoped to `s3://bucket/jobs/{jobId}/*` — nothing else
  - Placed in agent isolated subnet with locked-down security group
- **ECR Repository**: For the agent Docker image
- **S3 Bucket**: For job output + session recordings, lifecycle policy to expire after 7 days

### Phase 3: Job Ingestion & Scheduling (Deep-Dive #1)
- **API Gateway** (HTTP API):
  - `POST /jobs` — submit a new job
  - `GET /jobs/{id}` — check job status + output
  - `GET /jobs/{id}/recording` — get pre-signed URL for session recording
- **Ingest Lambda**:
  - Validates input (task description, tenant_id, priority: high/medium/low)
  - Writes job record to DynamoDB (status: PENDING)
  - Routes message to the correct priority queue based on input
  - `MessageGroupId = tenant_id` for fair queuing within each priority tier
  - `MessageDeduplicationId` for exactly-once semantics
- **3 SQS FIFO Queues** (high/medium/low priority), each with Fair Queuing:
  - Automatic noisy-neighbor mitigation within each tier
  - Visibility timeout matched to max task duration
- **Worker Lambda** (single function, 3 event source mappings):
  - `jobs-high.fifo` → ESM with `MaximumConcurrency: 30`
  - `jobs-medium.fifo` → ESM with `MaximumConcurrency: 15`
  - `jobs-low.fifo` → ESM with `MaximumConcurrency: 5`
  - Calls `ecs:RunTask` to spin up Fargate task in isolated subnet
  - Updates DynamoDB job status to RUNNING
  - Passes job metadata (task description, job ID, S3 prefix) as container environment overrides
- **Per-Tenant Rate Limiting**:
  - DynamoDB table tracking per-tenant active task count
  - Worker Lambda checks count before launching; if limit exceeded, message returns to queue via visibility timeout backoff
  - Configurable max concurrent tasks per tenant (default: 5)

### Phase 4: Observability & Flight Recorder (Deep-Dive #2)
- **Session Recording Pipeline** (inside the container):
  - `entrypoint.sh` starts Xvfb on `:99`, then FFmpeg captures the virtual display
  - FFmpeg streams recording directly to S3 (zero-disk pattern)
  - Screenshots captured at key agent steps saved as PNGs to S3 prefix
- **Log Streaming**:
  - Fargate task logs to CloudWatch Logs via `awslogs` driver
  - Agent script writes structured JSON logs (step, action, timestamp, screenshot_key)
  - Log group with 30-day retention
- **Startup Health Check**:
  - `entrypoint.sh` polls for Xvfb and Chromium readiness with a 30s timeout
  - If not ready in time, exits with error code → Fargate marks task FAILED
- **Pre-signed URL Lambda**:
  - `GET /jobs/{id}/recording` generates time-limited S3 URLs for the MP4 and screenshots

### Phase 5: Cost Control — The Reaper
- **DynamoDB Jobs Table** with TTL:
  - `expiresAt` attribute set to `now + MAX_TASK_DURATION` (e.g., 15 min)
  - When TTL expires, DynamoDB Stream emits a DELETE event
- **Reaper Lambda** (DynamoDB Stream trigger):
  - Receives expired job records
  - Calls `ecs:StopTask` to kill any still-running Fargate task
  - Updates job status to TIMED_OUT
  - Logs the forced termination for audit
- **Belt-and-suspenders**: EventBridge rule every 10 min triggers a sweep Lambda that catches anything the TTL stream missed

### Phase 6: Completion Handler & Status API
- **ECS Task State Change → EventBridge → Completion Lambda**:
  - Detects when any Fargate task stops (success or failure)
  - Reads `result.json` from the task's S3 prefix
  - Updates DynamoDB: status → COMPLETED or FAILED, stores output reference
- **Agent output contract**: task writes to its S3 prefix:
  - `s3://bucket/jobs/{jobId}/result.json` — structured output
  - `s3://bucket/jobs/{jobId}/screenshots/*.png` — step screenshots
  - `s3://bucket/jobs/{jobId}/recording.mp4` — session recording (via FFmpeg)
- **GET /jobs/{id}** returns:
  ```json
  {
    "jobId": "abc-123",
    "status": "COMPLETED",
    "tenantId": "tenant-1",
    "priority": "high",
    "createdAt": "...",
    "completedAt": "...",
    "output": { "result": "..." },
    "recording": "https://s3.../presigned-url",
    "screenshots": ["https://s3.../step1.png"]
  }
  ```

### Phase 7: Testing & Documentation
- CDK unit tests (snapshot tests for generated CloudFormation)
- Integration test script: submit job via API → poll status → verify output + recording in S3
- README with:
  - Architecture diagram
  - One-command deploy instructions (`cdk deploy`)
  - How to submit a job and view results (curl examples)
  - Stack choices and tradeoffs
  - What I'd build next with more time

---

## Failure Modes Handled

| Failure | Mitigation |
|---------|-----------|
| Fargate task fails to start | SQS visibility timeout returns message to queue for retry; DynamoDB status stays PENDING |
| Agent hangs / infinite loop | Reaper kills task after TTL (15 min max); EventBridge sweep as fallback |
| FFmpeg/recording fails | Agent still completes; recording marked as UNAVAILABLE, logs still in CloudWatch |
| Agent crashes mid-execution | ECS state change event fires anyway → Completion Lambda marks job FAILED |
| SQS message lost | FIFO queue with exactly-once delivery; DynamoDB is source of truth for job state |
| Tenant floods system | Fair Queue deprioritizes noisy tenant; per-tenant concurrency limit enforced in Worker Lambda |
| Reaper Lambda fails | Belt-and-suspenders: EventBridge sweep catches orphaned tasks |
| S3 output missing on completion | Completion Lambda checks for result.json; if absent, marks job FAILED with "no output" reason |

## TODO — Detailed Task List

### Phase 1: Project Scaffolding ✅

- [x] **1.1** Bootstrap CDK Python project (`app.py`, `cdk.json`, `requirements.txt`)
- [x] **1.2** Install CDK dependencies in `.venv`: `aws-cdk-lib`, `constructs`, `pytest`
- [x] **1.3** Create directory structure: `stacks/`, `infra/`, `lambda/*/`, `agent/`, `tests/`
- [x] **1.4** Write `agent/agent.py` — placeholder script that:
  - Launches Chromium via Selenium
  - Navigates to Google, performs a search
  - Takes screenshots, saves to `/output/screenshots/`
  - Writes `/output/result.json` with structured output
  - Uploads all artifacts to S3 inline
- [x] **1.5** Write `agent/entrypoint.sh` — container entrypoint that:
  - Starts Xvfb on display `:99`
  - Waits for Xvfb to be ready (health check poll loop, 30s timeout)
  - Starts FFmpeg to capture `:99` to local file (fragmented MP4)
  - Runs `agent.py`
  - On agent exit, stops FFmpeg, uploads recording to S3
  - Exits with agent's exit code
- [x] **1.6** Write `agent/Dockerfile`:
  - Base image: `python:3.11-slim`
  - Install: Xvfb, Chromium, FFmpeg, Selenium, boto3, AWS CLI
  - Set `DISPLAY=:99`
  - Entrypoint: `entrypoint.sh`
- [ ] **1.7** Build Docker image locally and verify: `docker build -t cuseinfra-agent ./agent`
- [ ] **1.8** Test agent locally: `docker run --rm cuseinfra-agent` — confirm Xvfb starts, browser runs, screenshot is saved

### Phase 2: Core Infrastructure (CDK) ✅

- [x] **2.1** Write `infra/networking.py` — Networking construct:
  - [x] **2.1.1** Create VPC (`10.0.0.0/16`) with 3 subnet tiers
  - [x] **2.1.2** Public subnet with NAT Gateway
  - [x] **2.1.3** Private subnet for platform services
  - [x] **2.1.4** Agent isolated subnet (`PRIVATE_WITH_EGRESS`) routed through NAT GW
  - [x] **2.1.5** NACL on agent subnet: DENY VPC CIDR outbound, DENY IMDS outbound, ALLOW 443/80 outbound, ALLOW ephemeral inbound
  - [x] **2.1.6** Security group for Fargate tasks: no inbound rules, outbound 80/443 to `0.0.0.0/0`
- [x] **2.2** Create ECR via `ContainerImage.from_asset("agent")` (CDK builds + pushes automatically)
- [x] **2.3** Create S3 bucket for job output + recordings:
  - [x] **2.3.1** Bucket with S3-managed encryption
  - [x] **2.3.2** Lifecycle rule: expire objects after 7 days
  - [x] **2.3.3** Block all public access
- [x] **2.4** Create ECS Cluster (Fargate-only)
- [x] **2.5** Create Fargate Task Definition:
  - [x] **2.5.1** Container definition pointing to asset-built image
  - [x] **2.5.2** Task execution role: ECR pull + CloudWatch Logs write (CDK auto-grants)
  - [x] **2.5.3** Task role: `s3:PutObject` scoped to `s3://bucket/jobs/*` only
  - [x] **2.5.4** CPU/memory: 1 vCPU, 2 GB
  - [x] **2.5.5** CloudWatch log group with `awslogs` driver, 30-day retention
  - [x] **2.5.6** S3_BUCKET env var set; S3_PREFIX + JOB_ID passed as runtime overrides
- [x] **2.6** Write `stacks/cuseinfra_stack.py` — wire all constructs together
- [x] **2.7** Run `cdk synth` — 110 CloudFormation resources generated successfully

### Phase 3: Job Ingestion & Scheduling ✅

- [x] **3.1** Create DynamoDB Jobs table:
  - [x] **3.1.1** Partition key: `jobId` (string)
  - [x] **3.1.2** GSI on `tenantId` + `status` for per-tenant queries
  - [x] **3.1.3** TTL attribute: `expiresAt`
  - [x] **3.1.4** Enable DynamoDB Streams (NEW_AND_OLD_IMAGES)
- [x] **3.2** Create 3 SQS FIFO queues:
  - [x] **3.2.1** `jobs-high.fifo` — content-based deduplication enabled
  - [x] **3.2.2** `jobs-medium.fifo` — content-based deduplication enabled
  - [x] **3.2.3** `jobs-low.fifo` — content-based deduplication enabled
  - [x] **3.2.4** All queues: visibility timeout = 900s (15 min)
  - [x] **3.2.5** Dead-letter queue for each (after 3 failed receives)
- [x] **3.3** Write `infra/job_ingestion.py` — API Gateway + Ingest Lambda + SQS:
  - [x] **3.3.1** HTTP API with `POST /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/recording`
  - [x] **3.3.2** Wire routes to appropriate Lambda handlers
- [x] **3.4** Write `lambda/ingest/handler.py`:
  - [x] **3.4.1** Validate request body: `taskDescription`, `tenantId`, `priority`
  - [x] **3.4.2** Generate `jobId` (UUID v4)
  - [x] **3.4.3** Write to DynamoDB with TTL
  - [x] **3.4.4** Send SQS message to correct priority queue, `MessageGroupId = tenantId`
  - [x] **3.4.5** Return `{ jobId, status: "PENDING" }` with 202 Accepted
- [x] **3.5** Write `lambda/worker/handler.py`:
  - [x] **3.5.1** Parse SQS event record to extract job metadata
  - [x] **3.5.2** Query DynamoDB for tenant's active task count
  - [x] **3.5.3** If count >= max (5), throw error to return message to queue
  - [x] **3.5.4** Call `ecs:RunTask` with network config + container overrides
  - [x] **3.5.5** Update DynamoDB: status → `RUNNING`, `taskArn`, `startedAt`
- [x] **3.6** Configure Worker Lambda event source mappings in CDK:
  - [x] **3.6.1** ESM for `jobs-high.fifo` with `MaximumConcurrency: 30`
  - [x] **3.6.2** ESM for `jobs-medium.fifo` with `MaximumConcurrency: 15`
  - [x] **3.6.3** ESM for `jobs-low.fifo` with `MaximumConcurrency: 5`
- [x] **3.7** Grant Worker Lambda permissions: `ecs:RunTask`, `iam:PassRole`, DynamoDB read/write

### Phase 4: Observability & Flight Recorder ✅

- [x] **4.1** `agent/entrypoint.sh` implements S3 streaming:
  - [x] **4.1.1** Start Xvfb, poll for readiness (check `/tmp/.X11-unix/X99`), exit 1 if not ready in 30s
  - [x] **4.1.2** Start FFmpeg: capture `:99` at 5 FPS, fragmented MP4 to local file
  - [x] **4.1.3** Run `agent.py`, capture exit code
  - [x] **4.1.4** Stop FFmpeg gracefully (SIGTERM, wait)
  - [x] **4.1.5** Upload recording to S3 via `aws s3 cp`
  - [x] **4.1.6** Exit with agent's exit code
- [x] **4.2** `agent/agent.py` writes structured JSON logs to stdout:
  - [x] **4.2.1** Each step logs: `{ "step": N, "action": "...", "timestamp": "..." }`
  - [x] **4.2.2** On screenshot: `{ "step": N, "action": "screenshot", "key": "..." }`
  - [x] **4.2.3** Upload each screenshot to `s3://$BUCKET/$PREFIX/screenshots/stepN.png` inline
- [x] **4.3** Write `infra/observability.py`:
  - [x] **4.3.1** CloudWatch Log Group for Fargate tasks, 30-day retention
  - [x] **4.3.2** S3 output bucket with lifecycle + encryption + public access block
- [x] **4.4** Write `lambda/presign/handler.py`:
  - [x] **4.4.1** Look up job in DynamoDB to get S3 prefix
  - [x] **4.4.2** List objects under `s3://bucket/jobs/{jobId}/`
  - [x] **4.4.3** Generate pre-signed GET URLs (1-hour expiry)
  - [x] **4.4.4** Return `{ recording: "...", screenshots: ["..."] }`
- [x] **4.5** Grant presign Lambda permissions: DynamoDB read, `s3:GetObject` + `s3:ListBucket`

### Phase 5: Cost Control — The Reaper ✅

- [x] **5.1** Write `infra/reaper.py`:
  - [x] **5.1.1** DynamoDB Stream on Jobs table → Reaper Lambda trigger (filter: REMOVE events only)
  - [x] **5.1.2** EventBridge rule: rate every 10 minutes → Sweep Lambda
- [x] **5.2** Write `lambda/reaper/handler.py`:
  - [x] **5.2.1** Parse DynamoDB Stream event to extract expired job record (OldImage)
  - [x] **5.2.2** If `taskArn` exists and status was `RUNNING`, call `ecs:StopTask`
  - [x] **5.2.3** Write a new DynamoDB record with status `TIMED_OUT`
  - [x] **5.2.4** Log the forced termination event
- [x] **5.3** Write `lambda/sweep/handler.py`:
  - [x] **5.3.1** Scan DynamoDB for jobs where `status = RUNNING` and `createdAt < now - 20min`
  - [x] **5.3.2** For each orphan: call `ecs:StopTask`, update status to `TIMED_OUT`
  - [x] **5.3.3** Call `ecs:ListTasks` and cross-reference — stop any Fargate tasks not in DynamoDB
- [x] **5.4** Grant reaper Lambdas permissions: `ecs:StopTask`, `ecs:ListTasks`, DynamoDB read/write

### Phase 6: Completion Handler & Status API ✅

- [x] **6.1** Write `lambda/completion/handler.py`:
  - [x] **6.1.1** Handle EventBridge ECS Task State Change event (filter: task stopped)
  - [x] **6.1.2** Extract `taskArn` from event, look up `jobId` in DynamoDB by `taskArn-index` GSI
  - [x] **6.1.3** Check container exit code from event detail
  - [x] **6.1.4** If exit 0: try to read `result.json` from S3 — if exists, status = `COMPLETED`; if missing, `FAILED`
  - [x] **6.1.5** If non-zero exit: status = `FAILED` with exit code and stop reason
  - [x] **6.1.6** Update DynamoDB: `status`, `completedAt`, `exitCode`, `output`, `stopReason`
- [x] **6.2** GSI on `taskArn` added in `infra/job_executor.py`
- [x] **6.3** Create EventBridge rule:
  - [x] **6.3.1** Source: `aws.ecs`, detail-type: `ECS Task State Change`
  - [x] **6.3.2** Filter: `lastStatus = STOPPED`, `clusterArn = our cluster`
  - [x] **6.3.3** Target: Completion Lambda
- [x] **6.4** Write `lambda/get_job/handler.py`:
  - [x] **6.4.1** Read job record from DynamoDB
  - [x] **6.4.2** If status is `COMPLETED`, include output from DDB (cached by completion Lambda)
  - [x] **6.4.3** Return full job object with status, timestamps, output
- [x] **6.5** Grant Completion Lambda permissions: DynamoDB read/write, `s3:GetObject`

### Phase 7: Testing & Documentation ✅

- [x] **7.1** Write `tests/test_cuseinfra_stack.py` — 17 CDK assertion tests:
  - [x] **7.1.1** VPC created
  - [x] **7.1.2** Agent NACL rules: deny VPC CIDR, deny IMDS, security group no ingress
  - [x] **7.1.3** Fargate task role has S3 PutObject policy
  - [x] **7.1.4** 6 SQS queues (3 main FIFO + 3 DLQ), correct names and settings
  - [x] **7.1.5** DynamoDB table with TTL, streams, GSIs (tenant-status, taskArn)
  - [x] **7.1.6** ECS cluster, Fargate task definition (1024 CPU, 2048 MiB)
  - [x] **7.1.7** EventBridge rules: ECS stopped + sweep schedule
  - [x] **7.1.8** S3 lifecycle, API Gateway + routes
- [x] **7.2** Write integration test script (`tests/integration.sh`):
  - [x] **7.2.1** Submit a job via `POST /jobs` (curl)
  - [x] **7.2.2** Poll `GET /jobs/{id}` until completion (with timeout)
  - [x] **7.2.3** Fetch recording URL via `GET /jobs/{id}/recording`
  - [x] **7.2.4** Verify output exists in job response
  - [x] **7.2.5** Print pass/fail summary with color
- [ ] **7.3** Push agent Docker image to ECR: `docker push` (done automatically by `cdk deploy`)
- [ ] **7.4** Deploy: `cdk deploy --all`
- [ ] **7.5** Run integration test against deployed stack
- [x] **7.6** Write README.md:
  - [x] **7.6.1** Architecture diagram
  - [x] **7.6.2** Prerequisites: AWS account, CDK CLI, Docker, Python
  - [x] **7.6.3** One-command deploy: `npx cdk deploy`
  - [x] **7.6.4** Usage: curl examples for all 3 endpoints
  - [x] **7.6.5** Stack choices and tradeoffs section
  - [x] **7.6.6** Failure modes section
  - [x] **7.6.7** What I'd build next with more time

---

## What I'd Build Next (With More Time)

- **Warm Pools** — Pre-initialized Fargate tasks for sub-5s job start
- **Spot Instances** — ECS Capacity Providers with Spot for 60-90% cost reduction
- **IP Rotation Proxy** — Egress through residential proxy provider for geo-simulation
- **Live Session Streaming** — WebSocket-based VNC relay for real-time viewing
- **Multi-region** — Deploy to multiple AWS regions for geographic redundancy
- **CI/CD Pipeline** — CodePipeline/GitHub Actions for automated deploy on push
- **Credential Rotation** — Vault sidecar for automatic secret rotation
