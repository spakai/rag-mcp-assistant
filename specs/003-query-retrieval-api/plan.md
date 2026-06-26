# Plan — Spec 003: Query / Retrieval API

## Approach

Single zip, two Lambda handlers. `scripts/build.sh` already bundles all of
`src/` into `dist/ingest.zip`. The query Lambda reuses that same artifact with
handler `src.query.handler.handler` — no second build step needed.

API Gateway HTTP API v2 (`protocol_type = "HTTP"`) with a `$default` stage:
simpler and cheaper than REST API v1, sufficient for a single `POST /ask` route,
and produces a clean URL with no stage prefix.

Feature flag pattern (same as ingestion): query Lambda reads
`BEDROCK_EMBEDDING_MODEL_ID` and `BEDROCK_GENERATION_MODEL_ID` from env vars.
When either is absent (LocalStack path), the handler returns `503` — no
environment-detection code, just a missing-config check.

---

## Files to create

### `src/query/__init__.py`
Empty — makes `src/query` a package.

### `src/query/retrieval.py`
Transport-agnostic retrieval core. One public entry point:

```python
def retrieve_and_answer(
    rdsdata_client,
    bedrock_client,
    cluster_arn: str,
    secret_arn: str,
    database: str,
    question: str,
    embedding_model_id: str,
    generation_model_id: str,
    top_k: int = 5,
) -> dict:
    # returns {"answer": str, "sources": [{"source_key", "chunk_index", "text"}]}
```

Internal helpers:
- `_embed_question(bedrock_client, question, model_id)` — same Bedrock call as
  `src/ingestion/embedder.py`'s `_embed_text`; exponential backoff on
  `ThrottlingException`
- `_search_chunks(rdsdata_client, cluster_arn, secret_arn, database, query_embedding, top_k)` —
  executes one `execute_statement` (no transaction needed for a read):
  ```sql
  SELECT chunk_index, text, source_key
  FROM chunks
  ORDER BY embedding <=> :qv::vector
  LIMIT :k
  ```
  Returns `[]` on empty result.
- `_generate_answer(bedrock_client, chunks, question, model_id)` — builds the
  prompt from retrieved chunks, calls `invoke_model` with Claude Messages API
  JSON; returns `"No relevant documents found."` when chunks is empty without
  calling Bedrock.

### `src/query/handler.py`
API Gateway HTTP API v2 Lambda handler (payload format version 2.0):

```python
def handler(event, context):
    # 1. Read env vars
    # 2. If models not configured → 503
    # 3. Parse event["body"] JSON → extract "question"
    # 4. If question missing → 400
    # 5. Call retrieve_and_answer(...)
    # 6. Return {"statusCode": 200, "body": json.dumps(result)}
```

Boto3 clients created once at handler invocation — same pattern as
`src/ingestion/handler.py` (no explicit endpoint_url for bedrock-runtime and
rds-data).

### `tests/test_retrieval.py`
Unit tests with `MagicMock` clients:
- Happy path: mocked search returns 2 chunks, mocked Bedrock returns answer →
  verify response shape
- No-results path: empty search → `"No relevant documents found."`, `sources == []`,
  Bedrock generation NOT called
- Throttling: `ThrottlingException` on first embed attempt, succeeds on retry
- Sources list shape: each entry has `source_key`, `chunk_index`, `text`

### `tests/integration/test_query_api.py`
Gated by `RUN_AWS_INTEGRATION=1`. Steps:
1. Read `api_endpoint` and `bucket_name` from `terraform output -json`
2. Upload a known `.txt` to S3, poll DynamoDB for `status == "embedded"` (120s)
3. `POST /ask` with a question whose answer is in the document
4. Assert HTTP 200, non-empty `answer`, at least one `source_key` matching the upload
5. `POST /ask` with an off-topic question → `sources == []` or answer is the
   no-documents fallback

---

## Files to modify

### `src/query/` directory (new)
No changes to existing files.

### `infra/main.tf`
Add after the existing ingest Lambda block:

