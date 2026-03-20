# Operating Amplify-OS as a SaaS

This guide covers how to run Amplify-OS as a fee-for-service platform for multiple artists, from initial setup through day-to-day operations.

## Architecture for Multi-Tenant SaaS

```
                    ┌─────────────┐
                    │  CloudFront │
                    │    (CDN)    │
                    └──────┬──────┘
                           │
              ┌────────────┴────────────┐
              │                         │
     ┌────────┴────────┐    ┌──────────┴──────────┐
     │  app.amplify-os │    │  api.amplify-os.com  │
     │     (Vercel)    │    │       (ALB)          │
     └────────┬────────┘    └──────────┬───────────┘
              │                        │
              │              ┌─────────┴──────────┐
              │              │   ECS Fargate       │
              │              │  ┌───────┐ ┌──────┐ │
              │              │  │  API  │ │Worker│ │
              │              │  │  x2   │ │  x2  │ │
              │              │  └───┬───┘ └──┬───┘ │
              │              └─────┼─────────┼─────┘
              │                    │         │
              │              ┌─────┴─────────┴─────┐
              │              │  Private Subnet      │
              │              │  ┌──────┐ ┌───────┐  │
              │              │  │ RDS  │ │ Redis │  │
              │              │  │PG 16 │ │  7.1  │  │
              │              │  └──────┘ └───────┘  │
              │              └─────────────────────┘
              │
     ┌────────┴──────────────────────────────┐
     │        Artist Local Nodes             │
     │  ┌──────────┐  ┌──────────┐           │
     │  │ Artist A │  │ Artist B │  . . .    │
     │  │  (home)  │  │ (studio) │           │
     │  └──────────┘  └──────────┘           │
     └───────────────────────────────────────┘
```

Every artist (tenant) gets:
- Isolated data (row-level `tenant_id` on every table)
- Their own plan tier and usage limits
- Independent OAuth connections to social platforms
- Optional local node for media rendering and browser automation

## Deployment Checklist

### 1. Infrastructure

```bash
# Provision AWS infrastructure
cd infra/terraform
terraform init
terraform apply -var-file=envs/production.tfvars

# Note the outputs:
# - db_endpoint: RDS connection string
# - redis_endpoint: ElastiCache address
# - ecr_api_url: Docker image registry for API
# - ecr_worker_url: Docker image registry for worker
# - api_alb_dns: ALB domain for API
# - cdn_domain: CloudFront domain for media assets
```

### 2. DNS

| Record | Type | Value |
|--------|------|-------|
| `api.amplify-os.com` | CNAME | ALB DNS from Terraform output |
| `app.amplify-os.com` | CNAME | Vercel deployment URL |
| `amplify-os.com` | A/ALIAS | Vercel or redirect to `app.` |

### 3. Secrets

Store in AWS Secrets Manager (or inject via CI):

```
DATABASE_URL          → RDS endpoint from Terraform
REDIS_URL             → ElastiCache endpoint from Terraform
JWT_SECRET            → openssl rand -hex 32
STRIPE_SECRET_KEY     → From Stripe Dashboard
STRIPE_WEBHOOK_SECRET → From Stripe webhook setup
ANTHROPIC_API_KEY     → From Anthropic Console
Platform OAuth creds  → From each platform's developer portal
```

### 4. Initial Deploy

```bash
# Deploy backend
bash infra/deploy/cloud/deploy.sh production

# Run initial migration
aws ecs run-task \
  --cluster amplify-production \
  --task-definition amplify-production-api \
  --overrides '{"containerOverrides":[{"name":"api","command":["alembic","upgrade","head"]}]}' \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={...}"

# Deploy frontend
cd apps/web && npx vercel --prod
```

### 5. Stripe Setup

1. Create products and prices in Stripe matching your plan tiers:
   - Product: "Amplify Pro" — Price: $29/month, $290/year
   - Product: "Amplify Agency" — Price: $99/month, $990/year
2. Set up webhook endpoint: `https://api.amplify-os.com/api/v1/webhooks/stripe`
3. Subscribe to events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`

## Onboarding a New Artist

### Self-Service (default)

1. Artist visits `app.amplify-os.com` and signs up
2. System creates a new tenant with `plan_tier: solo`
3. Onboarding wizard guides them through:
   - Create artist profile
   - Connect a social channel
   - Add a release
   - Create a campaign
   - Choose a plan (upgrade from solo)

### Manual Onboarding (for managed artists)

```bash
# Via admin console: app.amplify-os.com/admin
# Or via API:

# 1. Create tenant
curl -X POST https://api.amplify-os.com/api/v1/tenants \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Artist Name",
    "slug": "artist-name",
    "plan_tier": "pro"
  }'

# 2. Create user and associate with tenant
# 3. Set up billing (create Stripe customer, attach payment method)
```

### Providing a Local Node

For artists who need media rendering or browser automation:

```bash
# On the artist's machine:
docker compose --profile local-node up -d

