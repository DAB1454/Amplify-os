# Amplify-OS Architecture

## System Diagram

```
                              ┌──────────────────────┐
                              │     apps/web         │
                              │   Next.js 15 + TW    │
                              │   :3000              │
                              └──────────┬───────────┘
                                         │ HTTP
              ┌──────────────────────────┼──────────────────────────┐
              │                          │                          │
┌─────────────┴──────────┐  ┌───────────┴───────────┐  ┌──────────┴──────────┐
│   apps/local_node      │  │      apps/api         │  │    apps/worker      │
│  Supervisor + Worker   │  │     FastAPI           │  │  Scheduler + Jobs   │
│  FFmpeg + Playwright   │──│     :8000             │──│  Publish, Render,   │
│  Offline Queue         │  │  18 route modules     │  │  Metrics, Moderate  │
│  Encrypted Secrets     │  │  JWT + RBAC           │  │  Experiments        │
│  :8100 health          │  │  Tenant Middleware    │  │                     │
└────────────────────────┘  └───────────┬───────────┘  └──────────┬──────────┘
                                        │                         │
                   ┌────────────────────┼─────────────────────────┤
                   │                    │                         │
          ┌────────┴────────┐  ┌───────┴────────┐  ┌────────────┴────────────┐
          │  packages/core  │  │  packages/db   │  │  packages/adapters      │
          │  Domain models  │  │  SQLAlchemy    │  │  Instagram, TikTok,     │
          │  Policies       │  │  Alembic       │  │  YouTube, Bandcamp,     │
          │  Workflows      │  │  Repository    │  │  Linktree, Email        │
          │  Analytics      │  │  TenantMixin   │  │  (auto + assisted)      │
          └─────────────────┘  └───────┬────────┘  └─────────────────────────┘
                                       │
          ┌────────────────────────────┼────────────────────────────┐
          │                            │                            │
  ┌───────┴────────┐  ┌──────────────┴──────────────┐  ┌──────────┴────────┐
  │  PostgreSQL 16 │  │  Redis 7                     │  │  S3 / Local FS    │
  │  (tenant rows) │  │  (jobs + cache)              │  │  (media assets)   │
  └────────────────┘  └─────────────────────────────┘  └───────────────────┘
```

Additional packages: `packages/agents` (AI runtime), `packages/media` (renderers), `packages/billing` (plans + Stripe), `packages/observability` (logging + metrics + tracing).

## Components

### Web App (`apps/web`)
Next.js 15 dashboard with Tailwind CSS. Pages: dashboard, artists, releases, campaigns, channels, calendar, posts, approvals, analytics, billing, admin, settings. Communicates with the API via REST.

### API (`apps/api`)
FastAPI application with 18 route modules, service layer, and middleware. TenantMiddleware injects `tenant_id` into every request. RBAC middleware enforces role-based permissions. Supports both local (no auth) and cloud (JWT) modes.

### Worker (`apps/worker`)
Background job processor with 10 job types: publish posts, render media, generate AI content, moderate comments, sync metrics, run experiments, send alerts, scan scheduled posts, ingest calendar data, and weekly analyst reports.

### Local Node (`apps/local_node`)
Self-contained daemon for single-artist deployments. Process supervisor with auto-restart, offline SQLite queue for API outages, Fernet-encrypted secret store, FFmpeg media rendering, Playwright browser automation, and HTTP health endpoint.

### PostgreSQL 16
Primary data store with row-level tenant isolation via `tenant_id` on every table. Managed via Alembic migrations. Automated backups with 7-day retention (RDS).

### Redis 7
Job queue (BLPOP-based), session cache, and rate-limit state store.

### Platform Adapters (`packages/adapters`)
Six platform integrations in two modes:
- **Automatic**: Instagram, TikTok, YouTube, Email — full API-based publish/metrics
- **Assisted**: Bandcamp, Linktree — task-based workflows with checklists and URL validation

### AI Engine (`packages/agents`)
Claude-based agent runtime with tool registry, memory management, and five specialized subagents: planner, content writer, publisher, analyst, and community manager.

### Media Renderer (`packages/media`)
Five renderers: caption burn (FFmpeg), waveform video, lyric card (Pillow), hook variant extraction, and teaser video. Content-addressed cache for deduplication.

### Billing (`packages/billing`)
Three-tier plan system (solo/pro/agency) with PlanLimits enforcement, in-memory metering, Stripe-compatible payment abstraction (StubPaymentProvider for dev), and SubscriptionEnforcer.

## Package Dependency Graph

