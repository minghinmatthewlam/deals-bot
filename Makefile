.PHONY: install db-up db-down db-shell migrate seed gmail-auth run run-dry weekly newsletter-subscribe confirmations test lint typecheck clean

# Python environment
install:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"

# Database
db-up:
	docker compose up -d postgres
	@echo "Waiting for database to be ready..."
	@sleep 3
	@docker compose exec postgres pg_isready -U dealintel || (echo "Database not ready" && exit 1)
	@echo "Database is ready!"

db-down:
	docker compose down

db-shell:
	docker compose exec postgres psql -U dealintel

# Migrations
migrate:
	.venv/bin/python -m alembic upgrade head

migrate-down:
	.venv/bin/python -m alembic downgrade -1

migrate-create:
	@read -p "Migration name: " name; \
	.venv/bin/python -m alembic revision --autogenerate -m "$$name"

# Application
seed:
	.venv/bin/dealintel seed

gmail-auth:
	.venv/bin/dealintel gmail-auth

run:
	.venv/bin/dealintel run

run-dry:
	.venv/bin/dealintel run --dry-run

weekly:
	.venv/bin/dealintel weekly

newsletter-subscribe:
	.venv/bin/dealintel newsletter-subscribe

confirmations:
	.venv/bin/dealintel confirmations

# Development
test:
	.venv/bin/pytest

test-cov:
	.venv/bin/pytest --cov=dealintel --cov-report=html
	@echo "Coverage report: htmlcov/index.html"

lint:
	.venv/bin/ruff check src tests
	.venv/bin/ruff format --check src tests

lint-fix:
	.venv/bin/ruff check --fix src tests
	.venv/bin/ruff format src tests

typecheck:
	.venv/bin/mypy src

# Cleanup
clean:
	rm -rf .venv
	rm -rf __pycache__ src/**/__pycache__ tests/__pycache__
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	rm -rf htmlcov .coverage
	rm -rf *.egg-info src/*.egg-info
	rm -f digest_preview.html

# Full setup (first time)
setup: install db-up migrate seed
	@test -f .env || cp .env.example .env
	@echo "Setup complete! Update .env and run 'make gmail-auth' to authenticate with Gmail."

# Help
help:
	@echo "Deal Intelligence - Available commands:"
	@echo ""
	@echo "Setup:"
	@echo "  make install     - Create venv and install dependencies"
	@echo "  make setup       - Full first-time setup (install + db + migrate + seed)"
	@echo "  make db-up       - Start PostgreSQL container"
	@echo "  make db-down     - Stop PostgreSQL container"
	@echo "  make db-shell    - Connect to PostgreSQL"
	@echo ""
	@echo "Database:"
	@echo "  make migrate     - Run database migrations"
	@echo "  make migrate-down - Rollback last migration"
	@echo "  make seed        - Seed stores from stores.yaml"
	@echo ""
	@echo "Application:"
	@echo "  make gmail-auth  - Authenticate with Gmail (first time)"
	@echo "  make run         - Run daily pipeline"
	@echo "  make run-dry     - Dry run (saves preview HTML)"
	@echo ""
	@echo "Development:"
	@echo "  make test        - Run tests"
	@echo "  make test-cov    - Run tests with coverage report"
	@echo "  make lint        - Check code style"
	@echo "  make lint-fix    - Fix code style issues"
	@echo "  make typecheck   - Run type checking"
	@echo "  make clean       - Remove generated files"
