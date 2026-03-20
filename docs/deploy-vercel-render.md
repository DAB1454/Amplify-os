# Amplify OS — Vercel + Render Deployment Plan

## Architecture

```
                    ┌─────────────────────────┐
                    │       Vercel (Free)      │
                    │   Next.js Dashboard      │
                    │   amplify-os.vercel.app  │
                    └──────────┬──────────────┘
                               │ NEXT_PUBLIC_API_URL
                               ▼
                    ┌─────────────────────────┐
                    │    Render Web Service    │
                    │   FastAPI + Uvicorn      │
                    │  api-amplify.onrender.com│
                    └──────┬──────────┬───────┘
                           │          │
                ┌──────────▼──┐  ┌────▼────────────┐
                │   Render    │  │   Render Redis   │
                │  PostgreSQL │  │   (cache+queue)  │
                │   (Free)    │  │     (Free)       │
                └──────┬──────┘  └────┬────────────┘
                       │              │
                    ┌──▼──────────────▼───────┐
                    │  Render Background Worker│
                    │  Job consumer + scheduler│
                    └─────────────────────────┘

                    ┌─────────────────────────┐
                    │   Local Node (future)    │
                    │   Optional premium       │
                    │   Artist's own machine   │
                    └─────────────────────────┘
```

**Single control plane** — one Postgres database, one Redis instance, one API, one worker.
All tenants (artists) share the same infrastructure. Tenant isolation is handled by the JWT + TenantMiddleware in the API layer.

---

## Prerequisites

- GitHub repo (push the monorepo)
- Vercel account (free tier)
- Render account (free tier to start, Starter for production)
- Domain (optional — can use `.vercel.app` + `.onrender.com` subdomains for beta)

---

## Phase 1: Render Infrastructure

### 1A. PostgreSQL Database

**Render Dashboard > New > PostgreSQL**

| Setting | Value |
|---------|-------|
| Name | `amplify-db` |
| Database | `amplify_os` |
| User | `amplify` |
| Region | Oregon (US West) |
| Plan | Free (90-day limit) or Starter ($7/mo for persistent) |

After creation, copy:
- **Internal Database URL** — used by API and Worker (fast, no egress)
- **External Database URL** — used for migrations from your machine

Note: Render's internal URLs use `postgres://` prefix. The API needs `postgresql+asyncpg://` and migrations need `postgresql://`. You'll transform the connection string in env vars:

```
# Render gives you:
postgres://amplify:PASSWORD@HOSTNAME:5432/amplify_os

# API needs (async):
postgresql+asyncpg://amplify:PASSWORD@HOSTNAME:5432/amplify_os

# Migrations need (sync):
postgresql://amplify:PASSWORD@HOSTNAME:5432/amplify_os
```

### 1B. Redis

**Render Dashboard > New > Redis**

| Setting | Value |
|---------|-------|
| Name | `amplify-redis` |
| Region | Oregon (same as Postgres) |
| Plan | Free (25MB) or Starter ($7/mo) |
| Eviction Policy | `noeviction` |

Copy the **Internal Redis URL** (e.g., `redis://red-xxx:6379`).

### 1C. Run Migrations

From your local machine, using the **external** Postgres URL:

```bash
cd apps/api

# Set the sync URL for Alembic
export DATABASE_URL_SYNC="postgresql://amplify:PASSWORD@EXTERNAL_HOST:5432/amplify_os"

# Run migrations
alembic upgrade head

# Seed your artist data
cd ../..
DATABASE_URL="postgresql+asyncpg://amplify:PASSWORD@EXTERNAL_HOST:5432/amplify_os" \
  python scripts/seed_drew.py
```

---

## Phase 2: Render API Service

**Render Dashboard > New > Web Service**

| Setting | Value |
|---------|-------|
| Name | `amplify-api` |
| Region | Oregon |
| Runtime | Docker |
| Dockerfile Path | `apps/api/Dockerfile` |
| Docker Context | `.` (repo root — packages are siblings) |
| Instance Type | Free (750 hrs/mo) or Starter ($7/mo) |
| Health Check Path | `/health` |
| Auto-Deploy | Yes (on push to main) |

**Environment Variables:**

