# RAG Knowledge Assistant

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

The query path is exposed as a REST API (spec 003) and, in spec 004, as an MCP
server — both calling the same `src/query/retrieval.py` core.

Services used: S3, Lambda, DynamoDB (spec 001 staging store), Aurora Serverless
v2 + pgvector (vector store), API Gateway, Amazon Bedrock (Titan Embeddings V2
for embeddings, Nova Micro for generation), Secrets Manager.

See [docs/architecture.md](docs/architecture.md) for C4 diagrams and service
rationale, and [docs/roadmap.md](docs/roadmap.md) for the full build sequence.

## Repository layout

```
infra/          Terraform — S3, Aurora, Lambdas, API Gateway, IAM
src/
  ingestion/    handler.py, extractor.py, chunker.py, embedder.py, vector_store.py
  query/        handler.py, retrieval.py
tests/          unit tests (mocked, no network)
tests/integration/  end-to-end tests (gated by RUN_AWS_INTEGRATION=1)
specs/          per-feature spec.md + plan.md
docs/           architecture.md, roadmap.md
scripts/        deploy-local.sh, deploy-aws.sh, seed helpers
```

## Running locally (LocalStack)

Specs 001 uses LocalStack; specs 002+ need real AWS for Bedrock and Aurora.

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
answer with citations. Returns `"no relevant documents found"` when the vector
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
