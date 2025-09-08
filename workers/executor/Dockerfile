FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better caching
COPY workers/executor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy executor application code
COPY workers/executor/ .

# Create necessary directories
RUN mkdir -p /app/logs /app/artifacts /app/repositories

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV ENVIRONMENT=production

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "from config import get_settings; settings = get_settings(); print(f'Worker {settings.worker_id} healthy')" || exit 1

# Run the new main entry point
CMD ["python", "main.py"]