| Variable | Value |
|----------|-------|
| `DEPLOYMENT_MODE` | `cloud` |
| `DATABASE_URL` | `postgresql+asyncpg://amplify:PASSWORD@INTERNAL_HOST:5432/amplify_os` |
| `REDIS_URL` | `redis://red-xxx:6379` |
| `JWT_SECRET` | Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `JWT_ALGORITHM` | `HS256` |
| `CORS_ORIGINS` | `["https://amplify-os.vercel.app","https://YOUR-DOMAIN.com"]` |
| `FRONTEND_URL` | `https://amplify-os.vercel.app` |
| `ANTHROPIC_API_KEY` | Your key |
| `TOKEN_ENCRYPTION_KEY` | Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `INSTAGRAM_CLIENT_ID` | From Meta Developer Console |
| `INSTAGRAM_CLIENT_SECRET` | From Meta Developer Console |
| `INSTAGRAM_REDIRECT_URI` | `https://amplify-api.onrender.com/oauth/callback/instagram` |
| `YOUTUBE_CLIENT_ID` | From Google Cloud Console |
| `YOUTUBE_CLIENT_SECRET` | From Google Cloud Console |
| `YOUTUBE_REDIRECT_URI` | `https://amplify-api.onrender.com/oauth/callback/youtube` |
| `TIKTOK_CLIENT_KEY` | From TikTok Developer Portal |
| `TIKTOK_CLIENT_SECRET` | From TikTok Developer Portal |
| `TIKTOK_REDIRECT_URI` | `https://amplify-api.onrender.com/oauth/callback/tiktok` |

**Free tier note:** Render free web services spin down after 15 minutes of inactivity. First request after sleep takes ~30-60s. Starter ($7/mo) keeps it always-on.

---

## Phase 3: Render Worker Service

**Render Dashboard > New > Background Worker**

| Setting | Value |
|---------|-------|
| Name | `amplify-worker` |
| Region | Oregon |
| Runtime | Docker |
| Dockerfile Path | `apps/worker/Dockerfile` |
| Docker Context | `.` (repo root) |
| Instance Type | Free or Starter ($7/mo) |
| Auto-Deploy | Yes |

**Environment Variables:**

| Variable | Value |
|----------|-------|
| `AMPLIFY_DATABASE_URL` | `postgresql+asyncpg://amplify:PASSWORD@INTERNAL_HOST:5432/amplify_os` |
| `AMPLIFY_REDIS_URL` | `redis://red-xxx:6379` |
| `AMPLIFY_LOG_LEVEL` | `INFO` |
| `ANTHROPIC_API_KEY` | Your key (for content generation jobs) |

Note: Worker config uses `AMPLIFY_` prefix (from `model_config = {"env_prefix": "AMPLIFY_"}`).

**Free tier note:** Background workers are Starter-only ($7/mo) on Render. For beta, you could run the worker as a second Web Service with a dummy health endpoint, or just run it locally during beta.

---

## Phase 4: Vercel Dashboard

**Vercel Dashboard > Add New Project > Import Git Repository**

| Setting | Value |
|---------|-------|
| Framework | Next.js |
| Root Directory | `apps/web` |
| Build Command | `npm run build` |
| Output Directory | `.next` |
| Install Command | `npm ci` |
| Node.js Version | 20.x |

**Environment Variables:**

| Variable | Value |
|----------|-------|
| `NEXT_PUBLIC_API_URL` | `https://amplify-api.onrender.com` |

**Important:** The `NEXT_PUBLIC_API_URL` is used in two ways:
1. **Server-side rewrites** (`next.config.js`) — proxies `/api/*` through Vercel to the Render API
2. **Client-side fetch** (`lib/api.ts`) — browser calls the API directly

For Vercel, the rewrites proxy works only for server-side rendering. Client-side calls go directly to the Render API URL. The CORS_ORIGINS on the API must include the Vercel domain.

**Deployment:** Vercel auto-deploys on push to main. For manual deploys:
```bash
cd apps/web
npx vercel --prod
```

---

## Phase 5: Wire Your Beta Artist

### Register your tenant

```bash
# Create your account via the API
curl -X POST https://amplify-api.onrender.com/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "drew@example.com",
    "password": "YOUR_PASSWORD",
    "tenant_name": "Drew Baird Music",
    "display_name": "Drew Baird"
  }'
```

