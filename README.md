# RAG Knowledge Assistant with MCP

A serverless retrieval-augmented-generation (RAG) system on AWS. Documents are
ingested into a vector store; users (and AI agents, via MCP) ask questions and
get answers grounded in those documents, with citations.

Built as a learning project targeting AWS SAA-C03 and GitHub GH-600.

## Architecture overview

Two paths share a single retrieval core:

- **Ingestion (write path):** S3 upload → Lambda → extract text → chunk →
  Bedrock embed → Aurora pgvector
- **Query (read path):** HTTP question → Lambda → embed → vector search →
  Bedrock generate → answer + citations

The query path is exposed as a REST API (spec 003) and as an MCP server (spec
004) — both calling the same `src/query/retrieval.py` core.

Services used: S3, Lambda, DynamoDB (spec 001 staging store), Aurora Serverless
v2 + pgvector (vector store), API Gateway v2, Amazon Bedrock (Titan Embeddings
V2 for embeddings, Nova Micro for generation), Secrets Manager.

See [docs/architecture.md](docs/architecture.md) for C4 diagrams and service
rationale, and [docs/roadmap.md](docs/roadmap.md) for the full build sequence.

## Repository layout

```
infra/          Terraform — S3, Aurora, Lambdas, API Gateway, IAM
src/
  ingestion/    handler.py, extractor.py, chunker.py, embedder.py, vector_store.py
  query/        handler.py, retrieval.py
  mcp/          handler.py (FastMCP tools; thin wrapper over src/query/retrieval.py)
tests/          unit tests (mocked, no network)
tests/integration/  end-to-end tests (gated by RUN_AWS_INTEGRATION=1)
evals/
  golden_set.json     labeled Q&A dataset
  scorer.py           pure scoring functions
  run_eval.py         CLI runner
  seed_docs/          synthetic documents used by the evaluation harness
specs/          per-feature spec.md + plan.md + examples.md
docs/           architecture.md, roadmap.md, adr/
scripts/        build.sh, deploy-local.sh, deploy-aws.sh, seed.sh
```

## Running locally (LocalStack)

Spec 001 uses LocalStack; specs 002+ need real AWS for Bedrock and Aurora.

```bash
export LOCALSTACK_AUTH_TOKEN=ls-...   # free Hobby token; required since 2026.03
docker compose up -d
bash scripts/deploy-local.sh

pytest tests/ -q
ruff check .
```

## Running against real AWS

```bash
bash scripts/deploy-aws.sh
RUN_AWS_INTEGRATION=1 pytest tests/integration/ -q
```

## Key environment variables

| Variable | Purpose |
|---|---|
| `BEDROCK_EMBEDDING_MODEL_ID` | Titan Embeddings V2 model ID |
| `BEDROCK_GENERATION_MODEL_ID` | Nova Micro (or other) model ID |
| `AURORA_SECRET_ARN` | Secrets Manager ARN for Aurora credentials |
| `RETRIEVAL_TOP_K` | Number of chunks to retrieve (default 5) |
| `RUN_AWS_INTEGRATION` | Set to `1` to run integration tests against real AWS |
| `EVAL_RETRIEVAL_THRESHOLD` | Minimum retrieval hit rate (default `0.8`) |
| `EVAL_ANSWER_THRESHOLD` | Minimum answer keyword score (default `0.6`) |

## Query API

```
POST /ask
{"question": "What does the onboarding guide say about leave policy?"}

200 OK
{
  "answer": "...",
  "sources": [
    {"source_key": "documents/onboarding.pdf", "chunk_index": 3, "text": "..."}
  ]
}
```

## MCP server

```python
import json, asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client("<mcp_endpoint from terraform output>") as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool(
                "ask_question",
                {"question": "What does the onboarding guide say about leave policy?"},
            )
            print(json.loads(result.content[0].text)["answer"])

asyncio.run(main())
```

Available tools: `search_documents(query)` → chunk list, `ask_question(question)` → answer + sources.

## Evaluation harness

```bash
bash scripts/seed.sh            # upload seed docs; allow ~35 s for ingestion
python evals/run_eval.py        # score retrieval + answer quality; exits 1 on regression
```

Example report:
```json
{
  "retrieval_hit_rate": 1.0,
  "answer_keyword_score": 1.0,
  "thresholds": {"retrieval": 0.8, "answer": 0.6},
  "passed": true
}
```

---

## Spec history

### Spec 001 — Document ingestion skeleton · LocalStack · Done

S3 upload under `documents/` triggers a Lambda that extracts text from `.txt`
and `.pdf` files, splits it into overlapping chunks (~1000 chars, ~100 overlap),
and writes each chunk plus a document-level record to DynamoDB.

Runs entirely on LocalStack. No embeddings yet.

