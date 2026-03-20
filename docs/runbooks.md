# Operational Runbooks

## Table of Contents

1. [Local Development Setup](#local-development-setup)
2. [Cloud Deployment](#cloud-deployment)
3. [Database Operations](#database-operations)
4. [Backup and Restore](#backup-and-restore)
5. [Incident Response](#incident-response)
6. [Troubleshooting](#troubleshooting)
7. [Rotating Secrets](#rotating-secrets)
8. [Monitoring](#monitoring)

---

## Local Development Setup

### Prerequisites

- Docker and Docker Compose
- Python 3.12+
- Node.js 20+
- Git
- ffmpeg (optional, for media rendering)

### Steps

```bash
# 1. Clone
git clone https://github.com/your-org/amplify-os.git
cd amplify-os

# 2. Environment
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY

# 3. Install
make setup

# 4. Start infra
docker compose up -d postgres redis

# 5. Migrate
make migrate

# 6. Seed (optional)
make seed         # generic data
make seed-drew    # Drew Baird artist profile

# 7. Run
make dev
# API: http://localhost:8000
# Web: http://localhost:3000
```

---

## Cloud Deployment

### First-Time Infrastructure Setup

```bash
# 1. Bootstrap Terraform state bucket
aws s3 mb s3://amplify-os-terraform-state --region us-east-1
aws dynamodb create-table \
  --table-name amplify-os-terraform-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST

# 2. Plan and apply infrastructure
cd infra/terraform
terraform init
terraform plan -var-file=envs/staging.tfvars
terraform apply -var-file=envs/staging.tfvars

# 3. Note outputs: db_endpoint, redis_endpoint, ecr_api_url, ecr_worker_url
terraform output
```

### Deploying Code

```bash
# Staging (automatic on merge to main via CI)
# Manual:
bash infra/deploy/cloud/deploy.sh staging

# Production (manual only)
bash infra/deploy/cloud/deploy.sh production

# Single service
bash infra/deploy/cloud/deploy.sh staging api
bash infra/deploy/cloud/deploy.sh staging worker
```

### Web Dashboard (Vercel)

```bash
cd apps/web
npx vercel --prod
# Set NEXT_PUBLIC_API_URL in Vercel project settings
```

### Running Migrations in Production

```bash
# Option 1: One-off ECS task
aws ecs run-task \
  --cluster amplify-production \
  --task-definition amplify-production-api \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx]}" \
  --overrides '{"containerOverrides":[{"name":"api","command":["alembic","upgrade","head"]}]}'

# Option 2: Exec into running task
aws ecs execute-command \
  --cluster amplify-production \
  --task <task-id> \
  --container api \
  --interactive \
  --command "alembic upgrade head"
```

---

## Database Operations

### Creating a Migration

```bash
make migrate-new msg="add_stripe_fields_to_tenant"
# Review the generated file in apps/api/alembic/versions/
# Test: make migrate
```

### Applying Migrations

```bash
# Local
make migrate

# Check current version
cd apps/api && alembic current

# Apply specific revision
cd apps/api && alembic upgrade <revision>
```

### Rolling Back

```bash
cd apps/api && alembic downgrade -1   # one step back
cd apps/api && alembic downgrade <rev> # specific revision
```

### Direct Database Access

```bash
# Local
docker compose exec postgres psql -U amplify -d amplify_os

# Production (via bastion or ECS exec)
aws ecs execute-command \
  --cluster amplify-production \
  --task <task-id> \
  --container api \
  --interactive \
  --command "python -c \"from app.config import Settings; print(Settings().database_url)\""
```

---

## Backup and Restore

### Automated Backups (RDS)

RDS automated backups are configured in Terraform:
- **Retention**: 7 days
- **Backup window**: 03:00–04:00 UTC
- **Point-in-time recovery**: Enabled by default on RDS

### Manual Database Backup

```bash
# Local
docker compose exec postgres pg_dump -U amplify -d amplify_os -Fc > backup_$(date +%Y%m%d_%H%M%S).dump

# Production (from bastion or local with RDS access)
pg_dump \
  -h amplify-prod.xxxx.rds.amazonaws.com \
  -U amplify -d amplify_os \
  -Fc \
  --no-owner \
  > amplify_prod_$(date +%Y%m%d_%H%M%S).dump

# Upload to S3
aws s3 cp amplify_prod_*.dump s3://amplify-prod-backups/database/
```

### Restore from Backup

```bash
# 1. Stop services that write to the database
aws ecs update-service --cluster amplify-production --service api --desired-count 0
aws ecs update-service --cluster amplify-production --service worker --desired-count 0

# 2. Wait for tasks to drain
aws ecs wait services-stable --cluster amplify-production --services api worker

# 3. Restore
pg_restore \
  -h amplify-prod.xxxx.rds.amazonaws.com \
  -U amplify -d amplify_os \
  --clean --if-exists \
  --no-owner \
  amplify_prod_20260318.dump

# 4. Restart services
aws ecs update-service --cluster amplify-production --service api --desired-count 2
aws ecs update-service --cluster amplify-production --service worker --desired-count 2

# 5. Verify
curl -f https://api.amplify-os.com/health
```

### Point-in-Time Recovery (RDS)

```bash
# Restore to a new instance from a specific timestamp
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier amplify-production \
  --target-db-instance-identifier amplify-production-pitr \
  --restore-time "2026-03-18T10:00:00Z" \
  --db-instance-class db.t4g.medium

# After verification, swap DNS or update DATABASE_URL
```

### Redis Backup

```bash
# Redis data is ephemeral (job queue + cache). No backup needed.
# If Redis dies, the worker re-processes jobs from the database.
# In-flight jobs retry automatically on next worker restart.
```

### Media Assets Backup

```bash
# S3 versioning is enabled — objects are never permanently deleted.
# Cross-region replication (add to Terraform if needed):
aws s3 sync s3://amplify-prod-assets s3://amplify-prod-assets-backup-us-west-2
```

### Backup Verification Checklist

Run monthly or after any backup/restore procedure:

- [ ] Download latest RDS automated snapshot and restore to a test instance
- [ ] Run `alembic current` on restored DB — migrations match
- [ ] Run a test API query against restored DB — data is intact
- [ ] Verify S3 versioning is active: `aws s3api get-bucket-versioning --bucket amplify-prod-assets`
- [ ] Confirm backup retention > 7 days in RDS console
- [ ] Delete test instance after verification

---

## Incident Response

### Severity Levels

| Level | Definition | Response Time | Examples |
|-------|-----------|---------------|----------|
| **SEV-1** | Service down, data loss risk | 15 min | API unreachable, DB corruption, security breach |
| **SEV-2** | Major feature broken | 1 hour | Publishing failing, billing errors, auth broken |
| **SEV-3** | Degraded performance | 4 hours | Slow API, worker backlog, adapter timeouts |
| **SEV-4** | Minor issue | 24 hours | UI bug, wrong metric count, cosmetic error |

### SEV-1: Complete Service Outage

```
1. ACKNOWLEDGE
   - Check #amplify-incidents (or your alerting channel)
   - Post: "Investigating: [symptom]. ETA for update: 15 min."

2. TRIAGE
   - curl https://api.amplify-os.com/health
   - Check ECS service events:
     aws ecs describe-services --cluster amplify-production --services api worker
   - Check RDS status:
     aws rds describe-db-instances --db-instance-identifier amplify-production
   - Check CloudWatch:
     aws logs tail /ecs/amplify-production-api --since 10m

3. COMMON FIXES
   - ECS tasks crashing → check logs, force redeploy:
     aws ecs update-service --cluster amplify-production --service api --force-new-deployment
   - DB connection exhausted → restart API service (increases pool)
   - Bad deploy → roll back:
     aws ecs update-service --cluster amplify-production --service api \
       --task-definition amplify-production-api:<previous-revision>
   - RDS down → check AWS Health Dashboard, failover if Multi-AZ

4. RESOLVE
   - Verify health endpoint returns 200
   - Check worker is processing jobs (Redis queue depth decreasing)
   - Post resolution update with root cause

5. POST-MORTEM
   - Write incident report within 48 hours
   - Include: timeline, root cause, impact (users affected, duration), remediation
   - Create follow-up tickets for preventive measures
```

### SEV-2: Publishing Pipeline Failure

```
1. CHECK adapter health
   - API: GET /api/v1/channels (look for connection_status != "connected")
   - Worker logs: aws logs tail /ecs/amplify-production-worker --since 30m --filter "ERROR"

2. COMMON CAUSES
   - Platform token expired → user must re-authorize
   - Platform API rate limited → check RateLimitError in logs
   - Platform API down → check platform status page
   - Policy engine blocking → check approval queue

3. MITIGATION
   - If platform-wide: pause scheduled posts for that platform
   - If rate limited: reduce worker concurrency
   - If token issue: notify affected users to re-connect
```

### SEV-2: Billing / Stripe Failure

```
1. CHECK Stripe dashboard (dashboard.stripe.com)
   - Webhook delivery status
   - Recent failed payments

2. VERIFY webhook endpoint
   - curl -X POST https://api.amplify-os.com/api/v1/webhooks/stripe \
       -H "Content-Type: application/json" -d '{"type":"test"}'
   - Check API logs for webhook handler errors

3. COMMON FIXES
   - Webhook secret mismatch → update STRIPE_WEBHOOK_SECRET
   - Endpoint down → redeploy API
   - Payment method failures → Stripe handles retry automatically
```

### SEV-3: Worker Backlog

```
1. CHECK queue depth
   - redis-cli -h <redis-host> LLEN amplify:jobs

2. IF growing
   - Scale workers: aws ecs update-service --cluster amplify-production \
       --service worker --desired-count 3
   - Check for stuck jobs: look for long-running tasks in logs

3. IF stuck
   - Identify and remove poison jobs from queue
   - Restart worker service
```

### Data Breach Response

```
1. CONTAIN
   - Rotate all secrets immediately (JWT, API keys, DB password)
   - Revoke all OAuth tokens
   - Block compromised IP if known

2. ASSESS
   - Determine scope: which tenants, what data
   - Check audit log: GET /api/v1/admin/audit-log

3. NOTIFY
   - Affected users within 72 hours (GDPR requirement if applicable)
   - Legal team

4. REMEDIATE
   - Patch vulnerability
   - Force password reset for affected users
   - Review and tighten security groups
```

---

## Troubleshooting

### API Won't Start

- Check `DATABASE_URL` is set and reachable
- Verify migrations are current: `alembic current`
- Check port 8000 isn't in use: `lsof -i :8000`
- Check logs: `docker compose logs api --tail 50`

### Worker Tasks Not Processing

- Verify Redis is running: `redis-cli ping`
- Check worker logs for connection errors
- Ensure `REDIS_URL` matches the running Redis instance
- Check queue depth: `redis-cli LLEN amplify:jobs`

### OAuth Callback Failures

- Verify redirect URIs match exactly (including trailing slashes)
- Check that client ID and secret are correct in `.env`
- Ensure the platform app is in the correct mode (dev vs. production)

### Posts Not Publishing

- Check adapter connection status in the dashboard
- Verify tokens haven't expired (look for 401 errors in worker logs)
- Check rate-limit counters in Redis
- Check policy engine — post may need approval

### Tenant Can't Access Data

- Verify tenant middleware is injecting correct tenant_id
- Check JWT token contains tenant_id claim
- Verify the resource's tenant_id matches

---

## Rotating Secrets

### JWT Secret

```bash
# 1. Generate new secret
openssl rand -hex 32

# 2. Update in environment / Secrets Manager
# 3. Deploy API (all existing sessions will be invalidated)
# 4. Users must log in again
```

### Database Password

```bash
# 1. Update in RDS
aws rds modify-db-instance \
  --db-instance-identifier amplify-production \
  --master-user-password "NEW_PASSWORD"

# 2. Update DATABASE_URL in environment
# 3. Restart API and worker services
```

### Platform OAuth Credentials

1. Generate new credentials in the platform's developer portal
2. Update in `.env` (local) or Secrets Manager (cloud)
3. Restart API and worker services
4. Users must re-authorize if client ID changed

### Stripe Keys

1. Roll keys in Stripe Dashboard (Settings → API keys → Roll key)
2. Update `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`
3. Restart API service
4. Verify webhook delivery in Stripe Dashboard

### AI API Keys

1. Generate new key in the provider dashboard
2. Revoke the old key
3. Update `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`
4. Restart worker service

---

## Monitoring

### Health Checks

| Service | Endpoint | Expected |
|---------|----------|----------|
| API | `GET /health` | `200 {"status": "ok"}` |
| Web | `GET /` | `200` |
| Local Node | `GET :8100/health` | `200 {"status": "ok"}` |
| Local Node | `GET :8100/ready` | `200 {"ready": true}` |

### Key Metrics

- **API**: Response times (p50, p95, p99), error rate, active connections
- **Worker**: Queue depth, processing latency, job failure rate
- **Database**: Connection pool utilization, query latency, disk usage
- **Redis**: Memory usage, connected clients, evicted keys
- **Adapters**: Error rate per platform, rate limit proximity
- **Billing**: Failed payments, MRR, churn events

### CloudWatch Alarms (set up in Terraform or manually)

| Alarm | Threshold | Action |
|-------|-----------|--------|
| API 5xx rate | > 5% for 5 min | Page on-call |
| API latency p99 | > 3s for 10 min | Notify Slack |
| Worker queue depth | > 100 for 15 min | Auto-scale workers |
| RDS CPU | > 80% for 10 min | Notify Slack |
| RDS free storage | < 5 GB | Page on-call |
| Redis memory | > 80% | Notify Slack |
| ECS task count | < desired for 5 min | Page on-call |

---

## Intelligence Pipeline Operations

### Job Schedule Reference

| Job | Frequency | Queue Name | Timeout |
|-----|-----------|------------|---------|
| Feature Extraction | Every 24h | `extract_features` | 5 min |
| Reward Computation | Every 4h | `compute_rewards` | 5 min |
| Tenant Pattern Update | Every 24h | `update_tenant_patterns` | 5 min |
| Cohort Aggregation | Weekly | `aggregate_cohorts` | 5 min |
| Model Health Check | Every 6h | `check_model_health` | 5 min |
| Evaluation Run | On-demand | `run_evaluation` | 5 min |

### Backfill Features for Historical Posts

When: Posts exist without feature vectors (e.g., after initial deployment or schema change).

```bash
# Via queue admin API — enqueue with extended lookback
curl -X POST http://localhost:8000/api/v1/queue/enqueue \
  -H "Content-Type: application/json" \
  -d '{"job_type": "extract_features", "payload": {"hours_back": 720}}'

# For a specific tenant
curl -X POST http://localhost:8000/api/v1/queue/enqueue \
  -H "Content-Type: application/json" \
  -d '{"job_type": "extract_features", "payload": {"tenant_id": "UUID", "hours_back": 720}}'
```

Note: The batch cap is 500 posts per run. For large backfills, run multiple times until the count returns 0.

### Backfill Rewards

When: Outcomes exist without computed rewards (e.g., after RewardCalculator logic changes).

```bash
curl -X POST http://localhost:8000/api/v1/queue/enqueue \
  -H "Content-Type: application/json" \
  -d '{"job_type": "compute_rewards", "payload": {}}'
```

### Force Tenant Pattern Recompute

When: Pattern logic changed, or patterns look stale for a specific tenant.

```bash
# Single tenant
curl -X POST http://localhost:8000/api/v1/queue/enqueue \
  -H "Content-Type: application/json" \
  -d '{"job_type": "update_tenant_patterns", "payload": {"tenant_id": "UUID"}}'

# All tenants (scheduled job does this automatically)
curl -X POST http://localhost:8000/api/v1/queue/enqueue \
  -H "Content-Type: application/json" \
  -d '{"job_type": "update_tenant_patterns", "payload": {}}'
```

### Rollback Intelligence Changes

If a deployment causes evaluation regression or bad patterns:

1. **Check health dashboard**: `GET /api/v1/intelligence/alerts`
2. **Compare evaluation runs**: `GET /api/v1/intelligence/evaluations` — look for MAE spike
3. **Roll back the worker deployment**:
   ```bash
   # Revert to previous worker image
   aws ecs update-service --cluster amplify-staging --service worker \
     --task-definition amplify-worker:<previous-revision>
   ```
4. **Re-run patterns** after rollback to overwrite bad patterns:
   ```bash
   curl -X POST http://localhost:8000/api/v1/queue/enqueue \
     -d '{"job_type": "update_tenant_patterns", "payload": {}}'
   ```
5. **Re-run cohort aggregation** if global priors were affected:
   ```bash
   curl -X POST http://localhost:8000/api/v1/queue/enqueue \
     -d '{"job_type": "aggregate_cohorts", "payload": {"days_back": 30}}'
   ```

### Stuck Intelligence Jobs

Symptoms: Job enqueued but never completes. Check DLQ.

```bash
# Check queue health
curl http://localhost:8000/api/v1/queue/health

# Peek at dead-letter queue
curl http://localhost:8000/api/v1/queue/dlq/peek?limit=10

# Manually recover stuck jobs
curl -X POST http://localhost:8000/api/v1/queue/recover-stuck
```

Common causes:
- Database connection timeout — check RDS connection count
- Feature extractor import error — check worker logs for `FeatureExtractor` import failures
- RewardCalculator division by zero — check for outcomes with all-zero metrics

### Low-Confidence Tenant

When: Health check alerts `high_low_confidence_ratio` (>50% patterns below 0.3 confidence).

1. Check tenant data volume: `GET /api/v1/intelligence/overview` — look at `feature_vectors` count
2. If data is sparse (<20 observations), this is expected — blending engine will lean on global priors
3. If data is sufficient but confidence is low:
   - Check for data quality issues: `GET /api/v1/intelligence/posts/{post_id}` on recent posts
   - Look for conflicting signals in reward trends: `GET /api/v1/intelligence/reward-trends`
   - Consider whether feature extraction is capturing relevant signals

### Privacy Incident — Tenant Data Leak in Global Priors

If global priors accidentally contain tenant-identifiable data:

1. **Immediately disable cohort aggregation** — remove from scheduler or set interval to 0
2. **Clear global priors table**:
   ```sql
   -- Audit first
   SELECT * FROM global_pattern_stats ORDER BY computed_at DESC LIMIT 50;
   -- Clear
   DELETE FROM global_pattern_stats;
   ```
3. **Verify k-anonymity threshold** in `GlobalPriorService` — default is `min_tenant_count=3`
4. **Re-run aggregation** with verified de-identification:
   ```bash
   curl -X POST http://localhost:8000/api/v1/queue/enqueue \
     -d '{"job_type": "aggregate_cohorts", "payload": {"days_back": 30}}'
   ```
5. **Audit the priors**: `GET /api/v1/intelligence/global-priors?min_tenant_count=1` — verify no single-tenant entries

### Intelligence Metrics to Monitor

| Metric | What to Watch | Alert Threshold |
|--------|--------------|-----------------|
| `intelligence.features.extracted` | Should be >0 on nightly runs | 0 for 2 consecutive runs |
| `intelligence.features.errors` | Extraction failures | >10% of pending |
| `intelligence.rewards.pending` | Backlog of unscored outcomes | >100 sustained |
| `intelligence.health.alert_count` | Pipeline health alerts | >0 |
| `intelligence.evaluation.mae` | Model accuracy | >20% regression vs previous |
| `intelligence.patterns.errors` | Pattern update failures | >0 |