Save the `access_token` from the response.

### Seed your artist profile

```bash
# Or run the seed script against the external DB
DATABASE_URL="postgresql+asyncpg://amplify:PASSWORD@EXTERNAL_HOST:5432/amplify_os" \
  python scripts/seed_drew.py
```

### Connect your social platforms

1. Go to `https://amplify-os.vercel.app/channels`
2. Click "Connect Instagram" — OAuth flow redirects to Meta, then back
3. Click "Connect TikTok" — same flow
4. Click "Connect YouTube" — same flow

Each connection stores encrypted tokens in the database and enables publishing + metrics sync for that channel.

---

## Cost Summary

### Beta (just you)

| Service | Plan | Cost |
|---------|------|------|
| Vercel | Hobby | Free |
| Render API | Free | $0 (sleeps after 15min) |
| Render Worker | Starter | $7/mo (required for background jobs) |
| Render PostgreSQL | Free | $0 (90-day limit, then $7/mo) |
| Render Redis | Free | $0 (25MB cap) |
| **Total** | | **$7/mo** |

### Beta (always-on, no cold starts)

| Service | Plan | Cost |
|---------|------|------|
| Vercel | Hobby | Free |
| Render API | Starter | $7/mo |
| Render Worker | Starter | $7/mo |
| Render PostgreSQL | Starter | $7/mo |
| Render Redis | Starter | $7/mo |
| **Total** | | **$28/mo** |

### Production (multi-tenant, real traffic)

| Service | Plan | Cost |
|---------|------|------|
| Vercel | Pro | $20/mo |
| Render API | Standard | $25/mo |
| Render Worker | Standard | $25/mo |
| Render PostgreSQL | Standard | $20/mo |
| Render Redis | Standard | $10/mo |
| **Total** | | **~$100/mo** |

---

## Render Blueprint (render.yaml)

Drop this in the repo root for one-click Render setup:

```yaml
# render.yaml — Amplify OS infrastructure blueprint
databases:
  - name: amplify-db
    databaseName: amplify_os
    user: amplify
    plan: free
    region: oregon

services:
  - type: redis
    name: amplify-redis
    plan: free
    region: oregon
    ipAllowList: []
    maxmemoryPolicy: noeviction

  - type: web
    name: amplify-api
    runtime: docker
    dockerfilePath: apps/api/Dockerfile
    dockerContext: .
    region: oregon
    plan: free
    healthCheckPath: /health
    envVars:
      - key: DEPLOYMENT_MODE
        value: cloud
      - key: DATABASE_URL
        fromDatabase:
          name: amplify-db
          property: connectionString
        # NOTE: You must manually change postgres:// to postgresql+asyncpg:// in the dashboard
      - key: REDIS_URL
        fromService:
          type: redis
          name: amplify-redis
          property: connectionString
      - key: JWT_SECRET
        generateValue: true
      - key: JWT_ALGORITHM
        value: HS256
      - key: CORS_ORIGINS
        sync: false
      - key: FRONTEND_URL
        sync: false
      - key: ANTHROPIC_API_KEY
        sync: false
      - key: TOKEN_ENCRYPTION_KEY
        sync: false
      - key: INSTAGRAM_CLIENT_ID
        sync: false
      - key: INSTAGRAM_CLIENT_SECRET
        sync: false
      - key: INSTAGRAM_REDIRECT_URI
        sync: false
      - key: YOUTUBE_CLIENT_ID
        sync: false
      - key: YOUTUBE_CLIENT_SECRET
        sync: false
      - key: YOUTUBE_REDIRECT_URI
        sync: false
      - key: TIKTOK_CLIENT_KEY
        sync: false
      - key: TIKTOK_CLIENT_SECRET
        sync: false
      - key: TIKTOK_REDIRECT_URI
        sync: false

  - type: worker
    name: amplify-worker
    runtime: docker
    dockerfilePath: apps/worker/Dockerfile
    dockerContext: .
    region: oregon
    plan: starter
    envVars:
      - key: AMPLIFY_DATABASE_URL
        fromDatabase:
          name: amplify-db
          property: connectionString
      - key: AMPLIFY_REDIS_URL
        fromService:
          type: redis
          name: amplify-redis
          property: connectionString
      - key: AMPLIFY_LOG_LEVEL
        value: INFO
      - key: ANTHROPIC_API_KEY
        sync: false
```

