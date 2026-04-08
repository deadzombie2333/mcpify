#!/bin/bash
set -e

# mcpify — EC2 one-time setup
echo "🔧 mcpify setup"

# Python 3.11
if ! command -v python3.11 &>/dev/null; then
    echo "Installing Python 3.11..."
    sudo dnf install -y python3.11 python3.11-pip 2>/dev/null || \
    sudo yum install -y python3.11 python3.11-pip 2>/dev/null || \
    sudo apt-get install -y python3.11 python3.11-pip 2>/dev/null
fi

# jq
if ! command -v jq &>/dev/null; then
    echo "Installing jq..."
    sudo dnf install -y jq 2>/dev/null || \
    sudo yum install -y jq 2>/dev/null || \
    sudo apt-get install -y jq 2>/dev/null
fi

# AWS CLI
if ! command -v aws &>/dev/null; then
    echo "Installing AWS CLI..."
    curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
    unzip -q /tmp/awscliv2.zip -d /tmp
    sudo /tmp/aws/install
    rm -rf /tmp/aws /tmp/awscliv2.zip
fi

# Python deps for embedder
pip3 install boto3 opensearch-py requests-aws4auth pdf2image python-docx 2>/dev/null || \
pip3.11 install boto3 opensearch-py requests-aws4auth pdf2image python-docx

# poppler (for pdf2image) and libreoffice (for docx→pdf)
if ! command -v pdftoppm &>/dev/null; then
    echo "Installing poppler-utils (PDF rendering)..."
    sudo dnf install -y poppler-utils 2>/dev/null || \
    sudo yum install -y poppler-utils 2>/dev/null || \
    sudo apt-get install -y poppler-utils 2>/dev/null
fi

if ! command -v libreoffice &>/dev/null; then
    echo "Installing LibreOffice headless (DOCX conversion)..."
    sudo dnf install -y libreoffice-core libreoffice-writer 2>/dev/null || \
    sudo yum install -y libreoffice-core libreoffice-writer 2>/dev/null || \
    sudo apt-get install -y libreoffice 2>/dev/null
fi

# Make scripts executable
chmod +x deploy.sh connect.sh update-docs.sh cli/mcpify deploy/package-lambda.sh

echo ""
echo "✅ Setup complete. Verify:"
echo "  python3 --version"
echo "  aws --version"
echo "  aws sts get-caller-identity"
echo ""
echo "Next: edit config.json, put docs in docs/, then run ./deploy.sh"
