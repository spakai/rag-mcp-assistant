#!/usr/bin/env python3
"""Evaluation harness CLI for the RAG knowledge assistant.

Reads the golden set, calls retrieve_chunks() and retrieve_and_answer() for
each question, scores retrieval hit rate and answer keyword presence, prints a
JSON report, and exits non-zero if either score is below its threshold.

Required env vars: BEDROCK_EMBEDDING_MODEL_ID, BEDROCK_GENERATION_MODEL_ID
Optional env vars: EVAL_RETRIEVAL_THRESHOLD (default 0.8),
                   EVAL_ANSWER_THRESHOLD (default 0.6),
                   AURORA_DATABASE (default "postgres")
"""
import json
import os
import subprocess
import sys

import boto3

from evals.scorer import score_answer, score_retrieval
from src.query.retrieval import retrieve_and_answer, retrieve_chunks

_HERE = os.path.dirname(__file__)
GOLDEN_SET_PATH = os.path.join(_HERE, "golden_set.json")
INFRA_DIR = os.path.join(_HERE, "..", "infra")

RETRIEVAL_THRESHOLD = float(os.environ.get("EVAL_RETRIEVAL_THRESHOLD", "0.8"))
ANSWER_THRESHOLD = float(os.environ.get("EVAL_ANSWER_THRESHOLD", "0.6"))


def _tf_outputs() -> dict:
    result = subprocess.run(
        ["terraform", "output", "-json"],
        cwd=INFRA_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def main() -> None:
    with open(GOLDEN_SET_PATH) as f:
        golden_set = json.load(f)

    outputs = _tf_outputs()
    cluster_arn = outputs["aurora_cluster_arn"]["value"]
    secret_arn = outputs["aurora_secret_arn"]["value"]
    database = outputs.get("aurora_database", {}).get("value", "postgres")
    embedding_model_id = os.environ["BEDROCK_EMBEDDING_MODEL_ID"]
    generation_model_id = os.environ["BEDROCK_GENERATION_MODEL_ID"]

    rdsdata = boto3.client("rds-data")
    bedrock = boto3.client("bedrock-runtime")

    retrieved_results: list[dict] = []
    generated_answers: list[dict] = []

    for item in golden_set:
        print(f"  evaluating {item['id']}: {item['question'][:60]}...", file=sys.stderr)

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
        "thresholds": {
            "retrieval": RETRIEVAL_THRESHOLD,
            "answer": ANSWER_THRESHOLD,
        },
        "passed": passed,
        "details": {
            "retrieved": retrieved_results,
            "answers": [
                {"id": a["id"], "answer": a["answer"][:120]} for a in generated_answers
            ],
        },
    }
    print(json.dumps(report, indent=2))

    if not passed:
        print("\nEVALUATION FAILED", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
