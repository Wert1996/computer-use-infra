# Cuseinfra — Architecture & CloudFormation Resource Breakdown

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                         AWS CLOUD                                               │
│                                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                                    API LAYER                                                │ │
│  │                                                                                             │ │
│  │   ┌────────────────────────────────────────────────────┐                                    │ │
│  │   │           API Gateway (HTTP API)                   │                                    │ │
│  │   │                                                    │                                    │ │
│  │   │  POST /jobs ──────────┐                            │                                    │ │
│  │   │  GET  /jobs/{id} ─────┼── Lambda Integrations      │                                    │ │
│  │   │  GET  /jobs/{id}/rec ─┘                            │                                    │ │
│  │   └────────┬──────────┬───────────┬────────────────────┘                                    │ │
│  │            │          │           │                                                          │ │
│  └────────────┼──────────┼───────────┼──────────────────────────────────────────────────────────┘ │
│               │          │           │                                                           │
│               ▼          ▼           ▼                                                           │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                                  LAMBDA FUNCTIONS                                           │ │
│  │                                                                                             │ │
│  │   ┌─────────────┐  ┌────────────┐  ┌─────────────┐                                         │ │
│  │   │  Ingest λ   │  │ GetJob λ   │  │ Presign λ   │                                         │ │
│  │   │             │  │            │  │             │                                         │ │
│  │   │ • Validate  │  │ • Read DDB │  │ • List S3   │                                         │ │
│  │   │ • Write DDB │  │ • Return   │  │ • Sign URLs │                                         │ │
│  │   │ • Send SQS  │  │   status   │  │ • Return    │                                         │ │
│  │   └──────┬──────┘  └─────┬──────┘  └──────┬──────┘                                         │ │
│  │          │               │                │                                                 │ │
│  │          │          ┌────▼──────────────────▼────┐                                           │ │
│  │          │          │      DynamoDB              │                                           │ │
│  │          │          │      Jobs Table            │                                           │ │
│  │          │          │                            │                                           │ │
│  │          │          │  PK: jobId                 │                                           │ │
│  │          │          │  GSI: tenantId-status      │                                           │ │
│  │          │          │  GSI: taskArn              │                                           │ │
│  │          │          │  TTL: expiresAt            │                                           │ │
│  │          │          │  Stream: NEW_AND_OLD_IMAGES│                                           │ │
│  │          │          └─────┬──────────────┬───────┘                                           │ │
│  │          │                │              │                                                   │ │
│  │          │          (TTL delete)    (read/write)                                             │ │
│  │          │                │              │                                                   │ │
│  │          │                ▼              │                                                   │ │
│  │          │          ┌───────────┐        │                                                   │ │
│  │          │          │ Reaper λ  │        │                                                   │ │
│  │          │          │           │        │                                                   │ │
│  │          │          │ DDB Stream│        │         ┌───────────┐                              │ │
│  │          │          │ → StopTask│        │         │ Sweep λ   │  EventBridge                │ │
│  │          │          └───────────┘        │         │ every 10m │  Scheduled Rule             │ │
│  │          │                               │         │ → orphan  │                              │ │
│  │          │                               │         │   cleanup │                              │ │
│  │          │                               │         └───────────┘                              │ │
│  │          │                               │                                                   │ │
│  │          │                               │         ┌──────────────┐                           │ │
│  │          │                               ├────────▶│ Completion λ │                           │ │
│  │          │                               │         │              │                           │ │
│  │          │                               │         │ ECS State    │  EventBridge              │ │
│  │          │                               │         │ Change Event │  Event Rule               │ │
│  │          │                               │         │ → read S3    │                           │ │
│  │          │                               │         │ → update DDB │                           │ │
│  │          │                               │         └──────────────┘                           │ │
│  │          │                               │                                                   │ │
│  └──────────┼───────────────────────────────┼───────────────────────────────────────────────────┘ │
│             │                               │                                                    │
│             ▼                               │                                                    │
│  ┌─────────────────────────────────────┐    │                                                    │
│  │         SCHEDULING LAYER            │    │                                                    │
│  │                                     │    │                                                    │
│  │  ┌───────────┐ ┌──────────┐ ┌──────┴──┐ │                                                    │
│  │  │ HIGH.fifo │ │ MED.fifo │ │LOW.fifo │ │   SQS FIFO Queues                                  │
│  │  │           │ │          │ │         │ │   (content-based dedup)                              │
│  │  │ DLQ: high │ │ DLQ: med │ │DLQ: low│ │                                                    │
│  │  │ MaxRcv: 3 │ │ MaxRcv:3 │ │MaxRcv:3│ │   MessageGroupId = tenantId                        │
│  │  └─────┬─────┘ └────┬────┘ └───┬────┘ │   → Fair Queue per tenant                           │
│  │        │            │          │       │                                                    │
│  │        │  ESM:30    │ ESM:15   │ ESM:5 │   MaximumConcurrency per queue                      │
│  │        └────────────┼──────────┘       │                                                    │
│  │                     │                  │                                                    │
│  │              ┌──────▼───────┐          │                                                    │
│  │              │  Worker λ    │          │                                                    │
│  │              │              │          │                                                    │
│  │              │ • Check rate │          │                                                    │
│  │              │   limit      │          │                                                    │
│  │              │ • RunTask    │          │                                                    │
│  │              │ • Update DDB │          │                                                    │
│  │              └──────┬───────┘          │                                                    │
│  │                     │ ecs:RunTask      │                                                    │
│  └─────────────────────┼──────────────────┘                                                    │
│                        │                                                                        │
│                        ▼                                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────────────────────────┐│
│  │                            VPC  (10.0.0.0/16)                                               ││
│  │                                                                                             ││
│  │  ┌──────────────────────┐  ┌──────────────────────┐  ┌───────────────────────────────────┐  ││
│  │  │   Public Subnet(s)   │  │  Private Subnet(s)   │  │    Agent Isolated Subnet(s)       │  ││
│  │  │                      │  │                      │  │           (2 AZs)                  │  ││
│  │  │  ┌────────────────┐  │  │  Platform services   │  │  ┌─────────────────────────────┐  │  ││
│  │  │  │  NAT Gateway   │  │  │  (Lambda, etc.)      │  │  │     Fargate Task            │  │  ││
│  │  │  │  ┌──────────┐  │  │  │                      │  │  │     (Firecracker MicroVM)   │  │  ││
│  │  │  │  │   EIP    │  │  │  │                      │  │  │                             │  │  ││
│  │  │  │  └──────────┘  │  │  │                      │  │  │  ┌────────┐  ┌───────────┐ │  │  ││
│  │  │  └────────────────┘  │  │                      │  │  │  │  Xvfb  │  │  Chromium │ │  │  ││
│  │  │                      │  │                      │  │  │  │ :99    │  │  Browser  │ │  │  ││
│  │  │  ┌────────────────┐  │  │                      │  │  │  └───┬────┘  └─────┬─────┘ │  │  ││
│  │  │  │ Internet GW    │  │  │                      │  │  │      │             │       │  │  ││
│  │  │  └────────────────┘  │  │                      │  │  │  ┌───▼─────────────▼────┐  │  │  ││
│  │  │                      │  │                      │  │  │  │  FFmpeg (5 FPS)      │  │  │  ││
│  │  └──────────────────────┘  └──────────────────────┘  │  │  │  → recording.mp4     │  │  │  ││
│  │                                                      │  │  └──────────┬───────────┘  │  │  ││
│  │                                                      │  │             │              │  │  ││
│  │                                                      │  │  ┌──────────▼───────────┐  │  │  ││
│  │                                                      │  │  │  agent.py            │  │  │  ││
│  │                                                      │  │  │  → screenshots/      │  │  │  ││
│  │                                                      │  │  │  → result.json       │──┼──┼──┼▶ S3
│  │                                                      │  │  └──────────────────────┘  │  │  ││
│  │                                                      │  │                             │  │  ││
│  │                                                      │  └─────────────────────────────┘  │  ││
│  │                                                      │                                   │  ││
│  │                                                      │  NACL Rules:                      │  ││
│  │                                                      │   DENY out → 10.0.0.0/16         │  ││
│  │                                                      │   DENY out → 169.254.169.254/32   │  ││
│  │                                                      │   ALLOW out → 0.0.0.0/0 :443     │  ││
│  │                                                      │   ALLOW out → 0.0.0.0/0 :80      │  ││
│  │                                                      │   ALLOW in  → ephemeral ports     │  ││
│  │                                                      │                                   │  ││
│  │                                                      │  Security Group:                  │  ││
│  │                                                      │   Inbound:  NONE                  │  ││
│  │                                                      │   Outbound: 80/443 → 0.0.0.0/0   │  ││
│  │                                                      └───────────────────────────────────┘  ││
│  │                                                                                             ││
│  └──────────────────────────────────────────────────────────────────────────────────────────────┘│
│                                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                                    STORAGE                                                  │ │
│  │                                                                                             │ │
│  │   ┌─────────────────────────────┐         ┌───────────────────────────────────┐              │ │
│  │   │  S3 Output Bucket           │         │  CloudWatch Logs                  │              │ │
│  │   │                             │         │                                   │              │ │
│  │   │  jobs/{jobId}/              │         │  Agent Log Group (30d retention)  │              │ │
│  │   │    ├── result.json          │         │                                   │              │ │
│  │   │    ├── recording.mp4        │         │  Structured JSON:                 │              │ │
│  │   │    └── screenshots/         │         │  {"step":1,"action":"navigate"}   │              │ │
│  │   │        ├── step1_*.png      │         │  {"step":2,"action":"screenshot"} │              │ │
│  │   │        ├── step2_*.png      │         │  {"step":7,"action":"complete"}   │              │ │
│  │   │        └── step3_*.png      │         │                                   │              │ │
│  │   │                             │         └───────────────────────────────────┘              │ │
│  │   │  Lifecycle: 7-day expiry    │                                                           │ │
│  │   │  Encryption: SSE-S3        │                                                           │ │
│  │   │  Public access: BLOCKED    │                                                           │ │
│  │   └─────────────────────────────┘                                                           │ │
│  │                                                                                             │ │
│  └─────────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Request Flow

