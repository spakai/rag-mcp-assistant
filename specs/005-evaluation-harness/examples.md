# Spec 005 — Example Payloads and Outputs

Concrete examples of the evaluation harness inputs, outputs, and CLI behavior.
All values are illustrative; scores will vary based on the actual deployed model
and ingested content.

---

## 1. Golden set entry

`evals/golden_set.json` (single entry shown):

```json
{
  "id": "q001",
  "question": "What are the five pillars of the AWS Well-Architected Framework?",
  "expected_source_keys": ["documents/aws-well-architected.txt"],
  "expected_answer_keywords": [
    "Operational Excellence",
    "Security",
    "Reliability",
    "Performance Efficiency",
    "Cost Optimization"
  ]
}
```

**How it scores:**

- *Retrieval hit*: `retrieve_chunks()` must return at least one chunk whose
  `source_key` is `"documents/aws-well-architected.txt"`. If it does, this
  question counts as a hit toward the hit rate.
- *Answer keyword score*: `retrieve_and_answer()` must produce an answer string
  that contains each of the five keywords (case-insensitive). The fraction present
  is averaged across all five golden entries to produce the final score.

---

## 2. Evaluation report — passing

```
$ BEDROCK_EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0 \
  BEDROCK_GENERATION_MODEL_ID=amazon.nova-micro-v1:0 \
  python evals/run_eval.py
```

stdout:
```json
{
  "retrieval_hit_rate": 1.0,
  "answer_keyword_score": 0.88,
  "thresholds": {
    "retrieval": 0.8,
    "answer": 0.6
  },
  "passed": true
}
```

Exit code: `0`. CI passes.

---

## 3. Evaluation report — failing (retrieval regression)

A bad chunking change drops the hit rate below threshold:

stdout:
```json
{
  "retrieval_hit_rate": 0.6,
  "answer_keyword_score": 0.52,
  "thresholds": {
    "retrieval": 0.8,
    "answer": 0.6
  },
  "passed": false
}
```

stderr:
```
EVALUATION FAILED
```

Exit code: `1`. The CI step fails with a non-zero exit and GitHub marks the job
as failed.

---

## 4. Tighter threshold for pre-release validation

```
$ EVAL_RETRIEVAL_THRESHOLD=0.9 EVAL_ANSWER_THRESHOLD=0.75 python evals/run_eval.py
```

Uses the same JSON report format; thresholds are reflected in the output:

```json
{
  "retrieval_hit_rate": 0.8,
  "answer_keyword_score": 0.72,
  "thresholds": {
    "retrieval": 0.9,
    "answer": 0.75
  },
  "passed": false
}
```

---

## 5. Scorer unit test examples

`tests/test_eval_scorer.py`:

```python
from evals.scorer import score_retrieval, score_answer

GOLDEN = [
    {
        "id": "q001",
        "expected_source_keys": ["documents/doc-a.txt"],
        "expected_answer_keywords": ["alpha", "beta", "gamma"],
    },
    {
        "id": "q002",
        "expected_source_keys": ["documents/doc-b.txt", "documents/doc-c.txt"],
        "expected_answer_keywords": ["delta"],
    },
]


def test_score_retrieval_all_hits():
    retrieved = [
        {"id": "q001", "returned_source_keys": ["documents/doc-a.txt"]},
        {"id": "q002", "returned_source_keys": ["documents/doc-c.txt"]},  # second key counts
    ]
    assert score_retrieval(GOLDEN, retrieved) == 1.0


def test_score_retrieval_partial():
    retrieved = [
        {"id": "q001", "returned_source_keys": ["documents/doc-a.txt"]},
        {"id": "q002", "returned_source_keys": ["documents/wrong.txt"]},
    ]
    assert score_retrieval(GOLDEN, retrieved) == 0.5


def test_score_retrieval_empty_golden():
    assert score_retrieval([], []) == 0.0


def test_score_answer_all_keywords():
    answers = [
        {"id": "q001", "answer": "The values are alpha, beta, and gamma."},
        {"id": "q002", "answer": "Delta is the key concept."},
    ]
    assert score_answer(GOLDEN, answers) == 1.0


def test_score_answer_case_insensitive():
    answers = [
        {"id": "q001", "answer": "ALPHA and BETA are present, but not the third."},
        {"id": "q002", "answer": "DELTA appears here."},
    ]
    # q001: 2/3 keywords; q002: 1/1 → (0.6667 + 1.0) / 2 ≈ 0.833
    result = score_answer(GOLDEN, answers)
    assert abs(result - 0.833) < 0.01


def test_score_answer_empty_keywords_counts_as_pass():
    golden = [{"id": "q001", "expected_source_keys": [], "expected_answer_keywords": []}]
    answers = [{"id": "q001", "answer": "anything"}]
    assert score_answer(golden, answers) == 1.0
```

