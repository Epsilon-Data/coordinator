#!/bin/bash

# Start the Epsilon Coordinator API Server locally
# This script sets up the environment and starts the Flask API server

echo "Starting Epsilon Coordinator API Server..."

# Get the directory of this script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PARENT_DIR="$( dirname "$DIR" )"

# Check if virtual environment exists
if [ ! -d "$PARENT_DIR/venv" ]; then
    echo "Creating virtual environment in $PARENT_DIR/venv..."
    cd "$PARENT_DIR"
    python3 -m venv venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source "$PARENT_DIR/venv/bin/activate"

# Install dependencies from both main requirements and API requirements
echo "Installing dependencies..."
cd "$PARENT_DIR"
pip install -r requirements.txt
pip install -r api/requirements.txt

# Load environment variables if .env exists
if [ -f "$PARENT_DIR/.env" ]; then
    echo "Loading environment variables from .env..."
    export $(cat "$PARENT_DIR/.env" | xargs)
fi

# Set API-specific environment variables
export API_PORT=${API_PORT:-8001}
export API_HOST=${API_HOST:-0.0.0.0}
export DEBUG=${DEBUG:-true}
export PYTHONPATH="$PARENT_DIR:$PARENT_DIR/api"
export SHARED_STORAGE_PATH=${SHARED_STORAGE_PATH:-"$PARENT_DIR/shared_storage"}

echo ""
echo "Health check: http://$API_HOST:$API_PORT/health"
echo "API info: http://$API_HOST:$API_PORT/api"
echo "Shared storage path: $SHARED_STORAGE_PATH"

# Change to API directory and start the server
cd "$DIR"
python server.py