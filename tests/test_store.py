import boto3
import pytest
from moto import mock_aws

from src.ingestion.store import replace_document, update_document_status

DOCUMENTS_TABLE = "documents"
CHUNKS_TABLE = "chunks"


def _create_tables(dynamo):
    dynamo.create_table(
        TableName=DOCUMENTS_TABLE,
        KeySchema=[{"AttributeName": "document_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "document_id", "AttributeType": "S"},
            {"AttributeName": "source_key", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "source_key_index",
                "KeySchema": [{"AttributeName": "source_key", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    dynamo.create_table(
        TableName=CHUNKS_TABLE,
        KeySchema=[
            {"AttributeName": "document_id", "KeyType": "HASH"},
            {"AttributeName": "chunk_index", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "document_id", "AttributeType": "S"},
            {"AttributeName": "chunk_index", "AttributeType": "N"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture()
def dynamo():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create_tables(client)
        yield client


def _make_chunks(n: int) -> list[dict]:
    return [{"chunk_index": i, "text": f"chunk {i}", "char_count": 7} for i in range(n)]


def test_stores_document_record(dynamo):
    chunks = _make_chunks(3)
    replace_document(dynamo, DOCUMENTS_TABLE, CHUNKS_TABLE, "documents/a.txt", chunks)

    resp = dynamo.scan(TableName=DOCUMENTS_TABLE)
    assert len(resp["Items"]) == 1
    item = resp["Items"][0]
    assert item["source_key"]["S"] == "documents/a.txt"
    assert item["chunk_count"]["N"] == "3"
    assert item["status"]["S"] == "ingested"


def test_stores_chunk_records(dynamo):
    chunks = _make_chunks(3)
    replace_document(dynamo, DOCUMENTS_TABLE, CHUNKS_TABLE, "documents/a.txt", chunks)

    resp = dynamo.scan(TableName=CHUNKS_TABLE)
    assert len(resp["Items"]) == 3


def test_idempotency_replaces_on_reupload(dynamo):
    chunks = _make_chunks(3)
    replace_document(dynamo, DOCUMENTS_TABLE, CHUNKS_TABLE, "documents/a.txt", chunks)

    new_chunks = _make_chunks(5)
    replace_document(dynamo, DOCUMENTS_TABLE, CHUNKS_TABLE, "documents/a.txt", new_chunks)

    doc_resp = dynamo.scan(TableName=DOCUMENTS_TABLE)
    assert len(doc_resp["Items"]) == 1
    assert doc_resp["Items"][0]["chunk_count"]["N"] == "5"

    chunk_resp = dynamo.scan(TableName=CHUNKS_TABLE)
    assert len(chunk_resp["Items"]) == 5


def test_different_source_keys_coexist(dynamo):
    replace_document(dynamo, DOCUMENTS_TABLE, CHUNKS_TABLE, "documents/a.txt", _make_chunks(2))
    replace_document(dynamo, DOCUMENTS_TABLE, CHUNKS_TABLE, "documents/b.txt", _make_chunks(4))

    assert dynamo.scan(TableName=DOCUMENTS_TABLE)["Count"] == 2
    assert dynamo.scan(TableName=CHUNKS_TABLE)["Count"] == 6


def test_update_document_status(dynamo):
    chunks = _make_chunks(2)
    document_id = replace_document(
        dynamo,
        DOCUMENTS_TABLE,
        CHUNKS_TABLE,
        "documents/a.txt",
        chunks,
    )

    update_document_status(dynamo, DOCUMENTS_TABLE, document_id, "embedded")

    resp = dynamo.get_item(
        TableName=DOCUMENTS_TABLE,
        Key={"document_id": {"S": document_id}},
    )
    assert resp["Item"]["status"]["S"] == "embedded"


def test_no_text_document_status(dynamo):
    replace_document(dynamo, DOCUMENTS_TABLE, CHUNKS_TABLE, "documents/empty.txt", [])

    resp = dynamo.scan(TableName=DOCUMENTS_TABLE)
    assert resp["Items"][0]["status"]["S"] == "no_text"
    assert resp["Items"][0]["chunk_count"]["N"] == "0"
