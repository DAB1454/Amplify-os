#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────
# Amplify-OS Cloud Deploy Script
# Builds Docker images, pushes to ECR, and updates ECS services.
#
# Usage:
#   ./infra/deploy/cloud/deploy.sh staging          # deploy to staging
#   ./infra/deploy/cloud/deploy.sh production        # deploy to production
#   ./infra/deploy/cloud/deploy.sh production api    # deploy only API
# ─────────────────────────────────────────────────────────────────────

ENV="${1:?Usage: deploy.sh <staging|production> [api|worker|both]}"
SERVICE="${2:-both}"

AWS_REGION="${AWS_REGION:-us-east-1}"
CLUSTER="amplify-${ENV}"
TAG="$(git rev-parse --short HEAD)"
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"

echo "═══════════════════════════════════════════════════════"
echo "  Amplify-OS Deploy"
echo "  Environment: ${ENV}"
echo "  Service:     ${SERVICE}"
echo "  Git SHA:     ${TAG}"
echo "  Timestamp:   ${TIMESTAMP}"
echo "═══════════════════════════════════════════════════════"

# Validate environment
if [[ "${ENV}" != "staging" && "${ENV}" != "production" ]]; then
  echo "ERROR: Environment must be 'staging' or 'production'"
  exit 1
fi

# Production safeguard
if [[ "${ENV}" == "production" ]]; then
  echo ""
  echo "⚠  You are deploying to PRODUCTION"
  read -r -p "Type 'yes' to confirm: " confirm
  if [[ "${confirm}" != "yes" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

# Get ECR login
echo ""
echo "→ Logging into ECR..."
aws ecr get-login-password --region "${AWS_REGION}" | \
  docker login --username AWS --password-stdin \
  "$(aws sts get-caller-identity --query Account --output text).dkr.ecr.${AWS_REGION}.amazonaws.com"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_BASE="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

build_and_push() {
  local name="$1"
  local dockerfile="$2"
  local image="${ECR_BASE}/amplify-os/${name}"

  echo ""
  echo "→ Building ${name}..."
  docker build -f "${dockerfile}" -t "${image}:${TAG}" -t "${image}:latest" .

  echo "→ Pushing ${name}..."
  docker push "${image}:${TAG}"
  docker push "${image}:latest"
}

deploy_service() {
  local name="$1"
  echo ""
  echo "→ Deploying ${name} to ECS cluster ${CLUSTER}..."
  aws ecs update-service \
    --cluster "${CLUSTER}" \
    --service "${name}" \
    --force-new-deployment \
    --region "${AWS_REGION}" \
    --no-cli-pager
}

# Build and push
if [[ "${SERVICE}" == "api" || "${SERVICE}" == "both" ]]; then
  build_and_push "api" "apps/api/Dockerfile"
fi

if [[ "${SERVICE}" == "worker" || "${SERVICE}" == "both" ]]; then
  build_and_push "worker" "apps/worker/Dockerfile"
fi

# Deploy
if [[ "${SERVICE}" == "api" || "${SERVICE}" == "both" ]]; then
  deploy_service "api"
fi

if [[ "${SERVICE}" == "worker" || "${SERVICE}" == "both" ]]; then
  deploy_service "worker"
fi

# Wait for stability
echo ""
echo "→ Waiting for services to stabilize..."
if [[ "${SERVICE}" == "both" ]]; then
  aws ecs wait services-stable --cluster "${CLUSTER}" --services api worker --region "${AWS_REGION}"
elif [[ "${SERVICE}" == "api" ]]; then
  aws ecs wait services-stable --cluster "${CLUSTER}" --services api --region "${AWS_REGION}"
else
  aws ecs wait services-stable --cluster "${CLUSTER}" --services worker --region "${AWS_REGION}"
fi

echo ""
echo "✓ Deploy complete (${ENV} @ ${TAG})"
