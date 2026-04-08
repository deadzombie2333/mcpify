# mcpify

Turn any S3 document bucket into a secure, permission-controlled MCP (Model Context Protocol) server. AI tools connect via OAuth2 through an Amazon Bedrock AgentCore Gateway and get semantic search, Q&A, and document browsing — all scoped to per-client access rules.

## Architecture

```
AI Tool (Claude, Kiro, etc.)
    │  OAuth2 (client_credentials)
    ▼
Cognito User Pool ──► JWT token
    │
    ▼
AgentCore Gateway (MCP protocol, JWT validation)
    │
    ▼
Lambda (VPC, Python 3.11)
    ├── auth/        → JWT extraction + DynamoDB permission lookup
    └── tools/
        ├── document_search    → Semantic search via OpenSearch Serverless
        ├── document_assistant → LLM Q&A via Bedrock (reads S3 docs)
        └── list_documents     → Browse accessible files in S3
```

**Infrastructure (CloudFormation):**

| Stack | Resources |
|-------|-----------|
| `*-vpc` | VPC, public/private subnets, NAT gateways, VPC endpoints (S3, Bedrock, OpenSearch, Cognito, DynamoDB, Lambda) |
| `*-gateway` | Cognito user pool + domain, AgentCore Gateway, DynamoDB permissions table |
| `*-lambda` | Lambda function (read-only S3 + DynamoDB, Bedrock invoke, OpenSearch access) |
| `*-opensearch` | OpenSearch Serverless vector collection (VPC access for Lambda, public for embedder) |

**Embedder (runs on EC2):**
- Reads documents from S3 (supports md, txt, json, pdf, docx)
- Chunks by markdown sections/paragraphs/sentences
- Generates embeddings via Amazon Titan Embed v2
- Indexes to OpenSearch Serverless with content-hash-based incremental sync
- PDF/DOCX support via OCR through Bedrock Nova vision

## Prerequisites

- An AWS account with permissions to create VPC, Lambda, Cognito, DynamoDB, OpenSearch Serverless, Bedrock AgentCore, and IAM resources
- An EC2 instance (Amazon Linux 2023 recommended) for running setup, deployment, and the embedder
- AWS CLI configured with appropriate credentials
- An existing S3 bucket containing your documents
- Bedrock model access enabled for:
  - `amazon.titan-embed-text-v2:0` (embeddings)
  - `us.amazon.nova-2-lite-v1:0` (Q&A and PDF/DOCX OCR, or your chosen model)

## Quick Start

### 1. Clone and setup

```bash
git clone <repo-url> && cd mcpify
./setup.sh
```

This installs Python 3.11, AWS CLI, jq, and Python dependencies (boto3, opensearch-py, pdf2image, python-docx). For PDF support it also installs poppler-utils and LibreOffice headless.

### 2. Configure

Edit `config.json`:

```json
{
  "project_name": "my-docs",
  "region": "us-west-2",
  "s3_bucket": "my-company-docs-bucket",
  "s3_prefix": "",
  "file_types": ["md", "txt", "json", "pdf", "docx"],
  "bedrock_model_id": "us.amazon.nova-2-lite-v1:0",
  "embedding_model_id": "amazon.titan-embed-text-v2:0",
  "embedding_dimensions": 1024,
  "chunk_max_size": 3000,
  "opensearch_top_k": 5,
  "lambda_memory_mb": 2048,
  "lambda_timeout_seconds": 300,
  "token_expiry_minutes": 1440
}
```

| Field | Description |
|-------|-------------|
| `project_name` | Prefix for all AWS resources (stacks, functions, collections) |
| `region` | AWS region for deployment |
| `s3_bucket` | Existing S3 bucket with your documents |
| `s3_prefix` | Optional path prefix within the bucket |
| `file_types` | File extensions to index |
| `bedrock_model_id` | LLM for Q&A and OCR |
| `embedding_model_id` | Model for vector embeddings |
| `embedding_dimensions` | Embedding vector size |
| `chunk_max_size` | Max characters per document chunk |
| `opensearch_top_k` | Default number of search results |
| `lambda_memory_mb` | Lambda memory allocation |
| `lambda_timeout_seconds` | Lambda timeout |
| `token_expiry_minutes` | OAuth2 access token TTL |

### 3. Deploy

```bash
./deploy.sh
```

