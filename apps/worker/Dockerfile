# Stage 1: Build
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY packages/ packages/

# Install only the library packages (not the app itself)
RUN pip install --no-cache-dir --prefix=/install \
    ./packages/core \
    ./packages/db \
    ./packages/agents \
    ./packages/adapters \
    ./packages/media \
    ./packages/billing \
    ./packages/observability \
    ./packages/learning

# Install worker runtime deps separately (no package build needed)
RUN pip install --no-cache-dir --prefix=/install \
    "redis>=5.0" \
    "sqlalchemy[asyncio]>=2.0" \
    "asyncpg>=0.30" \
    "pydantic-settings>=2.0" \
    "boto3>=1.35"

# Stage 2: Runtime
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 ffmpeg curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r amplify && useradd -r -g amplify -d /app amplify

WORKDIR /app

COPY --from=builder /install/lib /usr/local/lib
COPY --from=builder /install/bin /usr/local/bin
COPY apps/worker/app/ app/

RUN chown -R amplify:amplify /app
USER amplify

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import redis; r=redis.from_url('${REDIS_URL:-redis://localhost:6379/0}'); r.ping()" || exit 1

CMD ["python", "-m", "app.main"]
