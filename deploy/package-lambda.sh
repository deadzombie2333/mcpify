#!/bin/bash
set -e

# Package mcpify Lambda function
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG="$PROJECT_DIR/config.json"
PACKAGE_DIR="$SCRIPT_DIR/lambda-package"
REGION=$(jq -r '.region' "$CONFIG")
PROJECT_NAME=$(jq -r '.project_name' "$CONFIG")
FUNCTION_NAME="${PROJECT_NAME}-mcpify-lambda"

echo "Packaging Lambda for $FUNCTION_NAME..."

rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR"

# Copy server code
cp "$PROJECT_DIR/mcp-server/lambda_handler.py" "$PACKAGE_DIR/"
cp "$PROJECT_DIR/mcp-server/mcp_server.py" "$PACKAGE_DIR/"
cp -r "$PROJECT_DIR/mcp-server/tools" "$PACKAGE_DIR/"
cp -r "$PROJECT_DIR/mcp-server/auth" "$PACKAGE_DIR/"

# Install deps for Lambda (Amazon Linux)
pip3 install --target "$PACKAGE_DIR" \
    --platform manylinux2014_x86_64 \
    --implementation cp \
    --python-version 3.11 \
    --only-binary=:all: \
    --upgrade \
    fastmcp boto3 opensearch-py requests requests-aws4auth

# Create zip
cd "$PACKAGE_DIR"
zip -r "$SCRIPT_DIR/lambda-deployment.zip" . -q
cd "$SCRIPT_DIR"

PACKAGE_SIZE=$(du -h lambda-deployment.zip | cut -f1)
echo "Package: lambda-deployment.zip ($PACKAGE_SIZE)"

# Upload
PACKAGE_BYTES=$(stat -c%s lambda-deployment.zip 2>/dev/null || stat -f%z lambda-deployment.zip 2>/dev/null)
if [ "$PACKAGE_BYTES" -gt 52428800 ]; then
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    BUCKET="${PROJECT_NAME}-mcpify-deploy-${ACCOUNT_ID}"
    aws s3 mb "s3://$BUCKET" --region "$REGION" 2>/dev/null || true
    aws s3 cp lambda-deployment.zip "s3://$BUCKET/lambda-deployment.zip"
    aws lambda update-function-code \
        --function-name "$FUNCTION_NAME" \
        --s3-bucket "$BUCKET" --s3-key lambda-deployment.zip \
        --region "$REGION"
else
    aws lambda update-function-code \
        --function-name "$FUNCTION_NAME" \
        --zip-file fileb://lambda-deployment.zip \
        --region "$REGION"
fi

aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"
echo "✅ Lambda deployed: $FUNCTION_NAME"
