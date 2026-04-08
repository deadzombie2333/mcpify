#!/bin/bash
set -e

# mcpify — output MCP connection config for AI tools
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$SCRIPT_DIR/config.json"

PROJECT=$(jq -r '.project_name' "$CONFIG")
REGION=$(jq -r '.region' "$CONFIG")
GW_STACK="${PROJECT}-mcpify-gateway"

# Get gateway outputs
OUTPUTS=$(aws cloudformation describe-stacks --stack-name "$GW_STACK" --region "$REGION" --query 'Stacks[0].Outputs')
GATEWAY_URL=$(echo "$OUTPUTS" | jq -r '.[] | select(.OutputKey=="GatewayUrl") | .OutputValue')
TOKEN_ENDPOINT=$(echo "$OUTPUTS" | jq -r '.[] | select(.OutputKey=="TokenEndpoint") | .OutputValue')
ADMIN_CLIENT_ID=$(echo "$OUTPUTS" | jq -r '.[] | select(.OutputKey=="AdminClientId") | .OutputValue')
USER_POOL_ID=$(echo "$OUTPUTS" | jq -r '.[] | select(.OutputKey=="UserPoolId") | .OutputValue')

# Get admin client secret
CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
    --user-pool-id "$USER_POOL_ID" \
    --client-id "$ADMIN_CLIENT_ID" \
    --region "$REGION" \
    --query 'UserPoolClient.ClientSecret' --output text)

SCOPE="${PROJECT}-mcpify/mcp:access"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  mcpify — MCP Connection Config (admin)"
echo "═══════════════════════════════════════════════════"
echo ""
echo "Gateway URL:    $GATEWAY_URL"
echo "Token Endpoint: $TOKEN_ENDPOINT"
echo "Client ID:      $ADMIN_CLIENT_ID"
echo "Client Secret:  $CLIENT_SECRET"
echo "Scope:          $SCOPE"
echo ""
echo "── Paste into your AI tool config ──"
echo ""
echo '{
  "mcpServers": {
    "'$PROJECT'": {
      "url": "'$GATEWAY_URL'",
      "auth": {
        "type": "oauth2",
        "token_url": "'$TOKEN_ENDPOINT'",
        "client_id": "'$ADMIN_CLIENT_ID'",
        "client_secret": "'$CLIENT_SECRET'",
        "scope": "'$SCOPE'"
      }
    }
  }
}'
echo ""
echo "── For specific clients, use: ./cli/mcpify client connect --name <name> ──"
