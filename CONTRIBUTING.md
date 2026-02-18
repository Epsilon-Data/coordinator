# Contributing to Epsilon Coordinator

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

## Getting Started

### Prerequisites

- Python 3.9+
- PostgreSQL
- Docker and Docker Compose v2
- Git

### Local Development Setup

```bash
# Clone the repository
git clone https://github.com/Epsilon-Data/epsilon-coordinator.git
cd epsilon-coordinator

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dev dependencies
pip install -e ".[dev]"

# Set up pre-commit hooks
pre-commit install --hook-type commit-msg

# Copy environment file
cp .env.example .env
# Edit .env with your local database URL

# Run database migrations
alembic upgrade head
```

### Running Workers Locally

```bash
# Set worker mode via environment variable
export WORKER_MODE=executor
python entrypoint.py

# Or use Docker Compose
docker compose up
```

### Running Tests

```bash
pytest
pytest --cov=shared --cov=workers  # with coverage
```

## Commit Convention

This project uses [Conventional Commits](https://www.conventionalcommits.org/). All commit messages are validated by a pre-commit hook.

### Format

```
<type>(<optional scope>): <description>

[optional body]

[optional footer]
```

### Types

| Type | Description |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code change that neither fixes nor adds |
| `test` | Adding or fixing tests |
| `chore` | Maintenance, dependencies, CI |
| `perf` | Performance improvement |
| `ci` | CI/CD changes |
| `build` | Build system changes |

### Examples

```bash
feat: add PCR verification to attestation flow
fix(executor): return False on health check exception
docs: update deployment instructions
chore: upgrade sqlalchemy to 2.0
refactor(workers): extract common polling logic
```

## Pull Request Process

1. **Fork** the repository and create a branch from `main`
2. **Make changes** following the code style and conventions below
3. **Write tests** for new functionality
4. **Run tests** locally before submitting
5. **Create a PR** with a clear title and description
6. **Address review feedback** promptly

### PR Title

Use the same conventional commit format for PR titles:
```
feat: add new worker type for data validation
```

### PR Description

Include:
- Summary of changes (what and why)
- Test plan (how you verified the changes)
- Breaking changes (if any)

## Code Style

- Follow existing patterns in the codebase
- Use type hints for function signatures
- Keep functions focused and small
- Write docstrings for public methods
- Use logging instead of print statements

## Database Migrations

When changing models in `shared/db/models.py`:

```bash
# Generate migration
alembic revision --autogenerate -m "description of change"

# Review the generated migration file
# Then apply
alembic upgrade head
```

Always include the migration file in your PR.

## Project Structure

```
epsilon-coordinator/
  shared/           # Shared code (config, database, models)
  workers/
    executor/       # Enclave execution worker
    clone/          # Repository clone worker
    job_fetcher/    # Job fetcher worker
    ai_agent/       # AI validation worker (optional)
  migrations/       # Alembic database migrations
  scripts/          # Utility scripts
```

## Questions?

Open a [GitHub Discussion](https://github.com/Epsilon-Data/epsilon-coordinator/discussions) or create an issue tagged with `question`.
