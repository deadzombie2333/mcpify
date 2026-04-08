#!/bin/bash
set -e

# mcpify — EC2 one-time setup
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "🔧 mcpify setup"

# Python 3
if ! command -v python3 &>/dev/null; then
    echo "Installing Python 3..."
    sudo dnf install -y python3 python3-pip 2>/dev/null || \
    sudo yum install -y python3 python3-pip 2>/dev/null || \
    sudo apt-get install -y python3 python3-pip 2>/dev/null
fi

# pip3
if ! command -v pip3 &>/dev/null; then
    echo "Installing pip3..."
    sudo dnf install -y python3-pip 2>/dev/null || \
    sudo yum install -y python3-pip 2>/dev/null || \
    sudo apt-get install -y python3-pip 2>/dev/null
fi

# Python 3.11 (needed for Lambda packaging — fastmcp requires >=3.10)
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
pip3 install --user boto3 opensearch-py requests-aws4auth pdf2image python-docx

# poppler (for pdf2image) and libreoffice (for docx→pdf)
if ! command -v pdftoppm &>/dev/null; then
    echo "Installing poppler-utils (PDF rendering)..."
    sudo dnf install -y poppler-utils 2>/dev/null || \
    sudo yum install -y poppler-utils 2>/dev/null || \
    sudo apt-get install -y poppler-utils 2>/dev/null
fi

if ! command -v libreoffice &>/dev/null; then
    echo "Installing LibreOffice headless (DOCX conversion)..."
    if sudo dnf install -y libreoffice-core libreoffice-writer 2>/dev/null; then
        true
    elif sudo apt-get install -y libreoffice 2>/dev/null; then
        true
    else
        echo "  Package not in repos, installing from RPMs..."
        sudo dnf install -y wget tar 2>/dev/null || true
        LO_VERSION="25.8.6"
        cd /tmp
        wget -q "https://download.documentfoundation.org/libreoffice/stable/${LO_VERSION}/rpm/x86_64/LibreOffice_${LO_VERSION}_Linux_x86-64_rpm.tar.gz"
        tar -xzf "LibreOffice_${LO_VERSION}_Linux_x86-64_rpm.tar.gz"
        cd LibreOffice_${LO_VERSION}*_rpm/RPMS/
        sudo rpm -ivh *.rpm
        # Symlink to PATH
        LO_BIN=$(find /opt -name "soffice" 2>/dev/null | head -1)
        if [ -n "$LO_BIN" ]; then
            sudo ln -sf "$LO_BIN" /usr/local/bin/libreoffice
        fi
        cd "$SCRIPT_DIR"
        rm -rf /tmp/LibreOffice_${LO_VERSION}*
    fi
    # Install X11 libs required by LibreOffice headless
    echo "Installing LibreOffice runtime dependencies..."
    sudo dnf install -y libXinerama libXrender libXext libSM libICE libX11-xcb cairo cups-libs 2>/dev/null || true
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
