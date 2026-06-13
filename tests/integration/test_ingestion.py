"""
Integration tests against a live LocalStack stack.

Prerequisites:
  - docker compose up -d
  - bash scripts/deploy-local.sh

Skip automatically when the Terraform stack is not deployed.
"""

import io
import json
import os
import time

import boto3
import pytest

LOCALSTACK_ENDPOINT = (
    os.environ.get("AWS_ENDPOINT_URL")
    or os.environ.get("LOCALSTACK_ENDPOINT")
    or "http://127.0.0.1:4566"
)
SKIP_REASON = "LocalStack stack not deployed (run docker compose up -d && bash scripts/deploy-local.sh)"


def _stack_available() -> bool:
    try:
        tf_output = os.popen(
            f"cd {os.path.dirname(__file__)}/../../infra && terraform output -json 2>/dev/null"
        ).read()
        data = json.loads(tf_output)
        return bool(data.get("bucket_name"))
    except Exception:
        return False


@pytest.fixture(scope="module")
def stack():
    if not _stack_available():
        pytest.skip(SKIP_REASON)
    tf_output = json.loads(
        os.popen(
            f"cd {os.path.dirname(__file__)}/../../infra && terraform output -json"
        ).read()
    )
    return {
        "bucket": tf_output["bucket_name"]["value"],
        "documents_table": tf_output["documents_table"]["value"],
        "chunks_table": tf_output["chunks_table"]["value"],
    }


@pytest.fixture(scope="module")
def aws_clients():
    kwargs = {
        "endpoint_url": LOCALSTACK_ENDPOINT,
        "region_name": "us-east-1",
        "aws_access_key_id": "test",
        "aws_secret_access_key": "test",
    }
    return {
        "s3": boto3.client("s3", **kwargs),
        "dynamo": boto3.client("dynamodb", **kwargs),
    }


def _upload(clients, stack, key: str, body: bytes):
    """Upload to S3; the S3 event notification triggers the Lambda."""
    clients["s3"].put_object(Bucket=stack["bucket"], Key=key, Body=body)


def _wait_for_document(dynamo, table: str, source_key: str, timeout: int = 30) -> list[dict]:
    """Poll until at least one document record for source_key appears."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        items = _scan_all(dynamo, table)
        matches = [i for i in items if i["source_key"]["S"] == source_key]
        if matches:
            return matches
        time.sleep(1)
    return []


def _scan_all(dynamo, table: str) -> list[dict]:
    items = []
    resp = dynamo.scan(TableName=table)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = dynamo.scan(TableName=table, ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


def test_txt_upload_creates_document_and_chunks(stack, aws_clients):
    text = "word " * 300  # ~1500 chars -> 2 chunks with default size/overlap
    key = "documents/integration-test.txt"

    _upload(aws_clients, stack, key, text.encode("utf-8"))
    docs = _wait_for_document(aws_clients["dynamo"], stack["documents_table"], key)

    assert len(docs) == 1
    doc = docs[0]
    assert doc["status"]["S"] == "ingested"
    chunk_count = int(doc["chunk_count"]["N"])
    assert chunk_count >= 1

    doc_id = doc["document_id"]["S"]
    chunks = aws_clients["dynamo"].query(
        TableName=stack["chunks_table"],
        KeyConditionExpression="document_id = :d",
        ExpressionAttributeValues={":d": {"S": doc_id}},
    )["Items"]
    assert len(chunks) == chunk_count
    for chunk in chunks:
        assert "text" in chunk
        assert "char_count" in chunk
        assert "source_key" in chunk
        assert "created_at" in chunk


def test_idempotency_on_reupload(stack, aws_clients):
    text = "reupload test " * 100
    key = "documents/integration-idempotency.txt"

    _upload(aws_clients, stack, key, text.encode("utf-8"))
    first_docs = _wait_for_document(aws_clients["dynamo"], stack["documents_table"], key)
    assert len(first_docs) == 1
    first_doc_id = first_docs[0]["document_id"]["S"]

    _upload(aws_clients, stack, key, text.encode("utf-8"))
    # Wait for a new document_id to appear (idempotency replaces the record)
    deadline = time.time() + 30
    second_docs = first_docs
    while time.time() < deadline:
        candidates = [
            i for i in _scan_all(aws_clients["dynamo"], stack["documents_table"])
            if i["source_key"]["S"] == key
        ]
        if len(candidates) == 1 and candidates[0]["document_id"]["S"] != first_doc_id:
            second_docs = candidates
            break
        time.sleep(1)

    assert len(second_docs) == 1
    assert second_docs[0]["document_id"]["S"] != first_doc_id
    assert second_docs[0]["chunk_count"]["N"] == first_docs[0]["chunk_count"]["N"]


def test_pdf_upload_creates_records(stack, aws_clients):
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    key = "documents/integration-test.pdf"

    _upload(aws_clients, stack, key, buf.getvalue())
    docs = _wait_for_document(aws_clients["dynamo"], stack["documents_table"], key)

    assert len(docs) == 1
