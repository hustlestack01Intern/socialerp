# SocialERP Makefile
# Run `make help` to see all targets

.PHONY: help check test test-unit test-integration test-contract eval lint typecheck format db-up db-down migrate migrate-down seed run-api run-worker run-dashboard clean

# Default target
help:
	@echo "SocialERP - Agentic ERP for Kenyan Social Commerce"
	@echo ""
	@echo "Core targets:"
	@echo "  make check           Run all checks (lint + typecheck + tests + evals)"
	@echo "  make test            Run all tests (unit + integration + contract)"
	@echo "  make test-unit       Run unit tests only"
	@echo "  make test-integration Run integration tests only"
	@echo "  make test-contract   Run contract tests only"
	@echo "  make eval            Run evaluation harness"
	@echo "  make lint            Run ruff + eslint"
	@echo "  make typecheck       Run mypy --strict + tsc --noEmit"
	@echo "  make format          Format code with ruff + prettier"
	@echo ""
	@echo "Database targets:"
	@echo "  make db-up           Start Postgres + Redis in docker-compose"
	@echo "  make db-down         Stop and remove database containers"
	@echo "  make migrate         Run Alembic migrations up"
	@echo "  make migrate-down    Rollback last migration"
	@echo "  make seed            Seed development data"
	@echo ""
	@echo "Run targets:"
	@echo "  make run-api         Start FastAPI development server"
	@echo "  make run-worker      Start arq worker"
	@echo "  make run-dashboard   Start Next.js development server"
	@echo ""
	@echo "Utility targets:"
	@echo "  make clean           Remove build artifacts and cache"

# =============================================================================
# CHECK - Run all quality gates
# =============================================================================
# The gate is the whole product: Python AND the dashboard. `lint` and
# `typecheck` are the umbrella targets (ruff + eslint, mypy + tsc), so the
# dashboard cannot drift behind a Python-only gate (found 2026-10-21).
check: lint typecheck test eval
	@echo "✅ All checks passed!"

# =============================================================================
# LINTING & FORMATTING
# =============================================================================
lint: lint-python lint-js

lint-python:
	@echo "🔍 Running ruff..."
	uv run ruff check .

lint-js:
	@echo "🔍 Running eslint..."
	cd apps/dashboard && pnpm lint

format: format-python format-js

format-python:
	@echo "✨ Formatting Python with ruff..."
	uv run ruff format .

format-js:
	@echo "✨ Formatting JS/TS with prettier..."
	cd apps/dashboard && pnpm format

# =============================================================================
# TYPE CHECKING
# =============================================================================
typecheck: typecheck-python typecheck-js

typecheck-python:
	@echo "🔍 Running mypy --strict..."
	uv run mypy packages apps

typecheck-js:
	@echo "🔍 Running tsc --noEmit..."
	cd apps/dashboard && pnpm typecheck

# =============================================================================
# TESTS
# =============================================================================
test:
	@echo "🧪 Running all tests..."
	uv run pytest

test-unit:
	@echo "🧪 Running unit tests..."
	uv run pytest tests/unit -v --tb=short

test-integration:
	@echo "🧪 Running integration tests..."
	uv run pytest tests/integration -v --tb=short

test-contract:
	@echo "🧪 Running contract tests..."
	uv run pytest tests/contract -v --tb=short

# =============================================================================
# EVALUATION
# =============================================================================
eval:
	@echo "📊 Running evaluation harness..."
	uv run python -m evals.runner

# =============================================================================
# DATABASE
# =============================================================================
db-up:
	@echo "🐳 Starting database services..."
	docker compose up -d postgres redis

db-down:
	@echo "🛑 Stopping database services..."
	docker compose down

migrate: db-up
	@echo "📦 Running migrations..."
	uv run alembic upgrade head

migrate-down:
	@echo "🔄 Rolling back last migration..."
	uv run alembic downgrade -1

seed: migrate
	@echo "🌱 Seeding development data..."
	uv run python -m scripts.seed

# =============================================================================
# RUN SERVICES
# =============================================================================
run-api: db-up
	@echo "🚀 Starting API server..."
	cd apps/api && uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000

run-worker: db-up
	@echo "👷 Starting worker..."
	cd apps/worker && uv run python -m worker.main

run-dashboard:
	@echo "🌐 Starting dashboard..."
	cd apps/dashboard && pnpm dev

# =============================================================================
# FAKE SERVERS
# =============================================================================
fake-meta:
	@echo "🤖 Starting Fake Meta server..."
	docker compose up -d fake-meta

fake-daraja:
	@echo "💰 Starting Fake Daraja server..."
	docker compose up -d fake-daraja

fake-all: fake-meta fake-daraja

# =============================================================================
# CLEANUP
# =============================================================================
clean:
	@echo "🧹 Cleaning up..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "node_modules" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".next" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	rm -rf htmlcov 2>/dev/null || true
	rm -rf .coverage 2>/dev/null || true
	docker compose down 2>/dev/null || true

# =============================================================================
# CI TARGETS (used in GitHub Actions)
# =============================================================================
ci-install:
	@echo "📦 Installing dependencies..."
	uv sync --all-extras
	cd apps/dashboard && pnpm install --frozen-lockfile

ci-check: check

ci-test: test