# TickTick task runner. Run `just` to list recipes.

# List available recipes
default:
    @just --list

# Install/sync dependencies from the lockfile
install:
    uv sync

# Update all dependencies to latest allowed versions and sync
update:
    uv lock --upgrade
    uv sync

# Run the backend regression tests
test:
    uv run python -m unittest -v tests/test_backend_fixes.py

# Run development server with hot reload on port 8000
dev:
    uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Run production server on port 8000
serve:
    uv run uvicorn main:app --host 0.0.0.0 --port 8000

# Format Python code with ruff
format:
    uvx ruff format

# Lint Python code with ruff
lint:
    uvx ruff check

# Start the Docker deployment in the background
docker-up:
    docker compose up -d

# Stop the Docker deployment
docker-down:
    docker compose down
