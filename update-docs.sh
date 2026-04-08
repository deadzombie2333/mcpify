#!/bin/bash
set -e

# mcpify — re-embed documents from S3
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$SCRIPT_DIR/config.json"

PROJECT=$(jq -r '.project_name' "$CONFIG")
REGION=$(jq -r '.region' "$CONFIG")
BUCKET=$(jq -r '.s3_bucket' "$CONFIG")
PREFIX=$(jq -r '.s3_prefix // ""' "$CONFIG")
OS_STACK="${PROJECT}-mcpify-opensearch"

echo "📚 Re-embedding from s3://$BUCKET/$PREFIX"

OS_ENDPOINT=$(aws cloudformation describe-stacks --stack-name "$OS_STACK" --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`Endpoint`].OutputValue' --output text)
export OPENSEARCH_ENDPOINT="${OS_ENDPOINT#https://}"

cd "$SCRIPT_DIR"
python3 embedder/run_embedder.py

echo "✅ Done"
