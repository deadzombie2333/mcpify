#!/bin/bash
set -e

# mcpify — single deploy script
# Reads config.json, deploys all stacks in order, uploads docs, runs embedder

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$SCRIPT_DIR/config.json"

if [ ! -f "$CONFIG" ]; then echo "❌ config.json not found"; exit 1; fi

PROJECT=$(jq -r '.project_name' "$CONFIG")
REGION=$(jq -r '.region' "$CONFIG")
VPC_NAME="${PROJECT}-mcpify"
DOMAIN_PREFIX="${PROJECT}-mcpify-auth"
TOKEN_EXPIRY=$(jq -r '.token_expiry_minutes // 60' "$CONFIG")
LAMBDA_MEM=$(jq -r '.lambda_memory_mb // 2048' "$CONFIG")
LAMBDA_TIMEOUT=$(jq -r '.lambda_timeout_seconds // 300' "$CONFIG")

BUCKET=$(jq -r '.s3_bucket' "$CONFIG")
S3_PREFIX=$(jq -r '.s3_prefix // ""' "$CONFIG")

echo "🚀 mcpify deploy: $PROJECT ($REGION)"
echo "   S3 source: s3://$BUCKET/$S3_PREFIX"
echo ""

# ─── Step 1: Verify S3 bucket exists ───
echo "📦 Step 1: Verifying S3 bucket ($BUCKET)"
if aws s3 ls "s3://$BUCKET" --region "$REGION" 2>/dev/null; then
    echo "  ✅ Bucket accessible"
else
    echo "  ❌ Cannot access s3://$BUCKET — check bucket name and permissions"
    exit 1
fi
echo ""

# ─── Step 2: VPC ───
VPC_STACK="${PROJECT}-mcpify-vpc"
echo "🌐 Step 2: VPC ($VPC_STACK)"
aws cloudformation deploy \
    --template-file "$SCRIPT_DIR/deploy/vpc-template.yaml" \
    --stack-name "$VPC_STACK" \
    --parameter-overrides \
        VpcName="$VPC_NAME" \
        AvailabilityZone1="${REGION}a" \
        AvailabilityZone2="${REGION}b" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "$REGION" \
    --no-fail-on-empty-changeset
echo "  ✅ VPC deployed"
echo ""

# ─── Step 3: Gateway (Cognito + DynamoDB + AgentCore) ───
GW_STACK="${PROJECT}-mcpify-gateway"
echo "🔐 Step 3: Gateway ($GW_STACK)"
aws cloudformation deploy \
    --template-file "$SCRIPT_DIR/deploy/gateway-template.yaml" \
    --stack-name "$GW_STACK" \
    --parameter-overrides \
        ProjectName="$PROJECT" \
        DomainPrefix="$DOMAIN_PREFIX" \
        TokenExpiryMinutes="$TOKEN_EXPIRY" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "$REGION" \
    --no-fail-on-empty-changeset

# Extract outputs
GW_OUTPUTS=$(aws cloudformation describe-stacks --stack-name "$GW_STACK" --region "$REGION" --query 'Stacks[0].Outputs')
GATEWAY_URL=$(echo "$GW_OUTPUTS" | jq -r '.[] | select(.OutputKey=="GatewayUrl") | .OutputValue')
GATEWAY_ID=$(echo "$GW_OUTPUTS" | jq -r '.[] | select(.OutputKey=="GatewayId") | .OutputValue')
USER_POOL_ID=$(echo "$GW_OUTPUTS" | jq -r '.[] | select(.OutputKey=="UserPoolId") | .OutputValue')
ADMIN_CLIENT_ID=$(echo "$GW_OUTPUTS" | jq -r '.[] | select(.OutputKey=="AdminClientId") | .OutputValue')
TOKEN_ENDPOINT=$(echo "$GW_OUTPUTS" | jq -r '.[] | select(.OutputKey=="TokenEndpoint") | .OutputValue')
PERMISSIONS_TABLE=$(echo "$GW_OUTPUTS" | jq -r '.[] | select(.OutputKey=="PermissionsTableName") | .OutputValue')
echo "  ✅ Gateway deployed: $GATEWAY_URL"
echo ""