```
1. Client ─── POST /jobs ──────────────────────────────────────────────────────────────▶ API GW
2. API GW ─── invoke ──────────────────────────────────────────────────────────────────▶ Ingest λ
3. Ingest λ ─── PutItem ──────────────────────────────────────────────────────────────▶ DynamoDB (PENDING)
4. Ingest λ ─── SendMessage(GroupId=tenant) ──────────────────────────────────────────▶ SQS FIFO (by priority)
5. SQS ─── ESM trigger ──────────────────────────────────────────────────────────────▶ Worker λ
6. Worker λ ─── Query GSI (tenant RUNNING count) ────────────────────────────────────▶ DynamoDB (rate limit check)
7. Worker λ ─── RunTask ─────────────────────────────────────────────────────────────▶ ECS Fargate
8. Worker λ ─── UpdateItem ──────────────────────────────────────────────────────────▶ DynamoDB (RUNNING)
9. Fargate ─── entrypoint.sh starts Xvfb → FFmpeg → agent.py ───────────────────────▶ (isolated subnet)
10. agent.py ─── PutObject (screenshots, result.json) ───────────────────────────────▶ S3
11. entrypoint.sh ─── PutObject (recording.mp4) ─────────────────────────────────────▶ S3
12. Fargate task exits ──────────────────────────────────────────────────────────────▶ ECS State Change Event
13. EventBridge ─── rule match ──────────────────────────────────────────────────────▶ Completion λ
14. Completion λ ─── GetObject (result.json) ─────────────────────────────────────────▶ S3
15. Completion λ ─── UpdateItem ─────────────────────────────────────────────────────▶ DynamoDB (COMPLETED/FAILED)

Parallel: DynamoDB TTL expires ──▶ Stream REMOVE ──▶ Reaper λ ──▶ ecs:StopTask (if still RUNNING)
Parallel: EventBridge schedule ──▶ Sweep λ ──▶ scan for orphans ──▶ ecs:StopTask
```