---

## Code Changes Required

### 1. Handle Render's `postgres://` connection string

Render provides `postgres://` but SQLAlchemy async needs `postgresql+asyncpg://`. Add a transform to both config files:

**`apps/api/app/config.py`** — add a validator that rewrites the URL prefix.

**`apps/worker/app/config.py`** — same transform.

### 2. Vercel config (optional)

A `vercel.json` at `apps/web/` can pin the root directory, but Vercel auto-detects Next.js. Only needed if you want custom headers or redirects.

### 3. CORS origins

The API `cors_origins` default only includes localhost. For production, set via env var:
```
CORS_ORIGINS=["https://amplify-os.vercel.app"]
```

The Settings class already parses this as `list[str]`.

---

## Deployment Sequence (First Time)

```
Step 1  Create Render PostgreSQL         (2 min)
Step 2  Create Render Redis              (1 min)
Step 3  Run migrations from local        (1 min)
        └─ alembic upgrade head
Step 4  Seed your artist data            (1 min)
        └─ python scripts/seed_drew.py
Step 5  Deploy Render API service        (5 min — Docker build)
Step 6  Verify: GET /health returns 200  (1 min)
Step 7  Deploy Render Worker             (5 min — Docker build)
Step 8  Deploy Vercel dashboard          (2 min)
Step 9  Verify: login at dashboard URL   (1 min)
Step 10 Connect your social channels     (5 min — OAuth flows)
```

---

## Ongoing Deploys

| What | How |
|------|-----|
| Dashboard | Push to `main` → Vercel auto-deploys, or `cd apps/web && npx vercel --prod` |
| API | Push to `main` → Render auto-deploys from Dockerfile |
| Worker | Push to `main` → Render auto-deploys from Dockerfile |
| Migrations | `cd apps/api && DATABASE_URL_SYNC=... alembic upgrade head` (run before deploying API if schema changed) |
| Seed data | Run seed scripts against external DB URL |

---

## Local Node (Future Premium Feature)

The local node (`apps/local_node/`) is already built as a standalone Docker container that:
- Heartbeats to the control plane API at `/api/v1/local-nodes/heartbeat`
- Handles media rendering locally (ffmpeg)
- Works offline with a local SQLite database
- Syncs when reconnected

**Premium deployment path:**
1. Artist installs Docker (or a packaged Electron app wrapping the container)
2. Artist runs: `docker run -e AMPLIFY_API_URL=https://amplify-api.onrender.com -e AMPLIFY_NODE_TOKEN=xxx amplify-os/local-node`
3. Node registers with the control plane, receives job assignments
4. Heavy jobs (video rendering, batch media) run locally instead of on Render

This requires no infrastructure changes to the control plane — just an API endpoint for node registration and a job routing flag.

---

## Beta Verification Checklist

After deploying all services:

- [ ] `GET https://amplify-api.onrender.com/health` returns `{"status": "ok"}`
- [ ] Register account via dashboard or curl
- [ ] Login at `https://amplify-os.vercel.app` works
- [ ] Artist profile visible in dashboard
- [ ] Create a campaign
- [ ] Connect at least one social channel (Instagram recommended first)
- [ ] Create and approve a post
- [ ] Worker picks up the publish job (check Render worker logs)
- [ ] Intelligence dashboard shows data flowing
- [ ] Health check job runs without alerts

---

## Known Render Free-Tier Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| Web services sleep after 15 min | First request takes ~30-60s | Upgrade to Starter ($7) or use UptimeRobot to ping `/health` every 14 min |
| Free Postgres expires after 90 days | Database deleted | Upgrade to Starter ($7) before expiry |
| Free Redis: 25MB, no persistence | Queue data lost on restart | Fine for beta — jobs are re-enqueued by scheduler. Upgrade for production |
| Background workers require Starter | Can't run worker on free | Required $7/mo minimum, or run worker locally during beta |
| No custom domains on free tier | Use `.onrender.com` subdomain | Upgrade to add custom domain |
| Docker builds are slow (~5-10 min) | Deploy latency | Acceptable for beta. Use build cache |
