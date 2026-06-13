import uuid
from datetime import datetime, timezone


def replace_document(
    dynamo_client,
    documents_table: str,
    chunks_table: str,
    source_key: str,
    chunks: list[dict],
) -> str:
    _delete_existing(dynamo_client, documents_table, chunks_table, source_key)

    document_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with _batch_writer(dynamo_client, chunks_table) as writer:
        for chunk in chunks:
            writer.put_item(
                Item={
                    "document_id": document_id,
                    "chunk_index": chunk["chunk_index"],
                    "text": chunk["text"],
                    "char_count": chunk["char_count"],
                    "source_key": source_key,
                    "created_at": now,
                }
            )

    dynamo_client.put_item(
        TableName=documents_table,
        Item={
            "document_id": {"S": document_id},
            "source_key": {"S": source_key},
            "chunk_count": {"N": str(len(chunks))},
            "status": {"S": "ingested" if chunks else "no_text"},
            "created_at": {"S": now},
        },
    )

    return document_id


def _delete_existing(dynamo_client, documents_table: str, chunks_table: str, source_key: str) -> None:
    resp = dynamo_client.query(
        TableName=documents_table,
        IndexName="source_key_index",
        KeyConditionExpression="source_key = :sk",
        ExpressionAttributeValues={":sk": {"S": source_key}},
    )
    for item in resp.get("Items", []):
        old_id = item["document_id"]["S"]
        _delete_chunks(dynamo_client, chunks_table, old_id)
        dynamo_client.delete_item(
            TableName=documents_table,
            Key={"document_id": {"S": old_id}},
        )


def _delete_chunks(dynamo_client, chunks_table: str, document_id: str) -> None:
    paginator = dynamo_client.get_paginator("query")
    for page in paginator.paginate(
        TableName=chunks_table,
        KeyConditionExpression="document_id = :did",
        ExpressionAttributeValues={":did": {"S": document_id}},
    ):
        for item in page.get("Items", []):
            dynamo_client.delete_item(
                TableName=chunks_table,
                Key={
                    "document_id": {"S": document_id},
                    "chunk_index": item["chunk_index"],
                },
            )


class _batch_writer:
    """Minimal batch writer that accumulates PutRequest items and flushes in batches of 25."""

    def __init__(self, dynamo_client, table_name: str):
        self._client = dynamo_client
        self._table = table_name
        self._buffer: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._flush()

    def put_item(self, Item: dict) -> None:
        self._buffer.append({"PutRequest": {"Item": {k: _to_attr(v) for k, v in Item.items()}}})
        if len(self._buffer) >= 25:
            self._flush()

    def _flush(self) -> None:
        if not self._buffer:
            return
        self._client.batch_write_item(RequestItems={self._table: self._buffer})
        self._buffer = []


def _to_attr(value) -> dict:
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, int):
        return {"N": str(value)}
    raise TypeError(f"Unsupported DynamoDB attribute type: {type(value)}")
