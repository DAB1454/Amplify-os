.PHONY: setup dev build test lint format migrate migrate-new migrate-check docker-up docker-down clean seed seed-drew seed-artist local-node local-node-down

setup:
	python -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e packages/core
	.venv/bin/pip install -e packages/db
	.venv/bin/pip install -e packages/adapters
	.venv/bin/pip install -e packages/agents
	.venv/bin/pip install -e packages/media
	.venv/bin/pip install -e packages/billing
	.venv/bin/pip install -e packages/observability
	.venv/bin/pip install -e packages/learning
	.venv/bin/pip install -e apps/api
	.venv/bin/pip install -e apps/worker
	.venv/bin/pip install -e apps/local_node
	cd apps/web && npm install
	.venv/bin/pip install pre-commit
	.venv/bin/pre-commit install

dev:
	docker compose up -d postgres redis
	.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --app-dir apps/api &
	cd apps/worker && ../../.venv/bin/python -m worker.main &
	cd apps/web && npm run dev &
	wait

build:
	docker compose build

test:
	.venv/bin/pytest
	cd apps/web && npm test

lint:
	.venv/bin/ruff check .
	.venv/bin/mypy apps/ packages/

format:
	.venv/bin/ruff format .
	cd apps/web && npx prettier --write src/

migrate:
	cd apps/api && ../../.venv/bin/alembic upgrade head

migrate-new:
	cd apps/api && ../../.venv/bin/alembic revision --autogenerate -m "$(msg)"

migrate-check:
	cd apps/api && ../../.venv/bin/alembic check

seed:
	.venv/bin/python scripts/seed.py

seed-drew:
	.venv/bin/python scripts/seed_drew.py

seed-artist:
	.venv/bin/python scripts/seed_artist.py

docker-up:
	docker compose up -d

docker-down:
	docker compose down

local-node:
	docker compose --profile local-node up -d

local-node-down:
	docker compose --profile local-node down

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name node_modules -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .next -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