**Key files:** `src/ingestion/handler.py`, `extractor.py`, `chunker.py`, `store.py`

---

### Spec 002 — Embeddings + vector store · real AWS · Done

Extends the ingestion Lambda to embed each chunk with **Bedrock Titan Embeddings
V2** (1536-dimensional vectors, env-configurable model ID) and store it in
**Aurora Serverless v2 + pgvector** via the Data API (no VPC needed). Secrets
Manager holds the Aurora credentials. `min_capacity = 0` lets Aurora pause when
idle.

Includes exponential-backoff retry for Bedrock throttling. Integration tests are
gated by `RUN_AWS_INTEGRATION=1`.

**Key files:** `src/ingestion/embedder.py`, `vector_store.py`, `infra/main.tf`

**Lessons learned:**
- Aurora Data API rejects multi-statement SQL — split DELETEs into separate calls.
- `TIMESTAMPTZ` columns need `:param::timestamptz` cast; plain `stringValue` fails.
- `dynamodb:UpdateItem` must be explicit in IAM — not covered by `PutItem`.
- `pgvector` is activated with `CREATE EXTENSION IF NOT EXISTS vector`, not via
  `shared_preload_libraries`.

---

### Spec 003 — Query / retrieval API · real AWS · Done

Adds the read path: **API Gateway + Lambda** fronting a transport-agnostic
retrieval core (`src/query/retrieval.py`). A `POST /ask` request is embedded,
run through a cosine-distance top-k search in Aurora, and the retrieved chunks
are passed to **Bedrock Nova Micro** via the Converse API to generate a grounded
answer with citations. Returns `"No relevant documents found."` when the vector
search is empty.

The retrieval core is importable standalone — spec 004 (MCP server) reuses it
directly.

**Key files:** `src/query/retrieval.py`, `handler.py`, `infra/main.tf`

**Lessons learned:**
- Claude 3 Haiku is LEGACY in this account — use Amazon Nova models instead.
- Use the **Bedrock Converse API** (`converse()`) for generation; it is
  model-agnostic and avoids per-model JSON format differences.
- `amazon.nova-micro-v1:0` is a good default: cheapest active Nova model, no
  use-case form, works well for RAG Q&A.

---

### Spec 004 — MCP server · real AWS · Done

Exposes the knowledge base as an MCP server with two tools: `search_documents`
(returns relevant chunks from a vector search) and `ask_question` (returns a
grounded answer with citations). Hosted on a dedicated Lambda behind API Gateway
v2. Both tools delegate exclusively to `src/query/retrieval.py` — no retrieval
logic is duplicated.

**Key files:** `src/mcp/handler.py`, `infra/main.tf`

**Lessons learned:**
- **Lambda Function URL + account-level Block Public Access:** AWS accounts with
  the Lambda Public Access Block reject all Function URL requests with 403 before
  the Lambda is invoked. Switched to API Gateway v2 HTTP API (same pattern as
  spec 003).
- **FastMCP on Lambda requires three non-obvious fixes:**
  1. `json_response=True` on `StreamableHTTPSessionManager` — default SSE mode
     keeps the connection open; Lambda BUFFERED mode must return a complete
     response, causing a 120 s hang and API GW 503.
  2. Fresh `StreamableHTTPSessionManager` per invocation — `run()` is one-shot
     per instance; reusing across warm invocations fails.
  3. `enable_dns_rebinding_protection=False` — FastMCP defaults to validating
     `Host` against `127.0.0.1`/`localhost`; the API Gateway domain fails this
     check and returns 421.

---

### Spec 005 — Evaluation harness · CI + real AWS · Done

Introduces a labeled evaluation dataset (`evals/golden_set.json`, 5 Q&A entries)
and a scoring harness that measures **retrieval hit rate** (fraction of questions
where an expected source key appears in the top-k results) and **answer keyword
score** (average fraction of expected keywords present in the generated answer).
`evals/run_eval.py` exits non-zero on regression, gating the `integration-aws`
CI job.

**Key files:** `evals/scorer.py`, `run_eval.py`, `golden_set.json`, `scripts/seed.sh`

**Lessons learned:**
- **`DatabaseResumingException` was unhandled in both ingestion and query.** Aurora
  auto-pauses at `min_capacity = 0`; on cold-start it returns
  `DatabaseResumingException` rather than eventually succeeding. The ingestion
  Lambda was silently discarding all vectors on first run after a pause.
  Added retry with 20 s back-off (matching the existing `ThrottlingException`
  pattern) in both `vector_store._begin_transaction` and `retrieval._search_chunks`.
- Choose golden set keywords that are **proper nouns or numeric strings** (e.g.
  `"99.999999999"`, `"Standard-IA"`) — an LLM cannot paraphrase them away, keeping
  scores stable across model or prompt changes.
