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

# Release a version: run tests, bump pyproject.toml, commit and tag (e.g. `just release 0.8.0`)
release version: test
    #!/usr/bin/env bash
    set -euo pipefail
    VERSION="{{version}}"
    if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "error: version must look like 1.2.3, got '$VERSION'" >&2
        exit 1
    fi
    if [ -n "$(git status --porcelain)" ]; then
        echo "error: working tree is dirty, commit or stash first" >&2
        exit 1
    fi
    if git rev-parse -q --verify "refs/tags/v$VERSION" >/dev/null; then
        echo "error: tag v$VERSION already exists" >&2
        exit 1
    fi
    sed -i -E "s/^version = \".*\"/version = \"$VERSION\"/" pyproject.toml
    uv lock
    git add pyproject.toml uv.lock
    git commit -m "chore: version bump"
    git tag "v$VERSION"
    echo "tagged v$VERSION; push with: git push && git push origin v$VERSION"

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
