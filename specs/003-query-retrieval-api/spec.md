# Spec 003 — Query / retrieval API

- **Status:** Done
- **Tracking issue:** #7
- **Author:** human (principal)

## Context

Spec 002 stored embedded chunks in Aurora pgvector. This spec adds the read path:
an HTTP endpoint that takes a question, embeds it, runs a top-k vector similarity
search against the stored chunks, and passes the retrieved context to Bedrock to
generate a grounded answer with citations.

The retrieval and generation logic lives in a single **retrieval core** module
(`src/query/retrieval.py`). A thin API Gateway + Lambda wrapper exposes it over
HTTP. Spec 004 (MCP server) will reuse the same core — the two transports must
never diverge.

Integration tests require real AWS (Bedrock + Aurora Data API unavailable on
LocalStack free tier), gated behind `RUN_AWS_INTEGRATION=1`.

## User story

As a knowledge worker, I want to ask a natural-language question via an HTTP
endpoint and receive a grounded answer with citations, so that I can find
information from the documents I have uploaded.

## Acceptance criteria

Each criterion must be verifiable by an automated test.

- [x] `POST /ask` with `{"question": "..."}` returns HTTP 200 with a JSON body
      containing `answer` (string) and `sources` (list of `{source_key, chunk_index,
      text}` objects).
- [x] The question is embedded using the same Bedrock model as ingestion
      (`BEDROCK_EMBEDDING_MODEL_ID`); the model ID is read from an environment
      variable, not hardcoded.
- [x] A top-k vector similarity search (default k=5, configurable via
      `RETRIEVAL_TOP_K` env var) is run against the Aurora `chunks` table using
      pgvector's cosine distance operator (`<=>`) and returns the closest chunks.
- [x] The retrieved chunks are passed to a Bedrock text-generation model
      (`BEDROCK_GENERATION_MODEL_ID` env var) as context; the prompt instructs
      the model to answer only from provided context and to cite its sources.
- [x] If no relevant chunks are found (empty result from vector search), the API
      returns a `"no relevant documents found"` answer rather than hallucinating.
- [x] The retrieval core (`src/query/retrieval.py`) is transport-agnostic: it
      accepts a question string and returns a dict with `answer` and `sources`; it
      has no dependency on API Gateway event shapes.
- [x] Unit tests cover the retrieval core with mocked Bedrock and Aurora clients,
      including the no-results path.
- [x] An integration test (gated by `RUN_AWS_INTEGRATION=1`) seeds a known document,
      calls `POST /ask` with a question whose answer is in that document, and asserts
      the response contains the correct answer and at least one matching source.
- [x] `ruff check .` is clean and CI is green on the pull request.

## Out of scope

- MCP server (spec 004).
- Authentication / API keys (spec 006).
- Streaming responses.
- Re-ranking or hybrid search.
- Moving Aurora into a VPC (spec 008).

## Lessons learned

- **Claude 3 Haiku is LEGACY in this account.** `anthropic.claude-3-haiku-20240307-v1:0`
  returned `ResourceNotFoundException` ("Legacy model, not used in 30 days") and a
  second variant required an Anthropic use case form. Use Amazon Nova models instead
  — they are always ACTIVE and require no form submission.
- **Use the Bedrock Converse API for generation, not `invoke_model`.** `converse()`
  is model-agnostic: the same call works for Nova, Claude, Titan Text, etc. without
  per-model JSON format handling. `invoke_model` requires knowing whether the target
  is Claude (Messages API format) or Nova (native format) — the Converse API abstracts
  that away entirely.
- **`amazon.nova-micro-v1:0` is a good default generation model for this project.**
  It is the cheapest active Nova model, has no use case form, and works well for
  RAG-style grounded Q&A over short context windows.

## Constraints

- Follow all guardrails in `AGENTS.md`. In particular: no secrets committed,
  `min_capacity = 0` on Aurora must not be changed.
- The retrieval core must be importable and callable independently of any Lambda
  handler or transport layer — spec 004 depends on this.
- Bedrock model IDs are configuration, not constants. Handle throttling with
  exponential backoff (same pattern as `embedder.py`).
- All infrastructure (API Gateway, new Lambda) goes through Terraform in `infra/`.
- The `documents/` S3 event filter must not be touched.
- Aurora is accessed exclusively via the Data API — do not add VPC configuration.
