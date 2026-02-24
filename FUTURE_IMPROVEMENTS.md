# Future Improvements — Removed for Demo Simplicity

Items removed from the current deployment to keep the demo lean (104 → 61 CloudFormation resources). Each section describes what was removed, why it matters in production, and how to add it back.

---

## 1. Multi-AZ (removed 2nd AZ)

**What was removed:** The second Availability Zone — 1 extra public subnet, 1 extra agent subnet, 2 route tables, 2 routes, 2 subnet-route-table associations.

**Why it matters in production:** A single AZ is a single point of failure. If that AZ goes down, no new Fargate tasks can be launched and the NAT Gateway becomes unreachable. Production workloads should always span 2+ AZs.

**How to add it back:**
```python
# In infra/networking.py, change:
max_azs=1
# To:
max_azs=2
```

**Resources added:** ~8 (1 subnet per tier per AZ + route tables + associations)

---

## 2. Dead Letter Queues (removed)

**What was removed:** 3 SQS FIFO DLQs (`jobs-high-dlq.fifo`, `jobs-medium-dlq.fifo`, `jobs-low-dlq.fifo`) and the `dead_letter_queue` config on each priority queue.

**Why it matters in production:** Without DLQs, a poison message (one that always fails processing) will be retried indefinitely, consuming Worker Lambda concurrency and blocking other messages in the same `MessageGroupId`. DLQs capture these after N failed receives so the queue keeps moving. They also provide a natural alarm point — a CloudWatch alarm on DLQ message count signals systemic issues.

**How to add it back:**
```python
# In infra/job_ingestion.py, inside the queue creation loop:
dlq = sqs.Queue(self, f"DLQ-{priority}",
    queue_name=f"jobs-{priority}-dlq.fifo",
    fifo=True,
    retention_period=cdk.Duration.days(7),
)
# Then add to the main queue:
queue = sqs.Queue(self, f"Queue-{priority}",
    ...,
    dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=dlq),
)
```

**Resources added:** 3 (one DLQ per priority tier)

---

## 3. Presign Lambda (merged into GetJob)

**What was removed:** A separate `lambda/presign/handler.py` Lambda function, its IAM role, IAM policy, API Gateway route (`GET /jobs/{id}/recording`), and API Gateway integration. The presign logic (listing S3 objects and generating pre-signed URLs) was merged into the GetJob Lambda.

**Why it matters in production:** A dedicated endpoint is cleaner for API consumers who only want recording URLs without the full job payload. It also allows separate caching and rate-limiting for what could be a more expensive operation (S3 ListObjects + multiple pre-signed URL generations).

**How to add it back:** Restore the `lambda/presign/` directory and add a separate route in `infra/job_ingestion.py`:
```python
presign_fn = _lambda.Function(self, "PresignFn",
    runtime=_lambda.Runtime.PYTHON_3_12,
    handler="handler.handler",
    code=_lambda.Code.from_asset(f"{LAMBDA_DIR}/presign"),
    timeout=cdk.Duration.seconds(10),
    environment={
        "JOBS_TABLE": jobs_table.table_name,
        "OUTPUT_BUCKET": output_bucket.bucket_name,
    },
)
api.add_routes(
    path="/jobs/{id}/recording",
    methods=[apigwv2.HttpMethod.GET],
    integration=apigwv2_integrations.HttpLambdaIntegration("PresignIntegration", presign_fn),
)
```

**Resources added:** 6 (Lambda + role + policy + route + integration + permission)

---

## 4. Private Subnets (removed)

