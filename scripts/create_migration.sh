#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../apps/api"
alembic revision --autogenerate -m "${1:?Usage: create_migration.sh <message>}"
echo "Migration created. Review it in apps/api/alembic/versions/"
