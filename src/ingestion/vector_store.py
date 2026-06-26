import json
from datetime import datetime, timezone


def replace_document_vectors(
    rdsdata_client,
    cluster_arn: str,
    secret_arn: str,
    database: str,
    source_key: str,
    document_id: str,
    chunks_with_embeddings: list[dict],
) -> None:
    transaction_id = _begin_transaction(rdsdata_client, cluster_arn, secret_arn, database)

    try:
        _execute(
            rdsdata_client,
            transaction_id,
            cluster_arn,
            secret_arn,
            database,
            "DELETE FROM chunks WHERE source_key = :source_key",
            {"source_key": {"stringValue": source_key}},
        )
        _execute(
            rdsdata_client,
            transaction_id,
            cluster_arn,
            secret_arn,
            database,
            "DELETE FROM documents WHERE source_key = :source_key",
            {"source_key": {"stringValue": source_key}},
        )

        now = datetime.now(timezone.utc).isoformat()
        _execute(
            rdsdata_client,
            transaction_id,
            cluster_arn,
            secret_arn,
            database,
            (
                "INSERT INTO documents (document_id, source_key, chunk_count, status, created_at) "
                "VALUES (:document_id, :source_key, :chunk_count, :status, :created_at::timestamptz)"
            ),
            {
                "document_id": {"stringValue": document_id},
                "source_key": {"stringValue": source_key},
                "chunk_count": {"longValue": len(chunks_with_embeddings)},
                "status": {"stringValue": "embedded"},
                "created_at": {"stringValue": now},
            },
        )

        for chunk in chunks_with_embeddings:
            _execute(
                rdsdata_client,
                transaction_id,
                cluster_arn,
                secret_arn,
                database,
                (
                    "INSERT INTO chunks (document_id, chunk_index, text, char_count, source_key, created_at, embedding) "
                    "VALUES (:document_id, :chunk_index, :text, :char_count, :source_key, :created_at::timestamptz, :embedding::vector)"
                ),
                {
                    "document_id": {"stringValue": document_id},
                    "chunk_index": {"longValue": chunk["chunk_index"]},
                    "text": {"stringValue": chunk["text"]},
                    "char_count": {"longValue": chunk["char_count"]},
                    "source_key": {"stringValue": source_key},
                    "created_at": {"stringValue": now},
                    "embedding": {"stringValue": json.dumps(chunk["embedding"])},
                },
            )

        rdsdata_client.commit_transaction(
            resourceArn=cluster_arn,
            secretArn=secret_arn,
            transactionId=transaction_id,
        )
    except Exception:
        rdsdata_client.rollback_transaction(
            resourceArn=cluster_arn,
            secretArn=secret_arn,
            transactionId=transaction_id,
        )
        raise


def _begin_transaction(rdsdata_client, cluster_arn: str, secret_arn: str, database: str) -> str:
    response = rdsdata_client.begin_transaction(
        resourceArn=cluster_arn,
        secretArn=secret_arn,
        database=database,
    )
    return response["transactionId"]


def _execute(
    rdsdata_client,
    transaction_id: str,
    cluster_arn: str,
    secret_arn: str,
    database: str,
    sql: str,
    parameters: dict,
) -> None:
    rdsdata_client.execute_statement(
        resourceArn=cluster_arn,
        secretArn=secret_arn,
        transactionId=transaction_id,
        database=database,
        sql=sql,
        parameters=[
            {"name": key, "value": value} for key, value in parameters.items()
        ],
    )
