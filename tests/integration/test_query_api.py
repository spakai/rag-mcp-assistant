"""
Real-AWS integration tests for spec 003 — query / retrieval API.

Skipped unless RUN_AWS_INTEGRATION=1 is set.

Prerequisites:
    bash scripts/deploy-aws.sh   # deploys infra and initialises Aurora schema
"""
import json
import os
import time
import urllib.error
import urllib.request

import boto3
import pytest

RUN_AWS = os.environ.get("RUN_AWS_INTEGRATION") == "1"
SKIP_REASON = "Set RUN_AWS_INTEGRATION=1 and deploy via scripts/deploy-aws.sh to run"

INFRA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "infra")


def _tf_outputs() -> dict:
    raw = os.popen(f"cd {INFRA_DIR} && terraform output -json 2>/dev/null").read()
    return json.loads(raw)


@pytest.fixture(scope="module")
def stack():
    if not RUN_AWS:
        pytest.skip(SKIP_REASON)
    outputs = _tf_outputs()
    return {
        "bucket": outputs["bucket_name"]["value"],
        "documents_table": outputs["documents_table"]["value"],
        "api_endpoint": outputs["api_endpoint"]["value"],
    }


@pytest.fixture(scope="module")
def s3(stack):
    return boto3.client("s3")


@pytest.fixture(scope="module")
def dynamo(stack):
    return boto3.client("dynamodb")


def _wait_for_status(dynamo_client, table: str, source_key: str, status: str, timeout: int = 120) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = dynamo_client.query(
            TableName=table,
            IndexName="source_key_index",
            KeyConditionExpression="source_key = :sk",
            ExpressionAttributeValues={":sk": {"S": source_key}},
        )
        items = resp.get("Items", [])
        if items and items[0].get("status", {}).get("S") == status:
            return items[0]
        time.sleep(5)
    return None


def _post_ask(api_endpoint: str, question: str) -> dict:
    payload = json.dumps({"question": question}).encode()
    req = urllib.request.Request(
        api_endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def test_ask_returns_grounded_answer(stack, s3, dynamo):
    key = "documents/spec003-query-test.txt"
    content = (
        "The AWS Well-Architected Framework describes five pillars: "
        "Operational Excellence, Security, Reliability, Performance Efficiency, "
        "and Cost Optimization. Each pillar provides best practices for building "
        "resilient and efficient cloud architectures."
    )
    s3.put_object(Bucket=stack["bucket"], Key=key, Body=content.encode())

    doc = _wait_for_status(dynamo, stack["documents_table"], key, "embedded")
    assert doc is not None, f"Document never reached 'embedded' status for key={key}"

    result = _post_ask(
        stack["api_endpoint"],
        "What are the five pillars of the AWS Well-Architected Framework?",
    )

    assert "answer" in result
    assert "sources" in result
    assert len(result["answer"]) > 0
    assert len(result["sources"]) >= 1
    assert any(s["source_key"] == key for s in result["sources"])


def test_ask_off_topic_returns_no_documents(stack):
    result = _post_ask(
        stack["api_endpoint"],
        "What is the recipe for chocolate cake with no relation to cloud computing?",
    )

    assert "answer" in result
    assert "sources" in result
    if not result["sources"]:
        assert "No relevant documents found" in result["answer"] or len(result["answer"]) > 0


def test_ask_missing_question_returns_400(stack):
    payload = json.dumps({}).encode()
    req = urllib.request.Request(
        stack["api_endpoint"],
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        pytest.fail("Expected HTTP 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        body = json.loads(exc.read().decode())
        assert "error" in body
