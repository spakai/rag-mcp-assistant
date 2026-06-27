# Plan — Spec 005: Evaluation harness

## Approach

Introduce a small evaluation module at `evals/` that layers over the existing
retrieval core without duplicating it. Three concerns stay separate:

1. **Seed data** — synthetic text files committed at `evals/seed_docs/`; uploaded
   by `scripts/seed.sh` before any evaluation run.
2. **Scoring** — pure functions in `evals/scorer.py`; deterministic, no I/O,
   fully unit-testable.
3. **Runner** — `evals/run_eval.py`; reads Terraform outputs + env vars, drives
   `src.query.retrieval`, aggregates scores, prints JSON, exits non-zero on
   regression.

The golden set (`evals/golden_set.json`) references the stable S3 keys that
`seed.sh` produces (e.g. `documents/aws-well-architected.txt`) so the labels
remain valid across deploys.

---

## Files to create

### `evals/__init__.py`
Empty package marker.

### `evals/seed_docs/aws-well-architected.txt`
Synthetic ~500-word summary of the AWS Well-Architected Framework five pillars
in natural prose (not copied from AWS documentation). Content is stable so
Bedrock produces consistent embeddings across runs. Covers Operational
Excellence, Security, Reliability, Performance Efficiency, and Cost Optimization
including example design principles for each.

### `evals/seed_docs/aws-s3-overview.txt`
Synthetic ~300-word overview of Amazon S3 covering: buckets and objects,
storage classes (Standard, Standard-IA, One Zone-IA, Glacier), versioning and
delete markers, and S3's eleven-nines durability guarantee.

### `evals/golden_set.json`
Five labeled entries spanning both seed documents:

```json
[
  {
    "id": "q001",
    "question": "What are the five pillars of the AWS Well-Architected Framework?",
    "expected_source_keys": ["documents/aws-well-architected.txt"],
    "expected_answer_keywords": [
      "Operational Excellence", "Security", "Reliability",
      "Performance Efficiency", "Cost Optimization"
    ]
  },
  {
    "id": "q002",
    "question": "What design principles does the Security pillar recommend?",
    "expected_source_keys": ["documents/aws-well-architected.txt"],
    "expected_answer_keywords": ["least privilege", "encryption", "traceability"]
  },
  {
    "id": "q003",
    "question": "What S3 storage classes are available for infrequently accessed data?",
    "expected_source_keys": ["documents/aws-s3-overview.txt"],
    "expected_answer_keywords": ["Standard-IA", "One Zone-IA"]
  },
  {
    "id": "q004",
    "question": "How does S3 versioning protect against accidental deletions?",
    "expected_source_keys": ["documents/aws-s3-overview.txt"],
    "expected_answer_keywords": ["version", "delete marker"]
  },
  {
    "id": "q005",
    "question": "What durability does Amazon S3 provide for stored objects?",
    "expected_source_keys": ["documents/aws-s3-overview.txt"],
    "expected_answer_keywords": ["99.999999999", "eleven nines", "durability"]
  }
]
```

Keywords are chosen to be unambiguous proper nouns or technical phrases the LLM
cannot paraphrase away from — this makes the score stable across minor prompt
or model changes.

### `evals/scorer.py`
Two pure functions:

```python
def score_retrieval(golden_set: list[dict], retrieved_results: list[dict]) -> float:
    """
    Hit rate: fraction of questions where at least one expected_source_key
    appears in the returned chunks.
    """
    if not golden_set:
        return 0.0
    results_by_id = {r["id"]: r["returned_source_keys"] for r in retrieved_results}
    hits = sum(
        1 for item in golden_set
        if any(k in results_by_id.get(item["id"], []) for k in item["expected_source_keys"])
    )
    return hits / len(golden_set)


def score_answer(golden_set: list[dict], generated_answers: list[dict]) -> float:
    """
    Average keyword-presence fraction across all questions (case-insensitive).
    An entry with no keywords counts as 1.0 (nothing to fail).
    """
    if not golden_set:
        return 0.0
    answers_by_id = {a["id"]: a["answer"].lower() for a in generated_answers}
    total = 0.0
    for item in golden_set:
        answer = answers_by_id.get(item["id"], "")
        keywords = item.get("expected_answer_keywords", [])
        if not keywords:
            total += 1.0
            continue
        total += sum(1 for kw in keywords if kw.lower() in answer) / len(keywords)
    return total / len(golden_set)
```

No imports beyond the standard library; no side effects.

### `evals/run_eval.py`
CLI runner that ties scoring to the deployed system:

