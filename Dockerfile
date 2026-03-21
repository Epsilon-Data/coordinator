FROM python:3.11-slim

# Install system dependencies (combined from all workers)
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Configure git for Docker bind mount compatibility
RUN git config --global init.templateDir ''

WORKDIR /app

# Install epsilon-attestation-verifier from GitHub
ARG GITHUB_TOKEN=""
ARG VERIFIER_REF=main
RUN if [ -n "$GITHUB_TOKEN" ]; then \
      pip install --no-cache-dir "git+https://${GITHUB_TOKEN}@github.com/Epsilon-Data/epsilon-attestation-verifier.git@${VERIFIER_REF}"; \
    else \
      pip install --no-cache-dir "git+https://github.com/Epsilon-Data/epsilon-attestation-verifier.git@${VERIFIER_REF}"; \
    fi

# Copy package definition first for better layer caching
COPY pyproject.toml .

# Copy source code
COPY shared ./shared/
COPY workers ./workers/
COPY entrypoint.py .

# Copy Alembic migration files
COPY alembic.ini .
COPY migrations ./migrations/

# Install all dependencies (single image for all workers)
RUN pip install --no-cache-dir -e .[all-with-ai]

# Create necessary directories and non-root user
RUN mkdir -p /app/logs /app/artifacts /app/repositories /shared/epsilon \
    && useradd --create-home --shell /bin/bash --uid 1000 appuser \
    && chown -R appuser:appuser /app /shared/epsilon

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV ENVIRONMENT=production

# Default worker mode (can be overridden)
ENV WORKER_MODE=executor

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "from shared.db import db; conn = db.engine.connect(); conn.close()" || exit 1

USER appuser

# Run entrypoint with worker mode
ENTRYPOINT ["python", "entrypoint.py"]