This runs 10 steps in order:
1. Verify S3 bucket access
2. Deploy VPC with VPC endpoints
3. Deploy Cognito + AgentCore Gateway + DynamoDB permissions table
4. Deploy Lambda function
5. Deploy OpenSearch Serverless collection
6. Update Lambda environment variables with OpenSearch endpoint
7. Package and upload Lambda code
8. Register Lambda as AgentCore Gateway target
9. Seed admin permissions (wildcard access)
10. Run the embedder to index documents

On completion it prints the Gateway URL, Token Endpoint, and Admin Client ID.

### 4. Connect an AI tool

```bash
./connect.sh
```

This outputs the MCP server config JSON you paste into your AI tool:

```json
{
  "mcpServers": {
    "my-docs": {
      "url": "<gateway-url>",
      "auth": {
        "type": "oauth2",
        "token_url": "<token-endpoint>",
        "client_id": "<client-id>",
        "client_secret": "<client-secret>",
        "scope": "my-docs-mcpify/mcp:access"
      }
    }
  }
}
```

## Client Management

The `cli/mcpify` tool manages per-client access from the EC2 control plane.

### Create a client

```bash
./cli/mcpify client create --name team-a --folders api-docs,guides
```

Creates a Cognito app client, registers it with the AgentCore Gateway, and writes folder-level permissions to DynamoDB. Outputs the client ID and secret.

### Grant additional access

```bash
./cli/mcpify client grant --name team-a --folders internal-docs
./cli/mcpify client grant --name team-a --files specs/roadmap.md
```

### Deny access

```bash
./cli/mcpify client deny --name team-a --folders confidential
```

Deny rules take precedence over allow rules.

### Revoke a client

```bash
./cli/mcpify client revoke --name team-a
```

Deletes the Cognito app client, removes permissions from DynamoDB, and updates the Gateway allowed clients list.

### List all clients

```bash
./cli/mcpify client list
```

### Get connection config for a client

```bash
./cli/mcpify client connect --name team-a
```

## Permission Model

Permissions are stored in DynamoDB and evaluated per request:

1. Explicit deny is checked first (deny wins)
2. `folders: ["*"]` grants access to everything
3. Folder-level match: any parent folder in `access_rules.folders`
4. File-level match: exact path in `access_rules.files`

For semantic search, permissions are translated into OpenSearch bool filters so results only include accessible documents.

## Updating Documents

When documents change in S3:

```bash
./update-docs.sh
```

The embedder uses content-hash comparison for incremental sync:
- New files are chunked, embedded, and indexed
- Changed files have old chunks deleted and new ones indexed
- Deleted files are removed from the index
- Unchanged files are skipped

## MCP Tools

Connected AI tools get access to three tools:

| Tool | Trigger | Description |
|------|---------|-------------|
| `document_search` | `query` parameter | Semantic vector search across indexed documents |
| `document_assistant` | `question` parameter | LLM answers questions using accessible S3 documents |
| `list_documents` | `list_documents` parameter or no query/question | Browse accessible documents, optionally filtered by folder |

All tools are automatically filtered to the caller's permissions.

## Project Structure

```
mcpify/
├── config.json              # Project configuration
├── setup.sh                 # One-time EC2 dependency setup
├── deploy.sh                # Full deployment (all stacks + embedder)
├── connect.sh               # Output MCP connection config
├── update-docs.sh           # Re-embed documents from S3
├── cli/
│   └── mcpify               # Client management CLI
├── deploy/
│   ├── vpc-template.yaml    # VPC + VPC endpoints
│   ├── gateway-template.yaml # Cognito + AgentCore Gateway + DynamoDB
│   ├── lambda-template.yaml # Lambda function
│   ├── opensearch-template.yaml # OpenSearch Serverless collection
│   └── package-lambda.sh    # Lambda packaging and upload
├── embedder/
│   ├── run_embedder.py      # Embedder entry point
│   └── s3_embedder.py       # S3 → chunk → embed → OpenSearch
└── mcp-server/
    ├── lambda_handler.py    # Lambda entry point (auth + routing)
    ├── mcp_server.py        # FastMCP server definition
    ├── requirements.txt
    ├── auth/
    │   ├── jwt_utils.py     # JWT client_id extraction
    │   └── permissions.py   # DynamoDB lookup + path filtering + OpenSearch filter builder
    └── tools/
        ├── document_search.py    # Semantic search via OpenSearch
        ├── document_assistant.py # LLM Q&A from S3 docs
        └── list_documents.py     # S3 document listing
```