```
                    ┌──────────────┐
                    │ packages/core│
                    │  Domain      │
                    │  Policies    │
                    │  Workflows   │
                    │  Analytics   │
                    └──────┬───────┘
                           │
          ┌────────────────┼────────────────────┐
          │                │                    │
  ┌───────┴───────┐ ┌─────┴──────┐  ┌─────────┴─────────┐
  │ packages/db   │ │ packages/  │  │ packages/adapters  │
  │ ORM + Alembic │ │ billing    │  │ 6 platforms        │
  │ Repository    │ │ Plans      │  │ Auto + Assisted    │
  │ TenantMixin   │ │ Metering   │  │ Token management   │
  └───────────────┘ │ Stripe     │  └────────────────────┘
                    │ Enforcement │
                    └────────────┘
          ┌─────────────────┐  ┌────────────────────┐
          │ packages/agents │  │ packages/media      │
          │ Claude client   │  │ 5 renderers         │
          │ Tool registry   │  │ Content-addressed   │
          │ 5 subagents     │  │ cache               │
          └─────────────────┘  └────────────────────┘
                    ┌──────────────────────┐
                    │ packages/observability│
                    │ Structlog · Metrics   │
                    │ Tracing · Alerts      │
                    └──────────────────────┘
```

## Request Flow

```
Browser ──POST /api/v1/posts──▶ Nginx (rate limit)
                                   │
                                   ▼
                              TenantMiddleware
                              (extract tenant_id from JWT)
                                   │
                                   ▼
                              RBAC Middleware
                              (check role permissions)
                                   │
                                   ▼
                              Route Handler
                              (validate request, call service)
                                   │
                                   ▼
                              Policy Engine
                              (anti-spam, brand safety,
                               rate limits, hours check)
                                   │
                            ┌──────┴───────┐
                            │              │
                         ALLOWED      REQUIRE_APPROVAL
                            │              │
                            ▼              ▼
                      Publish Job    Approval Queue
                      (Redis)        (DB + notification)
                            │
                            ▼
                      Worker picks up
                            │
                            ▼
                      Platform Adapter
                      (Instagram/TikTok/YouTube)
                            │
                         ┌──┴──┐
                     SUCCESS  FAIL
                         │      │
                         ▼      ▼
                    Log metric  Retry with
                    Update post backoff
```

## Multi-Tenancy

```
┌─────────────────────────────────────────────────┐
│                  API Request                     │
│  Authorization: Bearer <JWT>                     │
│  JWT: { tenant_id: "abc123", role: "owner" }     │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│            TenantMiddleware                      │
│                                                  │
│  if DEPLOYMENT_MODE == "local":                  │
│      tenant_id = "local"                         │
│  else:                                           │
│      tenant_id = jwt.decode(token).tenant_id     │
│                                                  │
│  request.state.tenant_id = tenant_id             │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│           Database Queries                       │
│                                                  │
│  SELECT * FROM posts                             │
│  WHERE tenant_id = :tenant_id  ← always scoped   │
│  AND ...                                         │
└─────────────────────────────────────────────────┘
```

## Billing & Enforcement

```
┌───────────────────────────────────────────────┐
│  User Action: "Create 6th artist"             │
└──────────────────┬────────────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────────────┐
│  SubscriptionEnforcer.can_add_artist()        │
│                                               │
│  1. Look up tenant plan_tier → "pro"          │
│  2. Get PlanLimits for "pro"  → artists: 5    │
│  3. Count existing artists    → 5             │
│  4. 5 >= 5 → DENIED                          │
│                                               │
│  Return: EnforcementResult(                   │
│    allowed=False, current=5, limit=5,         │
│    message="Upgrade to Agency"                │
│  )                                            │
└───────────────────────────────────────────────┘
```

## Deployment Modes

| Mode | Use Case | Auth | Tenancy | Stack |
|------|----------|------|---------|-------|
| **Local** | Single artist | None | `tenant_id=local` | Docker Compose |
| **Cloud** | Multi-tenant SaaS | JWT + RBAC | Row-level isolation | AWS ECS, RDS, ElastiCache, Vercel |

## Data Flow

1. **Campaign creation** — Artist creates or system auto-generates a campaign tied to a release
2. **Content generation** — AI agent produces post copy, hashtags, and media variants
3. **Approval** — Policy engine evaluates content; high-risk items go to approval queue
4. **Publishing** — Scheduler triggers adapters to publish at optimal times
5. **Metrics collection** — Worker polls platform APIs and aggregates engagement data
6. **Experiment loop** — A/B test variants scored on engagement; winners auto-promoted