**What was removed:** 2 private subnets (1 per AZ, now 0 since we're single-AZ), their route tables, routes, and associations. These were declared as a "platform services" tier but nothing was actually placed there.

**Why it matters in production:** If you need VPC-connected Lambdas (e.g., to access an RDS database or ElastiCache cluster), they would run in these private subnets. Also needed for VPC endpoints (DynamoDB, S3, SQS) to avoid NAT Gateway data transfer costs.

**How to add it back:**
```python
# In infra/networking.py subnet_configuration, add:
ec2.SubnetConfiguration(
    name="Private",
    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
    cidr_mask=24,
),
```

**Resources added:** 4 per AZ (subnet + route table + route + association)

---

## 5. S3 Auto-Delete Custom Resource (removed)

**What was removed:** `auto_delete_objects=True` on the S3 bucket, which created a CDK custom resource Lambda + IAM role + S3 bucket policy (3 resources). This Lambda empties the bucket before CloudFormation deletes it during `cdk destroy`.

**Why it matters:** Only for development — allows clean `cdk destroy` without manual bucket emptying. Production stacks should never auto-delete data. The bucket still has `RemovalPolicy.DESTROY` so CloudFormation will attempt deletion, but it will fail if the bucket is non-empty (which is fine — lifecycle rules expire objects after 7 days).

**How to add it back:**
```python
# In infra/observability.py:
self.output_bucket = s3.Bucket(self, "OutputBucket",
    ...,
    auto_delete_objects=True,  # add this line
)
```

**Resources added:** 3 (custom resource Lambda + role + bucket policy)

---

## Summary

| Item | Resources Removed | Production Priority |
|------|-------------------|-------------------|
| Multi-AZ | ~8 | **High** — single AZ is a single point of failure |
| Dead Letter Queues | 3 | **High** — prevents poison messages from blocking queues |
| Presign Lambda | 6 | **Low** — merged into GetJob, no functionality lost |
| Private Subnets | 8 | **Medium** — needed for VPC endpoints and internal services |
| S3 Auto-Delete | 3 | **Low** — dev convenience only |
| **Total** | **~29** | |

## Additional Future Work (not previously implemented)

### High Priority (Production Readiness)

| Item | Description |
|------|-------------|
| **API Authentication** | Add tenant auth (API keys, JWT, or Cognito). Currently the API is open. |
| **API Rate Limiting** | Add rate limiting at the API Gateway level. Currently only per-tenant concurrency is limited at the worker level. |
| **Per-Tenant Secret Isolation** | Currently the ECS task role can read all secrets under `cuseinfra/tenants/*`, so tenant-1's task could read tenant-2's secrets. Fix with ABAC: tag each secret with `tenantId`, pass a session tag via ECS task, and add an IAM condition `secretsmanager:ResourceTag/tenantId == ${aws:PrincipalTag/tenantId}`. Alternatively, create a separate task role per tenant. |
| **Non-Retryable Failures** | On non-retryable failures (validation errors, bad input), discard the message instead of retrying. Currently messages get infinitely retried without a DLQ. |

### Medium Priority (Operations & Scale)

| Item | Description |
|------|-------------|
| **VPC Endpoints** | Add gateway/interface endpoints for S3, DynamoDB, CloudWatch, Secrets Manager to avoid NAT Gateway data transfer costs. |
| **CI/CD Pipeline** | Each Lambda and agent code in separate git repos. Each repo has its own CI that triggers infra redeployment via GitHub API `repository_dispatch`. If the infra repo uses the `latest` tag, new jobs and lambdas automatically use the latest images. |
| **ECS Service for APIs** | Move API handlers from Lambda to long-running ECS services for lower latency at scale. |
| **Consistency Tuning** | Use strong consistency for writes and eventual consistency for reads where appropriate. Add DynamoDB DAX for caching high-throughput reads. |
| **Priority Queue (RabbitMQ)** | Replace SQS multi-queue with RabbitMQ for true priority ordering (0-255 range). Can also be done using `PartialBatchResponse` in SQS itself. |

### Low Priority (Advanced Features)

| Item | Description |
|------|-------------|
| **Warm Pools** | Pre-initialized Fargate tasks for sub-5s job start. |
| **Spot Instances** | ECS Capacity Providers with Spot for 60-90% cost reduction. |
| **IP Rotation Proxy** | Egress through residential proxy provider for geo-simulation. |
| **Live Session Streaming** | WebSocket-based VNC relay for real-time viewing. |
| **Multi-region** | Deploy to multiple AWS regions for geographic redundancy. |
| **Credential Rotation** | Vault sidecar for automatic secret rotation with short-lived access tokens. Tokens should be discarded immediately at the application level. |
| **Agent Prompt Injection Defense** | Once secrets are loaded into agent memory, prevent exfiltration via prompt injection and agent phishing attacks. The IAM credentials are temporary (attackers won't receive long-lived credentials), but application-level guardrails are still needed. |