**Query Lambda** — same zip, different handler and env vars:
```hcl
resource "aws_lambda_function" "query" {
  function_name    = "rag-query"
  role             = aws_iam_role.query_lambda.arn
  filename         = "${path.module}/../dist/ingest.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/ingest.zip")
  handler          = "src.query.handler.handler"
  runtime          = "python3.12"
  memory_size      = var.lambda_memory_mb
  timeout          = var.lambda_timeout_seconds
  environment {
    variables = {
      BEDROCK_EMBEDDING_MODEL_ID  = var.bedrock_embedding_model_id
      BEDROCK_GENERATION_MODEL_ID = var.bedrock_generation_model_id
      AURORA_CLUSTER_ARN          = local.is_aws ? aws_rds_cluster.aurora[0].arn : ""
      AURORA_SECRET_ARN           = local.is_aws ? aws_secretsmanager_secret.aurora[0].arn : ""
      AURORA_DATABASE             = var.aurora_database_name
      RETRIEVAL_TOP_K             = tostring(var.retrieval_top_k)
    }
  }
}
```

**Query Lambda IAM** — Bedrock + rds-data + secretsmanager + logs only (no S3 or DynamoDB):
```hcl
resource "aws_iam_role" "query_lambda" { ... }
resource "aws_iam_role_policy" "query_lambda" { ... }
```

**API Gateway HTTP API v2**:
```hcl
resource "aws_apigatewayv2_api" "query"         # protocol_type = "HTTP"
resource "aws_apigatewayv2_stage" "query"       # name = "$default", auto_deploy = true
resource "aws_apigatewayv2_integration" "query" # AWS_PROXY, payload_format_version = "2.0"
resource "aws_apigatewayv2_route" "ask"         # route_key = "POST /ask"
resource "aws_lambda_permission" "allow_apigw_query"
```

### `infra/variables.tf`
Add:
```hcl
variable "bedrock_generation_model_id" {
  description = "Bedrock model ID for answer generation. Empty disables generation (LocalStack)."
  type        = string
  default     = ""
}

variable "retrieval_top_k" {
  description = "Number of chunks to retrieve per query."
  type        = number
  default     = 5
}
```

### `infra/outputs.tf`
Add:
```hcl
output "api_endpoint" {
  description = "Invoke URL for POST /ask"
  value       = "${aws_apigatewayv2_api.query.api_endpoint}/ask"
}
```

### `scripts/deploy-aws.sh`
Pass generation model to Terraform apply:
```bash
GENERATION_MODEL="${BEDROCK_GENERATION_MODEL_ID:-anthropic.claude-3-haiku-20240307-v1:0}"

terraform apply ... \
  -var "bedrock_embedding_model_id=$BEDROCK_MODEL" \
  -var "bedrock_generation_model_id=$GENERATION_MODEL"
```

### `.github/workflows/ci.yml`
Update `integration-aws` job:
```yaml
env:
  BEDROCK_EMBEDDING_MODEL_ID: amazon.titan-embed-text-v2:0
  BEDROCK_GENERATION_MODEL_ID: anthropic.claude-3-haiku-20240307-v1:0
...
- name: Run real-AWS integration tests
  run: RUN_AWS_INTEGRATION=1 pytest tests/integration/ -v
```

---

## Risks

| Risk | Mitigation |
|---|---|
| Claude Haiku not enabled in Bedrock account | Model ID is env var — check console, swap if needed |
| Aurora paused on first query (cold-start latency ~30s) | Integration test polls before asserting |
| pgvector IVFFlat index needs `ANALYZE` after first data load | Index was created in spec 002 init_db.py; no new DDL needed |
| API Gateway LocalStack support | HTTP API v2 is supported; Lambda returns 503 without models set |

---

## Order of implementation

1. `src/query/__init__.py`, `src/query/retrieval.py`
2. `tests/test_retrieval.py` — green before writing handler
3. `src/query/handler.py`
4. `infra/` changes (variables → main → outputs)
5. `scripts/deploy-aws.sh`, `.github/workflows/ci.yml`
6. `tests/integration/test_query_api.py`
7. Real-AWS smoke test, then PR

---

## Human approval required before implementation begins
