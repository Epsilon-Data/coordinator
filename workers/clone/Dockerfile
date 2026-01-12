FROM python:3.11-slim

# Install git and other dependencies
RUN apt-get update && apt-get install -y \
    git \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Configure git for Docker bind mount compatibility (fixes macOS Docker Desktop issues)
RUN git config --global init.templateDir ''

WORKDIR /app

# Copy package definition and install as editable
COPY pyproject.toml .
COPY shared ./shared/
COPY workers ./workers/

# Install package with clone worker dependencies
RUN pip install --no-cache-dir -e .[clone]

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV POLLING_INTERVAL=5
ENV BATCH_SIZE=5

# Run the worker
CMD ["python", "-m", "workers.clone.clone_worker"]