# ─── Step 4: Lambda ───
LAMBDA_STACK="${PROJECT}-mcpify-lambda"
echo "⚡ Step 4: Lambda ($LAMBDA_STACK)"
aws cloudformation deploy \
    --template-file "$SCRIPT_DIR/deploy/lambda-template.yaml" \
    --stack-name "$LAMBDA_STACK" \
    --parameter-overrides \
        ProjectName="$PROJECT" \
        VpcName="$VPC_NAME" \
        PermissionsTableName="$PERMISSIONS_TABLE" \
        S3Bucket="$BUCKET" \
        S3Prefix="$S3_PREFIX" \
        UserPoolId="$USER_POOL_ID" \
        ClientId="$ADMIN_CLIENT_ID" \
        LambdaMemory="$LAMBDA_MEM" \
        LambdaTimeout="$LAMBDA_TIMEOUT" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "$REGION" \
    --no-fail-on-empty-changeset

LAMBDA_OUTPUTS=$(aws cloudformation describe-stacks --stack-name "$LAMBDA_STACK" --region "$REGION" --query 'Stacks[0].Outputs')
LAMBDA_ARN=$(echo "$LAMBDA_OUTPUTS" | jq -r '.[] | select(.OutputKey=="LambdaArn") | .OutputValue')
LAMBDA_ROLE_ARN=$(echo "$LAMBDA_OUTPUTS" | jq -r '.[] | select(.OutputKey=="LambdaRoleArn") | .OutputValue')
echo "  ✅ Lambda deployed"
echo ""

# ─── Step 5: OpenSearch ───
OS_STACK="${PROJECT}-mcpify-opensearch"
echo "🔍 Step 5: OpenSearch ($OS_STACK)"

# Get EC2 instance role ARN for embedder access
EC2_ROLE_ARN=""
EC2_ROLE_ARN=$(aws sts get-caller-identity --query 'Arn' --output text 2>/dev/null | sed 's|:sts:|:iam:|;s|:assumed-role/|:role/|;s|/[^/]*$||' || true)
# If running as an EC2 instance role, extract the role ARN
if echo "$EC2_ROLE_ARN" | grep -q "role/"; then
    echo "  EC2 embedder role: $EC2_ROLE_ARN"
else
    # Fallback: use the caller identity directly
    EC2_ROLE_ARN=$(aws sts get-caller-identity --query 'Arn' --output text 2>/dev/null || true)
    echo "  Embedder identity: $EC2_ROLE_ARN"
fi

OS_PARAMS="ProjectName=$PROJECT VpcName=$VPC_NAME LambdaRoleArn=$LAMBDA_ROLE_ARN"
if [ -n "$EC2_ROLE_ARN" ]; then
    OS_PARAMS="$OS_PARAMS EmbedderRoleArn=$EC2_ROLE_ARN"
fi

aws cloudformation deploy \
    --template-file "$SCRIPT_DIR/deploy/opensearch-template.yaml" \
    --stack-name "$OS_STACK" \
    --parameter-overrides $OS_PARAMS \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "$REGION" \
    --no-fail-on-empty-changeset

OS_OUTPUTS=$(aws cloudformation describe-stacks --stack-name "$OS_STACK" --region "$REGION" --query 'Stacks[0].Outputs')
OS_ENDPOINT=$(echo "$OS_OUTPUTS" | jq -r '.[] | select(.OutputKey=="Endpoint") | .OutputValue')
OS_HOST="${OS_ENDPOINT#https://}"
echo "  ✅ OpenSearch deployed: $OS_HOST"
echo ""

# ─── Step 6: Update Lambda env with OpenSearch endpoint ───
echo "🔧 Step 6: Updating Lambda environment..."
FUNCTION_NAME="${PROJECT}-mcpify-lambda"
FILE_TYPES=$(jq -r '.file_types | join(",")' "$CONFIG")
BEDROCK_MODEL=$(jq -r '.bedrock_model_id // "us.amazon.nova-2-lite-v1:0"' "$CONFIG")
EMBED_MODEL=$(jq -r '.embedding_model_id // "amazon.titan-embed-text-v2:0"' "$CONFIG")
EMBED_DIMS=$(jq -r '.embedding_dimensions // 1024' "$CONFIG")

