# Plan — Spec 004: MCP Server

## Approach

Single zip, three Lambda handlers. `scripts/build.sh` bundles all of `src/` into
`dist/ingest.zip`. The MCP Lambda reuses that same artifact with handler
`src.mcp.handler.handler` — no second build step needed (same pattern as spec 003's
query Lambda).

**MCP framework: `FastMCP` + `mangum`.**  
The official `mcp` Python SDK's `FastMCP` class provides the high-level tool
registration API (`@mcp.tool()` decorator) and the Streamable HTTP transport
(`mcp.streamable_http_app()` returns a Starlette ASGI app with a single `/mcp` route).
`mangum` wraps the ASGI app for Lambda invocation; it auto-detects the Lambda Function
URL payload format (version 2.0, same shape as API Gateway HTTP API v2) via its
`HTTPGateway` handler.

**Transport: Lambda Function URL (not API Gateway).**  
MCP clients connect to any HTTP URL. A Function URL costs nothing at idle and requires
one Terraform resource (`aws_lambda_function_url`) vs. three API Gateway resources.
`AUTH_TYPE = "NONE"` for now; authentication is spec 006.

**Retrieval core extension.**  
`src/query/retrieval.py` gains one new public function, `retrieve_chunks()`, that runs
embed + vector search without generation. `ask_question` continues to call
`retrieve_and_answer()` unchanged. The MCP handler is a thin wrapper — no retrieval or
generation logic lives in `src/mcp/`.

**Env-gating.**  
Both tools check `BEDROCK_EMBEDDING_MODEL_ID` at call time. When absent (LocalStack
path), they return safe empty/unconfigured responses without raising — same discipline
as `src/query/handler.py`'s 503 check.

---

## Files to create

### `specs/004-mcp-server/spec.md`
Testable acceptance criteria for the MCP server.

### `src/mcp/__init__.py`
Empty — makes `src/mcp` a package.

### `src/mcp/handler.py`
`FastMCP` server with two tools; Mangum-wrapped Lambda entry point:

```python
mcp = FastMCP("rag-assistant")

@mcp.tool()
def search_documents(query: str) -> list[dict]:
    """Return the most relevant document chunks for a query."""
    if not _is_configured():
        return []
    ...  # calls retrieve_chunks()

@mcp.tool()
def ask_question(question: str) -> dict:
    """Ask a question and receive a grounded answer with citations."""
    if not _is_configured():
        return {"answer": "Service not configured.", "sources": []}
    ...  # calls retrieve_and_answer()

handler = Mangum(mcp.streamable_http_app())
```

Both tools import exclusively from `src.query.retrieval`. AWS clients are created
inside the tool functions (not at module level) so unit tests can patch them cleanly.

### `tests/test_mcp_handler.py`
Unit tests with patched retrieval core:
- `search_documents` — happy path (returns chunk list), empty-results path,
  unconfigured path (no env vars), `top_k` passed through correctly
- `ask_question` — happy path (answer + sources), no-docs path, unconfigured path,
  generation model env var passed through correctly

Tools are called directly (the `@mcp.tool()` decorator does not wrap the callable),
so no ASGI test client is needed for unit tests.

### `tests/integration/test_mcp_server.py`
Gated by `RUN_AWS_INTEGRATION=1`. Uses the `mcp` Python client SDK:

```python
async with streamablehttp_client(mcp_url) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()   # MCP handshake
        result = await session.call_tool("ask_question", {"question": "..."})
```

Three tests: `tools/list` asserts both tool names present; `search_documents` asserts
list response; `ask_question` asserts `answer` + `sources` keys present. All are
`@pytest.mark.asyncio`.

---

## Files to modify

### `src/query/retrieval.py`
Add `retrieve_chunks()` before `retrieve_and_answer()`:

```python
def retrieve_chunks(
    rdsdata_client, bedrock_client,
    cluster_arn, secret_arn, database,
    query, embedding_model_id, top_k=5,
) -> list[dict]:
    query_embedding = _embed_question(bedrock_client, query, embedding_model_id)
    chunks = _search_chunks(rdsdata_client, cluster_arn, secret_arn, database,
                            query_embedding, top_k)
    return [
        {"source_key": c["source_key"], "chunk_index": c["chunk_index"], "text": c["text"]}
        for c in chunks
    ]
```

Also extend `tests/test_retrieval.py` with two `retrieve_chunks` tests (happy path and
empty-results).

### `requirements.txt`
Add `mcp`, `mangum`, and `pytest-asyncio`.

### `infra/main.tf`
Add after the API Gateway block:

**MCP Lambda IAM** (mirrors query Lambda — CloudWatch logs + Bedrock `InvokeModel` +
`rds-data:ExecuteStatement` + `secretsmanager:GetSecretValue`):
```hcl
resource "aws_iam_role" "mcp_lambda" { ... }
resource "aws_iam_role_policy" "mcp_lambda" { ... }
```

**MCP Lambda** (`count = local.is_aws ? 1 : 0`):
```hcl
resource "aws_lambda_function" "mcp" {
  count            = local.is_aws ? 1 : 0
  function_name    = "rag-mcp"
  role             = aws_iam_role.mcp_lambda.arn
  filename         = "${path.module}/../dist/ingest.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/ingest.zip")
  handler          = "src.mcp.handler.handler"
  runtime          = "python3.12"
  ...environment vars (embed model, gen model, Aurora ARNs, top_k)...
}
```

**Lambda Function URL** (`count = local.is_aws ? 1 : 0`):
```hcl
resource "aws_lambda_function_url" "mcp" {
  count              = local.is_aws ? 1 : 0
  function_name      = aws_lambda_function.mcp[0].function_name
  authorization_type = "NONE"
  cors {
    allow_origins = ["*"]
    allow_methods = ["POST"]
    allow_headers = ["content-type", "mcp-session-id", "accept"]
  }
}
```

### `infra/outputs.tf`
Add:
```hcl
output "mcp_function_url" {
  description = "Lambda Function URL for the MCP server — append /mcp for the MCP endpoint"
  value       = local.is_aws ? aws_lambda_function_url.mcp[0].function_url : ""
}
```

---

## Risks

| Risk | Mitigation |
|---|---|
| `FastMCP.streamable_http_app()` API drift from training data | Verified against installed `mcp==1.28.0` before writing handler |
| `mangum` event shape mismatch with Lambda Function URL | Confirmed `HTTPGateway` handler reads `version == "2.0"` path which matches Function URL payload |
| MCP `initialize` handshake required before `tools/call` | Integration tests use `ClientSession` which handles the handshake automatically |
| `@mcp.tool()` wrapping breaks direct-call unit tests | Verified: decorator registers tool but does not replace the callable; direct calls work |
| Aurora cold-start latency (~30s) on first MCP call | Same constraint as spec 003 query API; integration tests don't assert latency |

---

## Order of implementation

1. `specs/004-mcp-server/spec.md`
2. Verify `mcp` SDK API (`FastMCP`, `streamable_http_app`, `ClientSession`)
3. Extend `src/query/retrieval.py` with `retrieve_chunks()`
4. Extend `tests/test_retrieval.py` with `retrieve_chunks` tests — green first
5. `src/mcp/__init__.py`, `src/mcp/handler.py`
6. `tests/test_mcp_handler.py` — green before touching infra
7. `requirements.txt` — add `mcp`, `mangum`, `pytest-asyncio`
8. `infra/main.tf` — MCP Lambda + Function URL + IAM
9. `infra/outputs.tf` — `mcp_function_url`
10. `tests/integration/test_mcp_server.py`
11. Full `pytest tests/ -q` + `ruff check .` — green
12. PR
