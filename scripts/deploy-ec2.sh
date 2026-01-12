#!/bin/bash
# ===========================================
# Epsilon Coordinator - EC2 Deployment Script
# ===========================================
# Usage: ./scripts/deploy-ec2.sh
#
# Prerequisites:
#   - Amazon Linux 2 or Ubuntu EC2 instance
#   - Security group allowing outbound HTTPS (443)
#   - IAM role with KMS permissions (or use access keys)

set -e

echo "=========================================="
echo "  Epsilon Coordinator - EC2 Setup"
echo "=========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
DATA_DIR="/data/epsilon"
APP_DIR="/opt/epsilon-coordinator"

# Detect OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
else
    OS="unknown"
fi

echo -e "${YELLOW}Detected OS: $OS${NC}"

# ===========================================
# Step 1: Install Docker
# ===========================================
echo -e "\n${GREEN}[1/6] Installing Docker...${NC}"

if command -v docker &> /dev/null; then
    echo "Docker already installed"
else
    if [ "$OS" = "amzn" ]; then
        # Amazon Linux 2
        sudo yum update -y
        sudo yum install -y docker
    elif [ "$OS" = "ubuntu" ]; then
        # Ubuntu
        sudo apt-get update
        sudo apt-get install -y docker.io
    else
        echo -e "${RED}Unsupported OS. Please install Docker manually.${NC}"
        exit 1
    fi

    sudo systemctl start docker
    sudo systemctl enable docker
    sudo usermod -aG docker $USER
fi

# ===========================================
# Step 2: Install Docker Compose
# ===========================================
echo -e "\n${GREEN}[2/6] Installing Docker Compose...${NC}"

if command -v docker-compose &> /dev/null; then
    echo "Docker Compose already installed"
else
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
fi

docker-compose --version

# ===========================================
# Step 3: Create Data Directory
# ===========================================
echo -e "\n${GREEN}[3/6] Creating data directory...${NC}"

sudo mkdir -p $DATA_DIR
sudo chown -R $USER:$USER $DATA_DIR

# Create subdirectories
mkdir -p $DATA_DIR/repositories
mkdir -p $DATA_DIR/ai_analysis
mkdir -p $DATA_DIR/execution_results

echo "Created: $DATA_DIR"
ls -la $DATA_DIR

# ===========================================
# Step 4: Setup Application Directory
# ===========================================
echo -e "\n${GREEN}[4/6] Setting up application...${NC}"

# If running from repo, copy to /opt
if [ -f "docker-compose.yml" ]; then
    echo "Running from repository directory"
    APP_DIR=$(pwd)
else
    echo "Please run this script from the epsilon-coordinator repository root"
    exit 1
fi

# ===========================================
# Step 5: Create .env file
# ===========================================
echo -e "\n${GREEN}[5/6] Checking environment configuration...${NC}"

if [ ! -f ".env" ]; then
    echo -e "${YELLOW}Creating .env file from template...${NC}"
    cp .env.example .env

    # Update for EC2
    sed -i 's|SHARED_STORAGE_HOST_PATH=./shared_storage|SHARED_STORAGE_HOST_PATH=/data/epsilon|g' .env

    echo -e "${RED}=========================================="
    echo "IMPORTANT: Edit .env file with your values:"
    echo "  - DATABASE_URL"
    echo "  - AWS_ACCESS_KEY_ID"
    echo "  - AWS_SECRET_ACCESS_KEY"
    echo "  - AWS_KMS_KEY_ARN"
    echo "  - MIDDLEWARE_ENDPOINT_URL"
    echo "==========================================${NC}"
    echo ""
    echo "Run: nano .env"
    echo "Then run: docker-compose up -d"
    exit 0
else
    echo ".env file exists"
fi

# ===========================================
# Step 6: Build and Start
# ===========================================
echo -e "\n${GREEN}[6/6] Building and starting services...${NC}"

# Need to use sudo for docker if not in docker group yet
if groups $USER | grep -q docker; then
    docker-compose build
    docker-compose up -d
else
    echo -e "${YELLOW}Note: You may need to logout/login for docker group to take effect${NC}"
    sudo docker-compose build
    sudo docker-compose up -d
fi

# ===========================================
# Done
# ===========================================
echo -e "\n${GREEN}=========================================="
echo "  Deployment Complete!"
echo "==========================================${NC}"
echo ""
echo "Check status:"
echo "  docker-compose ps"
echo ""
echo "View logs:"
echo "  docker-compose logs -f"
echo ""
echo "Stop services:"
echo "  docker-compose down"
echo ""