```python
#!/usr/bin/env python3
import json
import os
import sys

import boto3

from evals.scorer import score_answer, score_retrieval
from src.query.retrieval import retrieve_and_answer, retrieve_chunks

GOLDEN_SET = os.path.join(os.path.dirname(__file__), "golden_set.json")
RETRIEVAL_THRESHOLD = float(os.environ.get("EVAL_RETRIEVAL_THRESHOLD", "0.8"))
ANSWER_THRESHOLD = float(os.environ.get("EVAL_ANSWER_THRESHOLD", "0.6"))
INFRA_DIR = os.path.join(os.path.dirname(__file__), "..", "infra")


def _tf_outputs() -> dict:
    raw = os.popen(f"cd {INFRA_DIR} && terraform output -json 2>/dev/null").read()
    return json.loads(raw)


def main() -> None:
    with open(GOLDEN_SET) as f:
        golden_set = json.load(f)

    outputs = _tf_outputs()
    cluster_arn = outputs["aurora_cluster_arn"]["value"]
    secret_arn = outputs["aurora_secret_arn"]["value"]
    database = os.environ.get("AURORA_DATABASE", "postgres")
    embedding_model_id = os.environ["BEDROCK_EMBEDDING_MODEL_ID"]
    generation_model_id = os.environ["BEDROCK_GENERATION_MODEL_ID"]

    rdsdata = boto3.client("rds-data")
    bedrock = boto3.client("bedrock-runtime")

    retrieved_results: list[dict] = []
    generated_answers: list[dict] = []

    for item in golden_set:
        chunks = retrieve_chunks(
            rdsdata, bedrock, cluster_arn, secret_arn, database,
            item["question"], embedding_model_id,
        )
        retrieved_results.append({
            "id": item["id"],
            "returned_source_keys": [c["source_key"] for c in chunks],
        })
        result = retrieve_and_answer(
            rdsdata, bedrock, cluster_arn, secret_arn, database,
            item["question"], embedding_model_id, generation_model_id,
        )
        generated_answers.append({"id": item["id"], "answer": result["answer"]})

    retrieval_score = score_retrieval(golden_set, retrieved_results)
    answer_score = score_answer(golden_set, generated_answers)
    passed = retrieval_score >= RETRIEVAL_THRESHOLD and answer_score >= ANSWER_THRESHOLD

    report = {
        "retrieval_hit_rate": retrieval_score,
        "answer_keyword_score": answer_score,
        "thresholds": {"retrieval": RETRIEVAL_THRESHOLD, "answer": ANSWER_THRESHOLD},
        "passed": passed,
    }
    print(json.dumps(report, indent=2))

    if not passed:
        print("EVALUATION FAILED", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

### `tests/test_eval_scorer.py`
Unit tests for both scoring functions covering:

- `score_retrieval` — all hits (1.0), no hits (0.0), partial hits (0.5),
  multiple `expected_source_keys` where only the second matches (OR logic),
  missing question id in results (treated as empty).
- `score_answer` — all keywords present (1.0), no keywords in entry (1.0),
  partial match (fractional), case-insensitive match, answer missing from
  results (empty string).
- Edge: `golden_set=[]` returns 0.0 for both functions (no ZeroDivisionError).

### `tests/integration/test_eval_harness.py`
Gated by `RUN_AWS_INTEGRATION=1`. Seeds the two documents via `scripts/seed.sh`,
waits 30 s for ingestion, then calls `run_eval.py` via `subprocess.run` and
asserts exit code 0 and `report["passed"] is True`.

### `scripts/seed.sh`
```bash
#!/usr/bin/env bash
set -euo pipefail
BUCKET=$(cd infra && terraform output -raw bucket_name)
aws s3 cp evals/seed_docs/aws-well-architected.txt \
    "s3://$BUCKET/documents/aws-well-architected.txt"
aws s3 cp evals/seed_docs/aws-s3-overview.txt \
    "s3://$BUCKET/documents/aws-s3-overview.txt"
echo "Seeded 2 documents to s3://$BUCKET/documents/"
```

---

## Files to modify

### `.github/workflows/ci.yml`
In the `integration-aws` job, after the `Run real-AWS integration tests` step,
add three steps:

```yaml
- name: Seed evaluation documents
  run: bash scripts/seed.sh

- name: Wait for ingestion
  run: sleep 30

- name: Run evaluation harness
  env:
    BEDROCK_EMBEDDING_MODEL_ID: amazon.titan-embed-text-v2:0
    BEDROCK_GENERATION_MODEL_ID: amazon.nova-micro-v1:0
  run: python evals/run_eval.py
```

No new CI job needed; the eval runs inside the existing `integration-aws` job
which is already gated to `workflow_dispatch`.

---

## Risks

| Risk | Mitigation |
|---|---|
| Aurora cold-start delays ingestion after seed | `sleep 30` in CI; increase if aurora still spinning up on first run |
| Embedding drift changes retrieval ranking | Keywords are unambiguous technical terms; minor ranking shifts won't drop hit rate below 0.8 |
| LLM paraphrases keywords (e.g. "11 nines" instead of "eleven nines") | Golden set uses `99.999999999` as an alternative keyword alongside `eleven nines` so either form scores a hit |
| `run_eval.py` imports break if `AURORA_DATABASE` is missing | Defaults to `"postgres"` (same default used across spec 002–004) |
| Seed script fails when bucket_name output doesn't exist (LocalStack path) | `seed.sh` is only called from the real-AWS CI job; LocalStack path remains unchanged |

---

## Order of implementation

1. `evals/seed_docs/aws-well-architected.txt` and `aws-s3-overview.txt`
2. `evals/__init__.py`, `evals/golden_set.json`
3. `evals/scorer.py`
4. `tests/test_eval_scorer.py` — green with `pytest tests/test_eval_scorer.py -q`
   before touching any AWS code
5. `evals/run_eval.py`
6. `scripts/seed.sh`
7. `tests/integration/test_eval_harness.py`
8. `.github/workflows/ci.yml` — add eval steps
9. Full `pytest tests/ -q` + `ruff check .` — green
10. PR
