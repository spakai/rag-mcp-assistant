# RAG Knowledge Assistant with MCP

A serverless retrieval-augmented-generation (RAG) system on AWS. Upload documents; ask questions and get answers grounded in those documents with source citations. Built as a learning project targeting **AWS SAA-C03** and **GitHub GH-600** (AI engineering).

The query path is exposed as both a REST API and an **MCP server** so any AI agent can use it as a tool.

---

## Architecture

Two paths share one retrieval core:

```
Upload document → S3 → Lambda → Bedrock (embed) → Aurora pgvector
                                                         ↓
Ask question   → API Gateway → Lambda → Bedrock (embed + generate) → answer + citations
                                             ↑
MCP agent      → API Gateway → MCP Lambda ──┘  (same retrieval core)
```

| Component | Technology |
|---|---|
| Document storage | Amazon S3 (`documents/` prefix triggers ingestion) |
| Ingestion | AWS Lambda (extract → chunk → embed → store) |
| Vector store | Aurora Serverless v2 + pgvector via the Data API |
| Query API | API Gateway v2 + Lambda (`POST /ask`) |
| MCP server | API Gateway v2 + Lambda (Streamable HTTP, FastMCP) |
| Embeddings + generation | Amazon Bedrock (Titan embed v2, Nova Micro) |
| Secrets | AWS Secrets Manager (Aurora credentials) |
| Infrastructure | Terraform |

Full C4 diagrams and ADRs: [`docs/architecture.md`](docs/architecture.md).

---

## Specs — build sequence

Each spec is a complete agentic loop: spec → plan → implement test-first → PR → CI → merge.

| # | Name | Infra target | What it adds |
|---|---|---|---|
| [001](specs/001-document-ingestion/) | Document ingestion skeleton | LocalStack | S3 upload triggers Lambda; text extracted, split into overlapping chunks, stored in DynamoDB |
| [002](specs/002-embeddings-vector-store/) | Embeddings + vector store | Real AWS | Bedrock Titan embeddings; Aurora Serverless v2 + pgvector; chunks stored as vectors |
| [003](specs/003-query-retrieval-api/) | Query / retrieval API | Real AWS | API Gateway + Lambda; embed question → top-k vector search → Bedrock generation with citations |
| [004](specs/004-mcp-server/) | MCP server | Real AWS | FastMCP over Lambda; `search_documents` and `ask_question` tools; same retrieval core as REST API |
| [005](specs/005-evaluation-harness/) | Evaluation harness | CI + Real AWS | Labeled golden set; retrieval hit rate + answer keyword scorer; CI gate on regression |

---

## Quick start

### Prerequisites

- Python 3.12, Terraform, AWS CLI
- AWS account with Bedrock model access (Titan Embed v2, Nova Micro) in `us-east-1`
- For LocalStack (spec 001): Docker, a free [LocalStack Hobby token](https://app.localstack.cloud)

### Run unit tests (no infra needed)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest tests/ -q
ruff check .
```

### Deploy to AWS

```bash
export BEDROCK_EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0
export BEDROCK_GENERATION_MODEL_ID=amazon.nova-micro-v1:0
bash scripts/deploy-aws.sh
```

This builds the Lambda zip, runs `terraform apply`, and initialises the Aurora schema.

### Ingest a document

```bash
aws s3 cp my-doc.pdf s3://$(cd infra && terraform output -raw bucket_name)/documents/my-doc.pdf
```

The ingestion Lambda fires automatically. Allow ~30 s for the chunks to be embedded and stored.

### Query the REST API

```bash
curl -s -X POST https://<api-id>.execute-api.us-east-1.amazonaws.com/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the five pillars of the Well-Architected Framework?"}' | jq .
```

Response:
```json
{
  "answer": "The five pillars are Operational Excellence, Security, Reliability ...",
  "sources": [
    {"source_key": "documents/my-doc.pdf", "chunk_index": 0, "text": "..."}
  ]
}
```

### Use the MCP server from an AI agent

```python
import json, asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL = "https://<mcp-id>.execute-api.us-east-1.amazonaws.com/mcp"

async def main():
    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "ask_question",
                {"question": "What are the five pillars of the Well-Architected Framework?"},
            )
            payload = json.loads(result.content[0].text)
            print(payload["answer"])

asyncio.run(main())
```

Available tools: `search_documents(query)` → chunk list, `ask_question(question)` → answer + sources.

### Run the evaluation harness

```bash
bash scripts/seed.sh                          # upload seed docs; wait ~35 s for ingestion
python evals/run_eval.py                      # scores retrieval + answer quality; exits 1 on regression
```

Example output:
```json
{
  "retrieval_hit_rate": 1.0,
  "answer_keyword_score": 1.0,
  "thresholds": {"retrieval": 0.8, "answer": 0.6},
  "passed": true
}
```

---

## Repository layout

```
src/
  ingestion/    # extract → chunk → embed → store (Lambda handler + helpers)
  query/        # retrieval.py: shared core for REST API and MCP
  mcp/          # FastMCP handler; thin wrapper over src/query/retrieval.py
infra/          # Terraform: S3, Lambda, Aurora, API Gateway, IAM
tests/
  test_*.py                 # unit tests (mocked, no network)
  integration/test_*.py     # end-to-end tests (LocalStack or real AWS)
evals/
  golden_set.json           # 5 labeled Q&A entries
  scorer.py                 # pure scoring functions
  run_eval.py               # CLI runner
  seed_docs/                # synthetic documents for evaluation
scripts/
  build.sh                  # zip Lambda package → dist/ingest.zip
  deploy-aws.sh             # build + terraform apply + init Aurora schema
  deploy-local.sh           # LocalStack equivalent
  seed.sh                   # upload evaluation seed documents
specs/NNN-name/
  spec.md   # acceptance criteria
  plan.md   # implementation plan
  examples.md               # payload / output examples
docs/
  architecture.md           # C4 diagrams and service decisions
  roadmap.md                # spec sequence 001–008
  adr/                      # architecture decision records
```

---

## Development workflow

1. `pytest tests/ -q` and `ruff check .` — run constantly while iterating
2. LocalStack for specs that don't need Bedrock/Aurora: `docker compose up -d && bash scripts/deploy-local.sh`
3. Real AWS for embeddings, Aurora, Bedrock: `bash scripts/deploy-aws.sh`
4. Never push directly to `main` — open a PR and let CI gate it

For a new feature: read [`AGENTS.md`](AGENTS.md) for the full workflow.

---

## Cost notes

- **Aurora** is configured with `min_capacity = 0` so it pauses when idle (near-zero cost at rest). First query after a pause incurs a cold-start (~20–40 s); the code retries `DatabaseResumingException` automatically.
- **Bedrock** charges per token. Nova Micro is used for generation (cheapest generally-available model).
- **No NAT gateways, no OpenSearch Serverless** — see [`docs/architecture.md`](docs/architecture.md) for cost decisions.
- Teardown: `aws s3 rm s3://<bucket> --recursive && cd infra && terraform destroy`

---

## Roadmap

Completed: specs 001–005 (full RAG + MCP + evaluation).

Optional hardening specs (any order):

| # | Name | What it adds |
|---|---|---|
| 006 | Auth + multi-tenancy | Cognito; per-user document isolation |
| 007 | Step Functions orchestration | State machine for ingestion retries and visibility |
| 008 | VPC hardening | Aurora in private subnets; VPC endpoints; Lambda in VPC |

See [`docs/roadmap.md`](docs/roadmap.md) for details.
