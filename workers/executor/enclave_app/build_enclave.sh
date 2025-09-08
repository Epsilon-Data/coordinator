#!/bin/bash
set -e

echo "🔨 Building Nitro Enclave Image (EIF) for Executor..."

# Check if nitro-cli is installed
if ! command -v nitro-cli &> /dev/null; then
    echo "❌ Error: nitro-cli is not installed"
    echo "Please install nitro-cli: sudo amazon-linux-extras install aws-nitro-enclaves-cli"
    exit 1
fi

# Check if required files exist
if [ ! -f "kmstool_enclave_cli" ]; then
    echo "❌ Error: kmstool_enclave_cli not found"
    echo "Downloading kmstool_enclave_cli..."
    
    # Download the latest kmstool_enclave_cli
    ARCH=$(uname -m)
    if [ "$ARCH" = "x86_64" ]; then
        wget -O kmstool_enclave_cli https://s3.amazonaws.com/aws-nitro-enclaves-cli-region/aws-nitro-enclaves-cli.tar.gz
        tar -xvf aws-nitro-enclaves-cli.tar.gz --strip-components=3 -C . ./usr/bin/kmstool_enclave_cli
        rm aws-nitro-enclaves-cli.tar.gz
    else
        echo "❌ Error: Architecture $ARCH not supported"
        exit 1
    fi
    
    chmod +x kmstool_enclave_cli
fi

if [ ! -f "libnsm.so" ]; then
    echo "❌ Error: libnsm.so not found"
    echo "Downloading libnsm.so..."
    
    # Download libnsm.so from the SDK
    wget -O nsm-api.tar.gz https://github.com/aws/aws-nitro-enclaves-nsm-api/releases/download/v0.4.0/aws-nitro-enclaves-nsm-api-0.4.0-x86_64.tar.gz
    tar -xvf nsm-api.tar.gz --wildcards '*/libnsm.so'
    find . -name libnsm.so -exec mv {} . \;
    rm -rf nsm-api.tar.gz
fi

# Build Docker image
echo "📦 Building Docker image..."
docker build -t epsilon-executor-enclave:latest .

# Convert Docker image to EIF
echo "🔄 Converting to EIF format..."
nitro-cli build-enclave \
    --docker-uri epsilon-executor-enclave:latest \
    --output-file epsilon-executor.eif

# Get enclave measurements
echo "📊 Enclave measurements:"
nitro-cli describe-eif --eif-path epsilon-executor.eif

# Create directory for the EIF file
sudo mkdir -p /opt/enclaves
sudo cp epsilon-executor.eif /opt/enclaves/executor.eif
sudo chmod 644 /opt/enclaves/executor.eif

echo "✅ Build complete!"
echo "📁 EIF file copied to: /opt/enclaves/executor.eif"
echo ""
echo "To run the enclave:"
echo "  nitro-cli run-enclave --cpu-count 2 --memory 4096 --enclave-cid 16 --eif-path /opt/enclaves/executor.eif --debug-mode"
echo ""
echo "To view console output:"
echo "  nitro-cli console --enclave-id <enclave-id>"