# AGENTS.md

Operating guide for AI coding agents working in this repository. Read this fully
before planning or editing. Humans: this doubles as the contributor guide.

## What this project is

A serverless retrieval-augmented-generation (RAG) knowledge assistant on AWS,
built as a learning project for AWS SAA-C03 and GitHub GH-600. Documents are
ingested into a vector store; users and other AI agents (via MCP) ask questions
and get answers grounded in those documents, with citations.

Two paths: an **ingestion** path (document -> extract -> chunk -> embed -> store)
and a **query** path (question -> embed -> vector search -> generate answer).
The query path is exposed over both a REST API and an MCP server.

Architecture and the build sequence are authoritative in `docs/`:
`docs/architecture.md` (C4 diagrams, service choices) and `docs/roadmap.md`
(features 001-008). The system is built incrementally per the roadmap — not all
of it exists at once. Check the roadmap for what the current spec is in scope of.

## Repository layout

Directories appear as features land; this is the target shape.

- `infra/` — Terraform: S3, the vector store, Lambdas, IAM, events.
- `src/` — application code: ingestion handler, query/retrieval core, MCP server.
- `tests/` — `tests/` unit tests (mocked, no network); `tests/integration/`
  end-to-end tests.
- `specs/NNN-name/` — one folder per feature: `spec.md` (the requirement) and
  `plan.md` (the agent's plan + human approval).
- `docs/` — `architecture.md`, `roadmap.md`, and `adr/` (decision records).
- `scripts/` — build / deploy-local / deploy-aws / seed helpers.
- `.github/` — CI workflow and issue/PR templates.

## How to run things

Unit tests (fast, no infra — run constantly while iterating):
```
pytest tests/ -q
ruff check .
```

Local end-to-end against LocalStack (for specs that don't need Bedrock/Aurora):
```
export LOCALSTACK_AUTH_TOKEN=ls-...     # required since LocalStack 2026.03
docker compose up -d
bash scripts/deploy-local.sh
```

Real AWS (required from spec 002 onward — Bedrock + Aurora):
```
bash scripts/deploy-aws.sh
```

## Conventions

- Python 3.12. Keep `ruff check .` clean — CI fails on lint errors.
- The same handler code runs on LocalStack and AWS. Behaviour differences are
  controlled by environment variables, never by code that detects the
  environment.
- Terraform is the only way infrastructure changes; add resources to `infra/`,
  never create them imperatively in scripts or code.
- Tests define "done." A feature is complete only when a test asserts its
  acceptance criteria and passes in CI.
- One retrieval core: the REST API and the MCP server must call the same query
  logic, so their behaviour cannot drift. Do not duplicate retrieval/generation
  logic across the two transports.

## Guardrails — do not violate

### Cost (this project can spend real money — treat with care)
- **Never use Amazon OpenSearch Serverless as the vector store.** It has a large
  fixed monthly floor. The chosen store is Aurora Serverless v2 + pgvector
  (see ADR), with S3 Vectors as the documented cheaper alternative.
- Aurora must be configured to idle cheaply (`min_capacity = 0` so it can pause).
  Do not raise the minimum capacity without a recorded reason.
- Do not introduce always-on resources (NAT gateways, provisioned clusters,
  always-warm endpoints) without flagging the cost in the plan first.
- Deploy scripts and docs must keep teardown easy: empty the bucket, then
  `terraform destroy`. Never make a resource that blocks clean teardown without
  saying so.

### Secrets
- **Never commit secrets.** Aurora credentials come from AWS Secrets Manager at
  runtime; `LOCALSTACK_AUTH_TOKEN` and any AWS credentials come from the
  environment or GitHub Actions secrets. None may appear in committed files,
  including Terraform, tests, or fixtures. Bedrock uses IAM, not API keys — do
  not invent or store a Bedrock key.

### Pipeline safety
- **Keep the `documents/` S3 event filter.** Ingestion is triggered only for the
  `documents/` prefix. Any derived objects must be written elsewhere; broadening
  the filter risks a re-trigger loop.
- **Bedrock and Aurora are env-gated.** They are unavailable on LocalStack's free
  tier. Code that calls them must be guarded by an environment flag and tolerate
  their absence locally, so the LocalStack path stays green. (Same pattern the
  prior project used for Rekognition.)
- Bedrock model IDs are configuration, not constants — read them from environment
  variables so they can change without a code edit. Handle throttling with retry/
  backoff.

### Process
- **Never push directly to `main`.** Open a pull request and let CI gate it.
- **Build before apply.** Terraform references built Lambda artifacts; run the
  build (the deploy scripts do this) before any `apply`.

## Workflow for a new feature

1. Start from a GitHub Issue whose acceptance criteria are testable conditions.
   The spec lives in `specs/NNN-name/spec.md`.
2. Propose a plan in Plan Mode (no code): which files change, what tests, what
   risks, what infrastructure and cost impact. Wait for human approval.
3. Implement test-first: write/extend the test that encodes a criterion before
   the code that satisfies it.
4. Run `pytest tests/ -q` and `ruff check .` locally until green.
5. Open a pull request linking the issue (`Closes #N`) and filling the template.
   CI re-verifies on a clean machine; green CI gates merge.
6. If the feature involved a significant, hard-to-reverse decision, add an ADR
   in `docs/adr/` (copy the template; never reverse a decided ADR — supersede it).

## Architecture and decisions

- System architecture (C4 + service choices + cost rationale):
  `docs/architecture.md`.
- Build sequence and per-feature scope: `docs/roadmap.md`.
- Decision records — *why* the system is built this way: `docs/adr/`. Read the
  relevant ADR before changing what it covers (e.g. the vector-store ADR before
  swapping the store).

@docs/architecture.md

## Repo-specific gotchas

- LocalStack requires `LOCALSTACK_AUTH_TOKEN` even for free-tier services since
  the 2026.03 release; without it the container exits and nothing listens on
  4566. Use a free Hobby token locally and a `LOCALSTACK_AUTH_TOKEN` repo secret
  in CI.
- Prefer `127.0.0.1` over `localhost` for the LocalStack endpoint to avoid IPv6
  (`::1`) resolution surprises on WSL/CI.
- Aurora is reached via the **Data API** (HTTP/IAM), so Lambdas need no VPC in
  the early specs. A later spec (008) moves to a VPC posture deliberately; until
  then, do not add VPC config.
- S3 bucket names are globally unique — suffix bucket names with the account ID
  (from `aws_caller_identity`) to avoid `BucketAlreadyExists`.
