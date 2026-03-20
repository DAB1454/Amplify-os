# Amplify-OS

Agentic music marketing platform. Automates campaign planning, content creation, cross-platform publishing, analytics, and community management for independent artists and labels.

## Architecture

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
          │  Analytics      │  │                │  │  (auto + assisted)      │
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

## Deployment Modes

| Mode | Use Case | Auth | Tenancy |
|------|----------|------|---------|
| **Local** | Single artist on their own machine | None | `tenant_id=local` |
| **Cloud** | Multi-tenant SaaS for multiple artists | JWT + RBAC | Row-level isolation |

Set `DEPLOYMENT_MODE=local|cloud` in `.env`.

## Plan Tiers

| | Solo (Free) | Pro ($29/mo) | Agency ($99/mo) |
|---|---|---|---|
| Artists | 1 | 5 | Unlimited |
| Channels | 3 | Unlimited | Unlimited |
| Posts/month | 10 | 200 | Unlimited |
| AI generations | — | 500/mo | Unlimited |
| Media renders | 5/mo | 100/mo | Unlimited |
| API access | — | — | Yes |

## Prerequisites

- Python 3.12+
- Node.js 20+
- Docker & Docker Compose
- FFmpeg (optional, for media rendering)

## Quick Start

```bash
# 1. Clone and configure
git clone https://github.com/your-org/amplify-os.git && cd amplify-os
cp .env.example .env   # add your ANTHROPIC_API_KEY

# 2. Install
make setup

# 3. Start infrastructure
make docker-up

# 4. Database
make migrate
make seed              # demo data (Luna Vega artist)

# 5. Run
make dev
```

- Dashboard: http://localhost:3000
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

## Development

```bash
make test              # pytest + npm test
make lint              # ruff + mypy
make format            # ruff format + prettier
make migrate-new msg="add_foo_column"
make seed-drew         # seed Drew Baird artist data
make seed-artist       # seed demo artist (The Velvet Hooks)
```

## Local Node (single-artist 24/7 mode)

```bash
# Docker
make local-node

# Or bare metal
amplify-node start --api-url http://localhost:8000
amplify-node status
amplify-node capabilities
```

See [docs/local-node.md](docs/local-node.md) for systemd/launchd/Task Scheduler setup.

## Production Deployment

```bash
# Infrastructure
cd infra/terraform && terraform apply -var-file=envs/production.tfvars

# Deploy
bash infra/deploy/cloud/deploy.sh production

# Web
cd apps/web && npx vercel --prod
```

See [docs/saas-operations.md](docs/saas-operations.md) for the full SaaS operations guide.

## Project Structure

```
amplify-os/
├── apps/
│   ├── api/           FastAPI backend (18 route modules, services, middleware)
│   ├── worker/        Background jobs (publish, render, moderate, experiments)
│   ├── web/           Next.js 15 dashboard (12 pages + components)
│   └── local_node/    Local daemon (supervisor, offline queue, encrypted secrets)
├── packages/
│   ├── core/          Domain models, policies, workflows, analytics, notifications
│   ├── db/            SQLAlchemy models, Alembic migrations, repository pattern
│   ├── agents/        AI agent runtime (Claude client, tool registry, 5 subagents)
│   ├── adapters/      Platform integrations (6 platforms, auto + assisted modes)
│   ├── media/         Renderers (caption burn, waveform, lyric card, teaser, hooks)
│   ├── billing/       Plan tiers, metering, Stripe abstraction, enforcement
│   └── observability/ Structured logging, metrics, tracing, alerts
├── infra/
│   ├── terraform/     AWS VPC, RDS, ElastiCache, ECS, S3, CloudFront, ALB
│   ├── nginx/         Reverse proxy with rate limiting and TLS
│   └── deploy/        Environment configs, deploy scripts, docker-compose.prod
├── docs/              Architecture, runbooks, pricing, SaaS ops, local node guide
├── scripts/           Seed scripts, migration helpers
├── .github/workflows/ CI pipeline (lint, test, build, migrate, deploy)
└── .claude/           AI agents and slash commands
```

## Documentation

- [Architecture](docs/architecture.md) — System design and data flow
- [SaaS Operations](docs/saas-operations.md) — Running as a multi-artist service
- [Runbooks](docs/runbooks.md) — Deploy, backup/restore, incident response
- [Local Node](docs/local-node.md) — 24/7 single-artist deployment
- [Pricing](docs/pricing.md) — Plan tiers and limits
- [Tenant Model](docs/tenant-model.md) — Multi-tenancy implementation
- [Workflows](docs/workflows.md) — Campaign lifecycle
- [Adapters](docs/adapters.md) — Platform integration contracts
- [Examples](docs/examples.md) — Calendar import, release setup, campaign execution
- [Package Layout](docs/package-layout.md) — src layout, namespace packages, editable installs
