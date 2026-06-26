"""
Real-AWS integration tests for spec 002 — embeddings + Aurora vector store.

Skipped unless RUN_AWS_INTEGRATION=1 is set.

Prerequisites:
    bash scripts/deploy-aws.sh   # deploys infra and initialises Aurora schema
"""
import json
import os
import time

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
        "chunks_table": outputs["chunks_table"]["value"],
        "aurora_cluster_arn": outputs["aurora_cluster_arn"]["value"],
        "aurora_secret_arn": outputs["aurora_secret_arn"]["value"],
        "aurora_database": outputs["aurora_database"]["value"],
    }


@pytest.fixture(scope="module")
def s3(stack):
    return boto3.client("s3")


@pytest.fixture(scope="module")
def dynamo(stack):
    return boto3.client("dynamodb")


@pytest.fixture(scope="module")
def rdsdata(stack):
    return boto3.client("rds-data")


def _wait_for_status(dynamo_client, table: str, source_key: str, status: str, timeout: int = 120) -> dict | None:
    """Poll DynamoDB until a document row for source_key reaches the expected status."""
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


def _query_aurora(rdsdata_client, cluster_arn: str, secret_arn: str, database: str, sql: str) -> list[dict]:
    resp = rdsdata_client.execute_statement(
        resourceArn=cluster_arn,
        secretArn=secret_arn,
        database=database,
        sql=sql,
        includeResultMetadata=True,
    )
    cols = [c["name"] for c in resp.get("columnMetadata", [])]
    return [
        {col: list(field.values())[0] for col, field in zip(cols, row)}
        for row in resp.get("records", [])
    ]


def test_upload_creates_aurora_rows(stack, s3, dynamo, rdsdata):
    key = "documents/spec002-integration-test.txt"
    body = ("The quick brown fox jumps over the lazy dog. " * 30).encode()

    s3.put_object(Bucket=stack["bucket"], Key=key, Body=body)

    doc = _wait_for_status(dynamo, stack["documents_table"], key, "embedded")
    assert doc is not None, f"Document never reached 'embedded' status within 120 s for key={key}"

    document_id = doc["document_id"]["S"]

    rows = _query_aurora(
        rdsdata,
        stack["aurora_cluster_arn"],
        stack["aurora_secret_arn"],
        stack["aurora_database"],
        f"SELECT document_id, chunk_index, vector_dims(embedding) AS dims "
        f"FROM chunks WHERE source_key = '{key}';",
    )
    assert len(rows) >= 1, "No chunk rows found in Aurora"
    for row in rows:
        assert row["document_id"] == document_id
        assert row["dims"] == 1024, f"Expected 1024 dims, got {row['dims']}"

    doc_rows = _query_aurora(
        rdsdata,
        stack["aurora_cluster_arn"],
        stack["aurora_secret_arn"],
        stack["aurora_database"],
        f"SELECT status FROM documents WHERE source_key = '{key}';",
    )
    assert len(doc_rows) == 1
    assert doc_rows[0]["status"] == "embedded"


def test_reupload_replaces_rows(stack, s3, dynamo, rdsdata):
    key = "documents/spec002-idempotency-test.txt"
    body = ("Idempotency test content. " * 20).encode()

    s3.put_object(Bucket=stack["bucket"], Key=key, Body=body)
    assert _wait_for_status(dynamo, stack["documents_table"], key, "embedded") is not None

    s3.put_object(Bucket=stack["bucket"], Key=key, Body=body)
    assert _wait_for_status(dynamo, stack["documents_table"], key, "embedded") is not None

    doc_rows = _query_aurora(
        rdsdata,
        stack["aurora_cluster_arn"],
        stack["aurora_secret_arn"],
        stack["aurora_database"],
        f"SELECT COUNT(*) AS cnt FROM documents WHERE source_key = '{key}';",
    )
    assert doc_rows[0]["cnt"] == 1, "Expected exactly 1 document row after re-upload (idempotency)"