# Or install directly:
pip install -e apps/local_node
amplify-node start --api-url https://api.amplify-os.com --tenant-id <tenant-id>
```

## Day-to-Day Operations

### Admin Console

Access at `app.amplify-os.com/admin` (requires admin role).

Capabilities:
- **Tenant list**: View all tenants with plan tier, status, and usage summary
- **Plan management**: Upgrade/downgrade any tenant's plan
- **Enable/disable tenants**: Suspend accounts without deleting data
- **Global templates**: Promote campaign templates to be visible to all tenants
- **Platform stats**: Aggregate metrics across all tenants
- **Audit log**: Cross-tenant activity history

### Billing Operations

**Viewing usage:**
```
GET /api/v1/admin/tenants/<id>
# Returns: plan_tier, usage counts, subscription status
```

**Changing a plan:**
```
POST /api/v1/admin/tenants/<id>/change-plan
{"plan_tier": "agency"}
```

**Handling failed payments:**
- Stripe sends `invoice.payment_failed` webhook
- System should downgrade to solo after grace period (implement in webhook handler)
- Admin can manually override via admin console

### Capacity Planning

| Metric | Solo (100 users) | Growth (500 users) | Scale (2000 users) |
|--------|-------------------|---------------------|---------------------|
| API instances | 2 | 3 | 5 |
| Worker instances | 1 | 2 | 4 |
| RDS | db.t4g.medium | db.r6g.large | db.r6g.xlarge |
| Redis | cache.t4g.micro | cache.t4g.small | cache.r6g.large |
| Est. monthly AWS | ~$150 | ~$400 | ~$1,200 |

### Scaling Triggers

- **API latency p99 > 2s**: Add API task or scale up instance
- **Worker queue depth > 50 sustained**: Add worker task
- **RDS CPU > 70% sustained**: Scale up instance class
- **Redis memory > 70%**: Scale up node type or add eviction

## Revenue and Cost Model

### Per-Tenant Economics

| Tier | Monthly Revenue | Est. Infra Cost/User | Margin |
|------|-----------------|----------------------|--------|
| Solo | $0 | ~$0.50 | -$0.50 |
| Pro | $29 | ~$2.00 | ~$27.00 |
| Agency | $99 | ~$5.00 | ~$94.00 |

Infrastructure cost per user decreases with scale. Solo users subsidized by paid tiers.

### Key Business Metrics to Track

- **MRR** (Monthly Recurring Revenue): Sum of all active subscriptions
- **Churn rate**: % of paid users canceling per month
- **ARPU** (Average Revenue Per User): MRR / total paying users
- **CAC** (Customer Acquisition Cost): Marketing spend / new signups
- **LTV** (Lifetime Value): ARPU / monthly churn rate

### Usage-Based Cost Drivers

- **AI generations**: ~$0.01–0.05 per generation (Claude API)
- **Media renders**: ~$0.001 per render (compute time)
- **Storage**: ~$0.023/GB/month (S3)
- **Bandwidth**: ~$0.09/GB (CloudFront)

Monitor per-tenant AI and render usage to ensure plan limits are set correctly.

## Tenant Isolation Guarantees

1. **Database**: Every query is scoped by `tenant_id` via middleware. No cross-tenant data access is possible through the API.
2. **Authentication**: JWT tokens contain the tenant_id claim. Tokens from one tenant cannot access another's data.
3. **Billing**: Usage metering is keyed by tenant_id. One tenant's usage never affects another's limits.
4. **Worker**: Jobs include tenant_id. Worker processes verify tenant context before executing.
5. **Admin routes**: Only users with the `admin` role can access cross-tenant endpoints. Admin actions are audit-logged.

## Compliance Considerations

### Data Handling
- Tenant data is stored in a shared database with row-level isolation
- Secrets (OAuth tokens) are encrypted at rest
- All API traffic should use HTTPS (enforced by ALB/nginx)
- No PII in application logs (structured logging strips sensitive fields)

### Data Deletion
- When a tenant requests account deletion:
  1. Revoke all OAuth tokens
  2. Delete all rows with matching tenant_id
  3. Remove media assets from S3
  4. Cancel Stripe subscription
  5. Retain audit log entries for 90 days

### GDPR / Privacy
- Users can export their data via API
- Right to deletion: see above
- Cookie consent: handled by frontend (if needed)
- Data processing agreement: provide to enterprise customers

## Support Playbook

### Tier 1 (Self-Service)
- Knowledge base / FAQ
- Onboarding wizard in-app
- Community forum or Discord

### Tier 2 (Email — Pro plan)
- Target response time: 24 hours
- Common issues: OAuth re-authorization, plan limits, scheduling questions

### Tier 3 (Priority — Agency plan)
- Target response time: 4 hours
- Direct access to engineering for integration issues
- Custom onboarding assistance

### Escalation Path
1. User contacts support (email / in-app)
2. Support checks admin console for tenant status and usage
3. If technical: check API logs, worker logs, adapter status
4. If billing: check Stripe dashboard
5. If infrastructure: escalate to on-call engineer
