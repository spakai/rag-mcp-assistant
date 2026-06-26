from unittest.mock import MagicMock

from src.ingestion.handler import _process


def test_process_skips_embedding_without_model_env(monkeypatch):
    s3 = MagicMock()
    dynamo = MagicMock()
    monkeypatch.setenv("BEDROCK_EMBEDDING_MODEL_ID", "")

    _process(
        s3,
        dynamo,
        "bucket",
        "documents/test.txt",
        "documents",
        "chunks",
        1000,
        100,
    )

    dynamo.put_item.assert_called_once()
