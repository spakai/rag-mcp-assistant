"""
Real-AWS integration test for spec 005 — evaluation harness.

Skipped unless RUN_AWS_INTEGRATION=1 is set.

Prerequisites:
    bash scripts/deploy-aws.sh   # infra must be deployed
    bash scripts/seed.sh         # seed documents must be uploaded and ingested
"""
import json
import os
import subprocess
import time

import pytest

RUN_AWS = os.environ.get("RUN_AWS_INTEGRATION") == "1"
SKIP_REASON = "Set RUN_AWS_INTEGRATION=1 and run scripts/seed.sh before this test"

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


@pytest.mark.skipif(not RUN_AWS, reason=SKIP_REASON)
def test_eval_harness_seeds_and_passes():
    """Full evaluation must pass both thresholds against the deployed stack."""
    subprocess.run(
        ["bash", "scripts/seed.sh"],
        cwd=REPO_ROOT,
        check=True,
    )
    # Allow Lambda ingestion + Aurora cold-start to settle.
    time.sleep(35)

    result = subprocess.run(
        ["python", "evals/run_eval.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "BEDROCK_EMBEDDING_MODEL_ID": os.environ.get(
                "BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"
            ),
            "BEDROCK_GENERATION_MODEL_ID": os.environ.get(
                "BEDROCK_GENERATION_MODEL_ID", "amazon.nova-micro-v1:0"
            ),
        },
    )

    assert result.returncode == 0, (
        f"run_eval.py exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    report = json.loads(result.stdout)
    assert report["passed"] is True
    assert report["retrieval_hit_rate"] >= 0.8
    assert report["answer_keyword_score"] >= 0.6