## CloudFormation Resources (104 total)

### Networking — VPC & Subnets (31 resources)

These form the network perimeter. CDK creates 2 AZs with 3 subnet tiers each.

| # | Resource Type | CDK Path | Purpose |
|---|--------------|----------|---------|
| 1 | `AWS::EC2::VPC` | Networking/Vpc | The VPC (`10.0.0.0/16`) containing all resources |
| 2 | `AWS::EC2::InternetGateway` | Networking/Vpc | Allows public subnets to reach the internet |
| 3 | `AWS::EC2::VPCGatewayAttachment` | Networking/Vpc | Attaches the Internet Gateway to the VPC |
| 4 | `AWS::EC2::EIP` | Networking/Vpc/PublicSubnet1 | Elastic IP for the NAT Gateway |
| 5 | `AWS::EC2::NatGateway` | Networking/Vpc/PublicSubnet1 | NAT Gateway — the only internet egress path for agent tasks |
| 6 | `AWS::EC2::Subnet` | Networking/Vpc/PublicSubnet1 | Public subnet AZ1 — hosts NAT GW + IGW |
| 7 | `AWS::EC2::Subnet` | Networking/Vpc/PublicSubnet2 | Public subnet AZ2 |
| 8 | `AWS::EC2::Subnet` | Networking/Vpc/PrivateSubnet1 | Private subnet AZ1 — platform services |
| 9 | `AWS::EC2::Subnet` | Networking/Vpc/PrivateSubnet2 | Private subnet AZ2 — platform services |
| 10 | `AWS::EC2::Subnet` | Networking/Vpc/AgentIsolatedSubnet1 | Agent subnet AZ1 — Fargate tasks run here |
| 11 | `AWS::EC2::Subnet` | Networking/Vpc/AgentIsolatedSubnet2 | Agent subnet AZ2 — Fargate tasks run here |
| 12 | `AWS::EC2::RouteTable` | Networking/Vpc/PublicSubnet1 | Route table for public subnet AZ1 (→ IGW) |
| 13 | `AWS::EC2::RouteTable` | Networking/Vpc/PublicSubnet2 | Route table for public subnet AZ2 (→ IGW) |
| 14 | `AWS::EC2::RouteTable` | Networking/Vpc/PrivateSubnet1 | Route table for private subnet AZ1 (→ NAT) |
| 15 | `AWS::EC2::RouteTable` | Networking/Vpc/PrivateSubnet2 | Route table for private subnet AZ2 (→ NAT) |
| 16 | `AWS::EC2::RouteTable` | Networking/Vpc/AgentIsolatedSubnet1 | Route table for agent subnet AZ1 (→ NAT only) |
| 17 | `AWS::EC2::RouteTable` | Networking/Vpc/AgentIsolatedSubnet2 | Route table for agent subnet AZ2 (→ NAT only) |
| 18 | `AWS::EC2::Route` | Networking/Vpc/PublicSubnet1 | `0.0.0.0/0 → IGW` |
| 19 | `AWS::EC2::Route` | Networking/Vpc/PublicSubnet2 | `0.0.0.0/0 → IGW` |
| 20 | `AWS::EC2::Route` | Networking/Vpc/PrivateSubnet1 | `0.0.0.0/0 → NAT GW` |
| 21 | `AWS::EC2::Route` | Networking/Vpc/PrivateSubnet2 | `0.0.0.0/0 → NAT GW` |
| 22 | `AWS::EC2::Route` | Networking/Vpc/AgentIsolatedSubnet1 | `0.0.0.0/0 → NAT GW` (internet only, no VPC local) |
| 23 | `AWS::EC2::Route` | Networking/Vpc/AgentIsolatedSubnet2 | `0.0.0.0/0 → NAT GW` (internet only, no VPC local) |
| 24-29 | `AWS::EC2::SubnetRouteTableAssociation` (×6) | Networking/Vpc/* | Binds each subnet to its route table |
| 30 | `AWS::EC2::SecurityGroup` | Networking/AgentSG | Agent task SG: no inbound, outbound 80/443 only |

### Networking — NACLs (14 resources)

Network ACLs enforce subnet-level firewall rules. One NACL per agent subnet (2 AZs), with 5 rules each.

| # | Resource Type | CDK Path | Purpose |
|---|--------------|----------|---------|
| 31 | `AWS::EC2::NetworkAcl` | Networking/AgentNacl-AgentIsolatedSubnet1 | NACL for agent subnet AZ1 |
| 32 | `AWS::EC2::NetworkAcl` | Networking/AgentNacl-AgentIsolatedSubnet2 | NACL for agent subnet AZ2 |
| 33 | `AWS::EC2::SubnetNetworkAclAssociation` | AgentNacl-AgentIsolatedSubnet1/Default... | Binds NACL to agent subnet AZ1 |
| 34 | `AWS::EC2::SubnetNetworkAclAssociation` | AgentNacl-AgentIsolatedSubnet2/Default... | Binds NACL to agent subnet AZ2 |
| 35 | `AWS::EC2::NetworkAclEntry` | AgentNacl-.../DenyVpcOutbound (AZ1) | Rule 100: **DENY** outbound to `10.0.0.0/16` — blocks all VPC traffic |
| 36 | `AWS::EC2::NetworkAclEntry` | AgentNacl-.../DenyVpcOutbound (AZ2) | Rule 100: same for AZ2 |
| 37 | `AWS::EC2::NetworkAclEntry` | AgentNacl-.../DenyImdsOutbound (AZ1) | Rule 101: **DENY** outbound to `169.254.169.254/32` — blocks IMDS |
| 38 | `AWS::EC2::NetworkAclEntry` | AgentNacl-.../DenyImdsOutbound (AZ2) | Rule 101: same for AZ2 |
| 39 | `AWS::EC2::NetworkAclEntry` | AgentNacl-.../AllowHttpsOutbound (AZ1) | Rule 200: **ALLOW** outbound TCP 443 — internet HTTPS |
| 40 | `AWS::EC2::NetworkAclEntry` | AgentNacl-.../AllowHttpsOutbound (AZ2) | Rule 200: same for AZ2 |
| 41 | `AWS::EC2::NetworkAclEntry` | AgentNacl-.../AllowHttpOutbound (AZ1) | Rule 201: **ALLOW** outbound TCP 80 — internet HTTP |
| 42 | `AWS::EC2::NetworkAclEntry` | AgentNacl-.../AllowHttpOutbound (AZ2) | Rule 201: same for AZ2 |
| 43 | `AWS::EC2::NetworkAclEntry` | AgentNacl-.../AllowEphemeralInbound (AZ1) | Rule 100: **ALLOW** inbound TCP 1024-65535 — return traffic from NAT |
| 44 | `AWS::EC2::NetworkAclEntry` | AgentNacl-.../AllowEphemeralInbound (AZ2) | Rule 100: same for AZ2 |

### Compute — ECS & Container (3 resources)

| # | Resource Type | CDK Path | Purpose |
|---|--------------|----------|---------|
| 45 | `AWS::ECS::Cluster` | JobExecutor/Cluster | Fargate-only ECS cluster |
| 46 | `AWS::ECS::TaskDefinition` | JobExecutor/TaskDef | Fargate task def: 1 vCPU, 2 GB, awsvpc mode, agent container |
| 47 | `AWS::CDK::Metadata` | CDKMetadata | CDK analytics metadata (auto-generated) |

Note: The Docker image is built and pushed to ECR as a **CDK asset** during `cdk deploy`. The ECR repository is created in the CDK bootstrap stack, not in this template.

### Storage (4 resources)

| # | Resource Type | CDK Path | Purpose |
|---|--------------|----------|---------|
| 48 | `AWS::S3::Bucket` | Observability/OutputBucket | Job output + recordings. SSE-S3 encryption, 7-day lifecycle, public access blocked |
| 49 | `AWS::S3::BucketPolicy` | Observability/OutputBucket/Policy | Bucket policy enforcing encryption and granting auto-delete access |
| 50 | `Custom::S3AutoDeleteObjects` | Observability/OutputBucket/AutoDeleteObjects | CDK custom resource that empties the bucket on stack deletion (`RemovalPolicy.DESTROY`) |
| 51 | `AWS::Logs::LogGroup` | Observability/AgentLogGroup | CloudWatch log group for Fargate task logs, 30-day retention |

### Database (1 resource)

| # | Resource Type | CDK Path | Purpose |
|---|--------------|----------|---------|
| 52 | `AWS::DynamoDB::Table` | JobExecutor/JobsTable | Jobs table. PK: `jobId`. GSI: `tenantId-status-index`, `taskArn-index`. TTL: `expiresAt`. Stream: `NEW_AND_OLD_IMAGES`. PAY_PER_REQUEST billing. |

### Queues (6 resources)

| # | Resource Type | CDK Path | Purpose |
|---|--------------|----------|---------|
| 53 | `AWS::SQS::Queue` | JobIngestion/Queue-high | `jobs-high.fifo` — high priority, content-based dedup, 15min visibility timeout |
| 54 | `AWS::SQS::Queue` | JobIngestion/Queue-medium | `jobs-medium.fifo` — medium priority |
| 55 | `AWS::SQS::Queue` | JobIngestion/Queue-low | `jobs-low.fifo` — low priority |
| 56 | `AWS::SQS::Queue` | JobIngestion/DLQ-high | `jobs-high-dlq.fifo` — dead letters after 3 failed receives |
| 57 | `AWS::SQS::Queue` | JobIngestion/DLQ-medium | `jobs-medium-dlq.fifo` — dead letters |
| 58 | `AWS::SQS::Queue` | JobIngestion/DLQ-low | `jobs-low-dlq.fifo` — dead letters |

### API Gateway (8 resources)

| # | Resource Type | CDK Path | Purpose |
|---|--------------|----------|---------|
| 59 | `AWS::ApiGatewayV2::Api` | JobIngestion/Api | HTTP API named `cuseinfra-api` |
| 60 | `AWS::ApiGatewayV2::Stage` | JobIngestion/Api/DefaultStage | `$default` stage with auto-deploy |
| 61 | `AWS::ApiGatewayV2::Route` | JobIngestion/Api/POST--jobs | Route: `POST /jobs` |
| 62 | `AWS::ApiGatewayV2::Route` | JobIngestion/Api/GET--jobs--{id} | Route: `GET /jobs/{id}` |
| 63 | `AWS::ApiGatewayV2::Route` | JobIngestion/Api/GET--jobs--{id}--recording | Route: `GET /jobs/{id}/recording` |
| 64 | `AWS::ApiGatewayV2::Integration` | .../IngestIntegration | Lambda proxy integration → Ingest λ |
| 65 | `AWS::ApiGatewayV2::Integration` | .../GetJobIntegration | Lambda proxy integration → GetJob λ |
| 66 | `AWS::ApiGatewayV2::Integration` | .../PresignIntegration | Lambda proxy integration → Presign λ |

### Lambda Functions (8 resources)

| # | Resource Type | CDK Path | Purpose |
|---|--------------|----------|---------|
| 67 | `AWS::Lambda::Function` | JobIngestion/IngestFn | **Ingest** — validates input, writes to DDB, sends to SQS |
| 68 | `AWS::Lambda::Function` | JobIngestion/GetJobFn | **GetJob** — reads job from DDB, returns status + output |
| 69 | `AWS::Lambda::Function` | JobIngestion/PresignFn | **Presign** — lists S3 objects, generates pre-signed URLs |
| 70 | `AWS::Lambda::Function` | JobIngestion/WorkerFn | **Worker** — consumes SQS, checks rate limit, calls `ecs:RunTask` |
| 71 | `AWS::Lambda::Function` | JobIngestion/CompletionFn | **Completion** — handles ECS task stopped event, reads S3 output, updates DDB |
| 72 | `AWS::Lambda::Function` | Reaper/ReaperFn | **Reaper** — DDB Stream trigger, stops expired tasks |
| 73 | `AWS::Lambda::Function` | Reaper/SweepFn | **Sweep** — scheduled orphan cleanup every 10 min |
| 74 | `AWS::Lambda::Function` | Custom::S3AutoDeleteObjects | **CDK internal** — empties S3 bucket on stack deletion |

### Lambda Event Source Mappings (4 resources)

These wire triggers to Lambda functions.

| # | Resource Type | CDK Path | Purpose |
|---|--------------|----------|---------|
| 75 | `AWS::Lambda::EventSourceMapping` | WorkerFn/SqsEventSource:...high | Worker ← `jobs-high.fifo` (MaxConcurrency: **30**) |
| 76 | `AWS::Lambda::EventSourceMapping` | WorkerFn/SqsEventSource:...medium | Worker ← `jobs-medium.fifo` (MaxConcurrency: **15**) |
| 77 | `AWS::Lambda::EventSourceMapping` | WorkerFn/SqsEventSource:...low | Worker ← `jobs-low.fifo` (MaxConcurrency: **5**) |
| 78 | `AWS::Lambda::EventSourceMapping` | ReaperFn/DynamoDBEventSource:... | Reaper ← DynamoDB Stream (filter: REMOVE events only) |

### Lambda Permissions (5 resources)

Resource-based policies allowing services to invoke Lambda functions.

| # | Resource Type | CDK Path | Purpose |
|---|--------------|----------|---------|
| 79 | `AWS::Lambda::Permission` | JobIngestion/Api/POST--jobs | API Gateway → invoke Ingest λ |
| 80 | `AWS::Lambda::Permission` | JobIngestion/Api/GET--jobs--{id} | API Gateway → invoke GetJob λ |
| 81 | `AWS::Lambda::Permission` | JobIngestion/Api/GET--jobs--{id}--recording | API Gateway → invoke Presign λ |
| 82 | `AWS::Lambda::Permission` | JobIngestion/EcsTaskStoppedRule | EventBridge → invoke Completion λ |
| 83 | `AWS::Lambda::Permission` | Reaper/SweepSchedule | EventBridge → invoke Sweep λ |

### EventBridge Rules (2 resources)

| # | Resource Type | CDK Path | Purpose |
|---|--------------|----------|---------|
| 84 | `AWS::Events::Rule` | JobIngestion/EcsTaskStoppedRule | Matches `aws.ecs` / `ECS Task State Change` where `lastStatus=STOPPED` → triggers Completion λ |
| 85 | `AWS::Events::Rule` | Reaper/SweepSchedule | Runs every 10 minutes → triggers Sweep λ for orphan cleanup |

### IAM Roles (10 resources)

Every Lambda and ECS task gets a dedicated execution role.

| # | Resource Type | CDK Path | Purpose |
|---|--------------|----------|---------|
| 86 | `AWS::IAM::Role` | JobExecutor/TaskDef/ExecutionRole | ECS **execution** role — pull image from ECR, write to CloudWatch Logs |
| 87 | `AWS::IAM::Role` | JobExecutor/TaskRole | ECS **task** role — the role assumed by the running agent container |
| 88 | `AWS::IAM::Role` | JobIngestion/IngestFn/ServiceRole | Ingest Lambda execution role |
| 89 | `AWS::IAM::Role` | JobIngestion/GetJobFn/ServiceRole | GetJob Lambda execution role |
| 90 | `AWS::IAM::Role` | JobIngestion/PresignFn/ServiceRole | Presign Lambda execution role |
| 91 | `AWS::IAM::Role` | JobIngestion/WorkerFn/ServiceRole | Worker Lambda execution role |
| 92 | `AWS::IAM::Role` | JobIngestion/CompletionFn/ServiceRole | Completion Lambda execution role |
| 93 | `AWS::IAM::Role` | Reaper/ReaperFn/ServiceRole | Reaper Lambda execution role |
| 94 | `AWS::IAM::Role` | Reaper/SweepFn/ServiceRole | Sweep Lambda execution role |
| 95 | `AWS::IAM::Role` | Custom::S3AutoDeleteObjectsProvider | CDK custom resource Lambda role |

### IAM Policies (9 resources)

Each role gets a scoped policy with least-privilege permissions.

| # | Resource Type | CDK Path | Permissions Granted |
|---|--------------|----------|---------------------|
| 96 | `AWS::IAM::Policy` | JobExecutor/TaskDef/ExecutionRole/DefaultPolicy | `ecr:GetAuthorizationToken`, `ecr:BatchGetImage`, `logs:CreateLogStream`, `logs:PutLogEvents` |
| 97 | `AWS::IAM::Policy` | JobExecutor/TaskRole/DefaultPolicy | **`s3:PutObject` on `jobs/*` only** — the minimal agent permission |
| 98 | `AWS::IAM::Policy` | JobIngestion/IngestFn/ServiceRole/DefaultPolicy | `dynamodb:PutItem` + `sqs:SendMessage` on all 3 queues |
| 99 | `AWS::IAM::Policy` | JobIngestion/GetJobFn/ServiceRole/DefaultPolicy | `dynamodb:GetItem`, `dynamodb:Query` |
| 100 | `AWS::IAM::Policy` | JobIngestion/PresignFn/ServiceRole/DefaultPolicy | `dynamodb:GetItem`, `s3:GetObject`, `s3:ListBucket` |
| 101 | `AWS::IAM::Policy` | JobIngestion/WorkerFn/ServiceRole/DefaultPolicy | `dynamodb:*Item`, `dynamodb:Query`, `ecs:RunTask`, `iam:PassRole`, `sqs:ReceiveMessage/DeleteMessage` |
| 102 | `AWS::IAM::Policy` | JobIngestion/CompletionFn/ServiceRole/DefaultPolicy | `dynamodb:*Item`, `dynamodb:Query`, `s3:GetObject` |
| 103 | `AWS::IAM::Policy` | Reaper/ReaperFn/ServiceRole/DefaultPolicy | `dynamodb:*Item`, `dynamodb:GetRecords/GetShardIterator`, `ecs:StopTask` (cluster-scoped) |
| 104 | `AWS::IAM::Policy` | Reaper/SweepFn/ServiceRole/DefaultPolicy | `dynamodb:Scan`, `dynamodb:UpdateItem`, `ecs:StopTask`, `ecs:ListTasks`, `ecs:DescribeTasks` |

## Resource Summary by Category

| Category | Count | Key Resources |
|----------|-------|---------------|
| **VPC & Subnets** | 31 | 1 VPC, 6 subnets (3 tiers × 2 AZs), 6 route tables, 6 routes, 6 associations, 1 IGW, 1 NAT GW, 1 EIP, 1 SG, 1 VPC attachment |
| **NACLs** | 14 | 2 NACLs, 2 subnet associations, 10 NACL entries (5 rules × 2 AZs) |
| **Compute** | 3 | 1 ECS cluster, 1 task definition, 1 CDK metadata |
| **Storage** | 4 | 1 S3 bucket, 1 bucket policy, 1 auto-delete custom resource, 1 log group |
| **Database** | 1 | 1 DynamoDB table (with 2 GSIs, TTL, streams) |
| **Queues** | 6 | 3 FIFO queues + 3 dead-letter queues |
| **API Gateway** | 8 | 1 HTTP API, 1 stage, 3 routes, 3 integrations |
| **Lambda** | 8 | 7 application functions + 1 CDK custom resource |
| **Event Source Mappings** | 4 | 3 SQS → Worker, 1 DDB Stream → Reaper |
| **Lambda Permissions** | 5 | 3 API GW → Lambda, 2 EventBridge → Lambda |
| **EventBridge** | 2 | 1 ECS state change rule, 1 sweep schedule |
| **IAM Roles** | 10 | 2 ECS roles + 7 Lambda roles + 1 CDK custom resource role |
| **IAM Policies** | 9 | 1 per role (least-privilege, scoped to specific resources) |
| **Total** | **104** | |
