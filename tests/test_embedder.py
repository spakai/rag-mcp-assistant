from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from src.ingestion.embedder import embed_chunks


def test_embed_chunks_returns_embeddings():
    client = MagicMock()
    client.invoke_model.return_value = {
        "body": MagicMock()
    }
    client.invoke_model.return_value["body"].read.return_value = (
        b'{"embedding": [0.1, 0.2, 0.3]}'
    )

    chunks = [
        {"chunk_index": 0, "text": "hello", "char_count": 5},
        {"chunk_index": 1, "text": "world", "char_count": 5},
    ]

    result = embed_chunks(client, chunks, "model-id")

    assert len(result) == 2
    assert len(result[0]["embedding"]) == 3
    assert result[0]["embedding"] == [0.1, 0.2, 0.3]
    assert result[1]["embedding"] == [0.1, 0.2, 0.3]


def test_embed_chunks_retries_on_throttling():
    client = MagicMock()
    response = {"body": MagicMock()}
    response["body"].read.side_effect = [
        b'{"embedding": [0.5, 0.6, 0.7]}',
        b'{"embedding": [0.5, 0.6, 0.7]}',
        b'{"embedding": [0.5, 0.6, 0.7]}',
    ]

    client.invoke_model.side_effect = [
        ClientError({"Error": {"Code": "ThrottlingException"}}, "InvokeModel"),
        ClientError({"Error": {"Code": "ThrottlingException"}}, "InvokeModel"),
        {"body": response["body"]},
    ]

    chunks = [{"chunk_index": 0, "text": "hello", "char_count": 5}]

    result = embed_chunks(client, chunks, "model-id", max_retries=3)

    assert len(result[0]["embedding"]) == 3
    assert client.invoke_model.call_count == 3


def test_embed_chunks_raises_after_throttling_exhausted():
    client = MagicMock()
    client.invoke_model.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException"}}, "InvokeModel"
    )

    with pytest.raises(ClientError):
        embed_chunks(client, [{"chunk_index": 0, "text": "hello", "char_count": 5}], "model-id")


def test_embed_chunks_empty_list():
    client = MagicMock()

    assert embed_chunks(client, [], "model-id") == []
    client.invoke_model.assert_not_called()