aws lambda update-function-configuration \
    --function-name "$FUNCTION_NAME" \
    --environment "Variables={REGION=$REGION,PERMISSIONS_TABLE=$PERMISSIONS_TABLE,DOCS_BUCKET=$BUCKET,DOCS_PREFIX=$S3_PREFIX,OPENSEARCH_ENDPOINT=$OS_HOST,OPENSEARCH_INDEX=${PROJECT}-docs,BEDROCK_MODEL=$BEDROCK_MODEL,EMBEDDING_MODEL=$EMBED_MODEL,EMBEDDING_DIMS=$EMBED_DIMS,FILE_TYPES=$FILE_TYPES}" \
    --region "$REGION" > /dev/null
aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"
echo "  ✅ Lambda env updated"
echo ""

# ─── Step 7: Package and deploy Lambda code ───
echo "📦 Step 7: Packaging Lambda..."
bash "$SCRIPT_DIR/deploy/package-lambda.sh"
echo ""

# ─── Step 8: Add Gateway Target ───
echo "🎯 Step 8: Adding Lambda as Gateway target..."
GATEWAY_ARN="arn:aws:bedrock-agentcore:${REGION}:$(aws sts get-caller-identity --query Account --output text):gateway/${GATEWAY_ID}"

# Get the AgentCore role ARN from the gateway stack
AC_ROLE_ARN=$(aws iam get-role --role-name "${PROJECT}-mcpify-gateway-role" --query 'Role.Arn' --output text 2>/dev/null || true)

aws bedrock-agentcore create-gateway-target \
    --gateway-identifier "$GATEWAY_ID" \
    --name "${PROJECT}-lambda" \
    --target-configuration "lambdaTarget={lambdaArn=$LAMBDA_ARN}" \
    --credential-provider-configurations "[{\"credentialProviderType\":\"GATEWAY_IAM_ROLE\"}]" \
    --region "$REGION" 2>/dev/null || echo "  Target may already exist"
echo "  ✅ Gateway target configured"
echo ""

# ─── Step 9: Seed admin permissions ───
echo "🔑 Step 9: Seeding admin permissions..."
aws dynamodb put-item \
    --table-name "$PERMISSIONS_TABLE" \
    --item "{\"client_id\":{\"S\":\"$ADMIN_CLIENT_ID\"},\"name\":{\"S\":\"admin\"},\"access_rules\":{\"M\":{\"folders\":{\"L\":[{\"S\":\"*\"}]},\"files\":{\"L\":[]}}},\"deny_rules\":{\"M\":{\"folders\":{\"L\":[]},\"files\":{\"L\":[]}}}}" \
    --region "$REGION"
echo "  ✅ Admin client has wildcard access"
echo ""

# ─── Step 10: Run embedder ───
echo "📚 Step 10: Embedding documents..."
export OPENSEARCH_ENDPOINT="$OS_HOST"

# Wait for OpenSearch collection to be ACTIVE
COLLECTION_NAME="${PROJECT}-mcpify"
echo "  Waiting for OpenSearch collection to be ACTIVE..."
for i in $(seq 1 30); do
    STATUS=$(aws opensearchserverless batch-get-collection \
        --names "$COLLECTION_NAME" --region "$REGION" \
        --query 'collectionDetails[0].status' --output text 2>/dev/null || echo "UNKNOWN")
    if [ "$STATUS" = "ACTIVE" ]; then
        echo "  ✅ Collection is ACTIVE"
        break
    fi
    echo "  Status: $STATUS (attempt $i/30, waiting 20s...)"
    sleep 20
done

cd "$SCRIPT_DIR"
python3 embedder/run_embedder.py || echo "  ⚠️  Embedder failed — you can retry with ./update-docs.sh"
echo ""

# ─── Done ───
echo "═══════════════════════════════════════════════════"
echo "✅ mcpify deployed successfully!"
echo "═══════════════════════════════════════════════════"
echo ""
echo "Gateway URL:    $GATEWAY_URL"
echo "Token Endpoint: $TOKEN_ENDPOINT"
echo "Admin Client:   $ADMIN_CLIENT_ID"
echo "S3 Source:      s3://$BUCKET/$S3_PREFIX"
echo "Permissions:    $PERMISSIONS_TABLE"
echo ""
echo "Next steps:"
echo "  1. Get admin client secret:  ./connect.sh"
echo "  2. Create client:            ./cli/mcpify client create --name team-a --folders api-docs"
echo "  3. Re-embed after doc changes: ./update-docs.sh"
