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

# Copy package definition first for better layer caching
COPY pyproject.toml .

# Copy source code
COPY shared ./shared/
COPY workers ./workers/
COPY entrypoint.py .

# Install all dependencies (single image for all workers)
RUN pip install --no-cache-dir -e .[all]

# Create necessary directories
RUN mkdir -p /app/logs /app/artifacts /app/repositories

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV ENVIRONMENT=production

# Default worker mode (can be overridden)
ENV WORKER_MODE=executor

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "from shared.db import db; db.engine.connect()" || exit 1

# Run entrypoint with worker mode
ENTRYPOINT ["python", "entrypoint.py"]