# Spec 004 — MCP server

- **Status:** In Progress
- **Tracking issue:** TBD
- **Author:** human (principal)

## Context

Spec 003 delivered the REST query API (`POST /ask`) backed by a transport-agnostic
retrieval core (`src/query/retrieval.py`). This spec adds the second transport: an
MCP server that exposes the knowledge base as tools so that any MCP-compatible AI
agent can query it.

Two tools: `search_documents` (returns relevant chunks from a vector search) and
`ask_question` (returns a grounded answer with citations from the full RAG pipeline).
Both tools call `src/query/retrieval.py` directly — the retrieval and generation logic
is never duplicated.

The MCP server is hosted on a dedicated Lambda function, reached via a Lambda Function
URL (no additional API Gateway). The official Python `mcp` SDK (`FastMCP`) is used so
tool schemas are derived from type annotations, and the ASGI app is adapted for Lambda
with `mangum`.

Integration tests require real AWS (Bedrock + Aurora Data API unavailable on LocalStack
free tier), gated behind `RUN_AWS_INTEGRATION=1`.

## User story

As an external AI agent, I want to call `search_documents` and `ask_question` as MCP
tools so that I can query the RAG knowledge base without building my own retrieval logic.

## Acceptance criteria

Each criterion must be verifiable by an automated test.

- [ ] `search_documents(query)` returns a list of `{source_key, chunk_index, text}`
      dicts representing the most semantically relevant chunks; the list is empty when no
      chunks are found.
- [ ] `ask_question(question)` returns a dict with `answer` (string) and `sources`
      (list of `{source_key, chunk_index, text}`); when no relevant documents exist the
      answer is `"No relevant documents found."`.
- [ ] Both tools call `src/query/retrieval.py` exclusively — no retrieval or generation
      logic is duplicated in `src/mcp/`.
- [ ] When `BEDROCK_EMBEDDING_MODEL_ID` is not set (LocalStack path), both tools return
      a safe empty/unconfigured response without raising an exception.
- [ ] `retrieve_chunks()` is added to `src/query/retrieval.py` as a public function
      (embed + vector search, no generation) so the retrieval core remains the single
      source of truth for both transports.
- [ ] A dedicated MCP Lambda (`rag-mcp`) and a Lambda Function URL are provisioned in
      Terraform, gated behind `local.is_aws`; the `mcp_function_url` is emitted as a
      Terraform output.
- [ ] The MCP Lambda shares the same IAM permissions as the query Lambda (CloudWatch
      logs, Bedrock `InvokeModel`, RDS Data API `ExecuteStatement`, Secrets Manager
      `GetSecretValue`).
- [ ] Unit tests cover both tools with mocked retrieval core: happy path, no-results
      path, and the unconfigured (env var absent) path.
- [ ] An integration test (gated by `RUN_AWS_INTEGRATION=1`) uses the `mcp` Python
      client to connect to the deployed Function URL, calls both tools, and asserts the
      response shapes are correct and non-empty.
- [ ] `ruff check .` is clean and CI is green on the pull request.

## Out of scope

- Authentication / access control on the Function URL (spec 006).
- Streaming responses.
- Re-ranking or hybrid search.
- Moving Aurora into a VPC (spec 008).

## Constraints

- Follow all guardrails in `AGENTS.md`. In particular: no secrets committed,
  `min_capacity = 0` on Aurora must not be changed, no OpenSearch Serverless.
- The retrieval core (`src/query/retrieval.py`) must be the only place retrieval and
  generation logic lives. The MCP handler is a thin wrapper over it.
- Bedrock model IDs are configuration, not constants — read from environment variables.
- All infrastructure (Lambda, Function URL, IAM) goes through Terraform in `infra/`.
- The `documents/` S3 event filter must not be touched.
- Aurora is accessed exclusively via the Data API — do not add VPC configuration.
