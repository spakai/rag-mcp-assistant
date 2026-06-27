# Spec 005 — Evaluation harness

- **Status:** Proposed
- **Tracking issue:** TBD
- **Author:** human (principal)

## Context

Specs 001–004 built the full RAG pipeline: ingestion, embeddings, query API, and
MCP server. Without a way to measure quality, changes to chunking, embedding
models, prompts, or retrieval parameters are blind — it is impossible to tell
whether a change helps or hurts.

This spec introduces a labeled evaluation harness: a small golden question/answer
set that encodes the expected retrieval and answer behaviour for a fixed set of
seed documents, plus scoring functions that quantify retrieval relevance and
answer quality. The harness runs as a CI gate so regressions are caught
automatically before merge.

## User story

As a developer making changes to the RAG pipeline, I want an automated quality
gate that scores retrieval relevance and answer quality against a labeled dataset,
so that I know whether my change improved or degraded the system.

## Acceptance criteria

Each criterion must be verifiable by an automated test.

- [ ] A seed script (`scripts/seed.sh`) uploads at least two sample documents to
      the `documents/` S3 prefix. The source keys are stable across runs so the
      golden set can reference them.
- [ ] A labeled evaluation set (`evals/golden_set.json`) contains at least five
      entries. Each entry has:
      - `id` (string, unique) — stable identifier for the question.
      - `question` (string) — the natural-language query to evaluate.
      - `expected_source_keys` (list of ≥1 S3 key) — at least one must appear
        in the top-k retrieved chunks for a hit to be counted.
      - `expected_answer_keywords` (list of strings) — keywords that must
        appear in a correct answer; presence is checked case-insensitively.
- [ ] `evals/scorer.py` exposes two pure functions (no I/O, no AWS):
      - `score_retrieval(golden_set, retrieved_results) -> float`: the fraction
        of questions (hit rate) where at least one `expected_source_keys` entry
        is present in the returned chunks.
      - `score_answer(golden_set, generated_answers) -> float`: the average
        fraction of `expected_answer_keywords` found (case-insensitive) in the
        generated answer across all questions.
- [ ] `evals/run_eval.py` is a CLI entry point that:
      - reads Terraform outputs for Aurora ARNs and the S3 bucket name;
      - calls `retrieve_chunks()` and `retrieve_and_answer()` from
        `src/query/retrieval.py` for each golden set entry;
      - computes both scores and prints a JSON report to stdout;
      - exits with code 1 if either score is below its threshold.
- [ ] Retrieval and answer thresholds are configurable via
      `EVAL_RETRIEVAL_THRESHOLD` (default `0.8`) and `EVAL_ANSWER_THRESHOLD`
      (default `0.6`) environment variables; a regression below either threshold
      produces a non-zero exit code.
- [ ] Unit tests (`tests/test_eval_scorer.py`) cover `score_retrieval` and
      `score_answer` with synthetic golden sets and results — no network, no AWS.
- [ ] An integration test (`tests/integration/test_eval_harness.py`, gated by
      `RUN_AWS_INTEGRATION=1`) seeds the evaluation documents, waits for
      ingestion, invokes `run_eval.py` in a subprocess, and asserts exit code 0
      and `passed == true` in the JSON report.
- [ ] The `integration-aws` CI job runs `scripts/seed.sh` and then
      `python evals/run_eval.py` after the existing integration tests; a non-zero
      exit fails CI.
- [ ] `evals/run_eval.py` calls `src.query.retrieval` exclusively — no retrieval
      or generation logic is duplicated in `evals/`.
- [ ] `ruff check .` is clean and CI is green on the pull request.

## Out of scope

- LLM-as-judge scoring (richer but more expensive — a future spec could replace
  the keyword scorer with a Bedrock-based judge).
- Automated golden set expansion from user feedback.
- Per-document precision/recall at k; only hit rate is computed here.
- Authentication on the eval path (spec 006).

## Constraints

- Follow all guardrails in `AGENTS.md`. In particular: no secrets committed,
  `min_capacity = 0` on Aurora must not be changed, no OpenSearch Serverless.
- `evals/run_eval.py` must call `src.query.retrieval` exclusively — no retrieval
  or generation logic lives in `evals/`.
- Seed documents must be synthetic or public-domain text (no real user data
  committed to the repository).
- The golden set must be deterministic: expected values must be stable regardless
  of run order or concurrency.
- The CI eval job runs only on `workflow_dispatch` (same gate as the existing
  `integration-aws` job) to avoid Bedrock/Aurora costs on every push.
