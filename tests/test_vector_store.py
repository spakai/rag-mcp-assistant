from unittest.mock import MagicMock

from src.ingestion.vector_store import replace_document_vectors


def test_begins_transaction():
    client = MagicMock()
    replace_document_vectors(
        client,
        "cluster-arn",
        "secret-arn",
        "rag",
        "documents/a.txt",
        "doc-1",
        [{"chunk_index": 0, "text": "hello", "char_count": 5, "embedding": [0.1]}],
    )

    assert client.begin_transaction.call_count == 1


def test_deletes_before_inserts():
    client = MagicMock()
    replace_document_vectors(
        client,
        "cluster-arn",
        "secret-arn",
        "rag",
        "documents/a.txt",
        "doc-1",
        [{"chunk_index": 0, "text": "hello", "char_count": 5, "embedding": [0.1]}],
    )

    calls = client.execute_statement.call_args_list
    assert "DELETE FROM chunks" in calls[0].kwargs["sql"]
    assert "INSERT INTO chunks" in calls[-1].kwargs["sql"]


def test_commits():
    client = MagicMock()
    replace_document_vectors(
        client,
        "cluster-arn",
        "secret-arn",
        "rag",
        "documents/a.txt",
        "doc-1",
        [{"chunk_index": 0, "text": "hello", "char_count": 5, "embedding": [0.1]}],
    )

    client.commit_transaction.assert_called_once()


def test_idempotent_second_call_deletes():
    client = MagicMock()
    chunks = [{"chunk_index": 0, "text": "hello", "char_count": 5, "embedding": [0.1]}]

    replace_document_vectors(
        client,
        "cluster-arn",
        "secret-arn",
        "rag",
        "documents/a.txt",
        "doc-1",
        chunks,
    )
    replace_document_vectors(
        client,
        "cluster-arn",
        "secret-arn",
        "rag",
        "documents/a.txt",
        "doc-1",
        chunks,
    )

    assert client.execute_statement.call_count >= 4


def test_embedding_passed_as_string_value():
    client = MagicMock()
    client.execute_statement.return_value = {}
    replace_document_vectors(
        client,
        "cluster-arn",
        "secret-arn",
        "rag",
        "documents/a.txt",
        "doc-1",
        [{"chunk_index": 0, "text": "hello", "char_count": 5, "embedding": [0.1]}],
    )

    params = client.execute_statement.call_args_list[-1].kwargs["parameters"]
    embedding_param = next(
        param for param in params if param["name"] == "embedding"
    )
    assert embedding_param["value"]["stringValue"]
