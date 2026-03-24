# Stage 1: Builder
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build deps
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY packages/ packages/

# Install only the library packages (not the app itself)
RUN pip install --no-cache-dir --prefix=/install \
    ./packages/core \
    ./packages/db \
    ./packages/billing \
    ./packages/observability \
    ./packages/learning \
    ./packages/agents \
    ./packages/adapters

# Install API runtime deps separately (no package build needed)
RUN pip install --no-cache-dir --prefix=/install \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.32" \
    "alembic>=1.14" \
    "redis>=5.0" \
    "pydantic[email]>=2.0" \
    "pydantic-settings>=2.0" \
    "pyjwt>=2.9" \
    "passlib[bcrypt]>=1.7" \
    "bcrypt>=4.0,<4.1" \
    "cryptography>=42.0" \
    "httpx>=0.27" \
    "python-multipart>=0.0.9" \
    "boto3>=1.35"

# Stage 2: Runtime
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r amplify && useradd -r -g amplify -d /app amplify

WORKDIR /app

COPY --from=builder /install/lib /usr/local/lib
COPY --from=builder /install/bin /usr/local/bin
COPY apps/api/app ./app
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini .

RUN chown -R amplify:amplify /app
USER amplify

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