---

## 6. Seed script output

```
$ bash scripts/seed.sh
upload: evals/seed_docs/aws-well-architected.txt to s3://rag-docs-123456789012/documents/aws-well-architected.txt
upload: evals/seed_docs/aws-s3-overview.txt to s3://rag-docs-123456789012/documents/aws-s3-overview.txt
Seeded 2 documents to s3://rag-docs-123456789012/documents/
```

---

## 7. CI log — integration-aws job (passing)

```
Run bash scripts/seed.sh
upload: evals/seed_docs/aws-well-architected.txt to s3://rag-docs-123456789012/documents/aws-well-architected.txt
upload: evals/seed_docs/aws-s3-overview.txt to s3://rag-docs-123456789012/documents/aws-s3-overview.txt
Seeded 2 documents to s3://rag-docs-123456789012/documents/

Run sleep 30
(30 s pause for Lambda ingestion)

Run python evals/run_eval.py
{
  "retrieval_hit_rate": 1.0,
  "answer_keyword_score": 0.88,
  "thresholds": { "retrieval": 0.8, "answer": 0.6 },
  "passed": true
}
```

---

## 8. Integration test structure

`tests/integration/test_eval_harness.py` (illustrative):

```python
import json
import os
import subprocess
import time

import pytest

RUN_AWS = os.environ.get("RUN_AWS_INTEGRATION") == "1"
SKIP_REASON = "Set RUN_AWS_INTEGRATION=1 and deploy via scripts/deploy-aws.sh to run"


@pytest.mark.skipif(not RUN_AWS, reason=SKIP_REASON)
def test_eval_harness_passes():
    """Full evaluation must pass both thresholds against the deployed stack."""
    subprocess.run(["bash", "scripts/seed.sh"], check=True)
    time.sleep(30)   # allow Lambda ingestion to complete

    result = subprocess.run(
        ["python", "evals/run_eval.py"],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "BEDROCK_EMBEDDING_MODEL_ID": "amazon.titan-embed-text-v2:0",
            "BEDROCK_GENERATION_MODEL_ID": "amazon.nova-micro-v1:0",
        },
    )
    assert result.returncode == 0, f"eval failed:\n{result.stdout}\n{result.stderr}"
    report = json.loads(result.stdout)
    assert report["passed"] is True
    assert report["retrieval_hit_rate"] >= 0.8
    assert report["answer_keyword_score"] >= 0.6
```

---

## 9. Seed document excerpt

`evals/seed_docs/aws-well-architected.txt` (excerpt — full file is ~500 words):

```
The AWS Well-Architected Framework provides a consistent approach for customers
and partners to evaluate architectures and implement designs that scale over time.
It is organized around five pillars:

1. Operational Excellence — focuses on running and monitoring systems to deliver
   business value, including automating changes, responding to events, and
   refining procedures.

2. Security — includes the ability to protect information, systems, and assets.
   It applies the principles of least privilege, enables traceability across all
   layers, and uses encryption in transit and at rest.

3. Reliability — covers the ability of a workload to perform its intended function
   correctly and consistently. It emphasizes designing for failure and testing
   recovery procedures.

4. Performance Efficiency — focuses on the efficient use of computing resources.
   It involves selecting the right resource types and sizes based on workload
   requirements and monitoring to maintain efficiency.

5. Cost Optimization — focuses on avoiding unnecessary costs. Key principles
   include measuring efficiency, eliminating unneeded expense, and considering
   managed services to reduce ownership cost.
```
