import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from src.query.retrieval import retrieve_and_answer, retrieve_chunks


def _make_bedrock_embed_response(embedding):
    body = MagicMock()
    body.read.return_value = json.dumps({"embedding": embedding}).encode()
    return {"body": body}


def _make_bedrock_generate_response(text):
    return {
        "output": {
            "message": {
                "content": [{"text": text}]
            }
        }
    }


def _make_rds_response(rows):
    """rows: list of (chunk_index, text, source_key) tuples"""
    return {
        "columnMetadata": [
            {"label": "chunk_index"},
            {"label": "text"},
            {"label": "source_key"},
        ],
        "records": [
            [
                {"longValue": r[0]},
                {"stringValue": r[1]},
                {"stringValue": r[2]},
            ]
            for r in rows
        ],
    }


def _mock_bedrock(embedding, answer_text):
    """Return a bedrock mock wired for one embed (invoke_model) + one generate (converse)."""
    bedrock = MagicMock()
    bedrock.invoke_model.return_value = _make_bedrock_embed_response(embedding)
    bedrock.converse.return_value = _make_bedrock_generate_response(answer_text)
    return bedrock


def test_retrieve_and_answer_happy_path():
    bedrock = _mock_bedrock([0.1] * 1024, "The answer is 42.")

    rdsdata = MagicMock()
    rdsdata.execute_statement.return_value = _make_rds_response([
        (0, "Chunk text about 42.", "documents/doc.txt"),
        (1, "More context here.", "documents/doc.txt"),
    ])

    result = retrieve_and_answer(
        rdsdata, bedrock,
        "arn:cluster", "arn:secret", "rag",
        "What is the answer?",
        "embed-model", "gen-model",
        top_k=5,
    )

    assert result["answer"] == "The answer is 42."
    assert len(result["sources"]) == 2
    assert result["sources"][0] == {
        "source_key": "documents/doc.txt",
        "chunk_index": 0,
        "text": "Chunk text about 42.",
    }


def test_retrieve_and_answer_no_results():
    bedrock = MagicMock()
    bedrock.invoke_model.return_value = _make_bedrock_embed_response([0.1] * 1024)

    rdsdata = MagicMock()
    rdsdata.execute_statement.return_value = _make_rds_response([])

    result = retrieve_and_answer(
        rdsdata, bedrock,
        "arn:cluster", "arn:secret", "rag",
        "What is the answer?",
        "embed-model", "gen-model",
    )

    assert result["answer"] == "No relevant documents found."
    assert result["sources"] == []
    # converse must NOT be called when there are no chunks
    bedrock.converse.assert_not_called()


def test_retrieve_and_answer_top_k_passed_to_query():
    bedrock = _mock_bedrock([0.0] * 1024, "answer")

    rdsdata = MagicMock()
    rdsdata.execute_statement.return_value = _make_rds_response([
        (0, "text", "documents/a.txt"),
    ])

    retrieve_and_answer(
        rdsdata, bedrock,
        "arn:cluster", "arn:secret", "rag",
        "question", "embed-model", "gen-model",
        top_k=3,
    )

    call_kwargs = rdsdata.execute_statement.call_args
    params = {p["name"]: p["value"] for p in call_kwargs.kwargs["parameters"]}
    assert params["k"] == {"longValue": 3}


def test_embed_question_retries_on_throttling():
    bedrock = MagicMock()
    bedrock.invoke_model.side_effect = [
        ClientError({"Error": {"Code": "ThrottlingException"}}, "InvokeModel"),
        _make_bedrock_embed_response([0.1] * 1024),
    ]
    bedrock.converse.return_value = _make_bedrock_generate_response("ok")

    rdsdata = MagicMock()
    rdsdata.execute_statement.return_value = _make_rds_response([
        (0, "some text", "documents/x.txt"),
    ])

    result = retrieve_and_answer(
        rdsdata, bedrock,
        "arn:cluster", "arn:secret", "rag",
        "question", "embed-model", "gen-model",
    )

    assert result["answer"] == "ok"
    assert bedrock.invoke_model.call_count == 2


def test_embed_question_raises_after_retries_exhausted():
    bedrock = MagicMock()
    bedrock.invoke_model.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException"}}, "InvokeModel"
    )
    rdsdata = MagicMock()

    with pytest.raises(ClientError):
        retrieve_and_answer(
            rdsdata, bedrock,
            "arn:cluster", "arn:secret", "rag",
            "question", "embed-model", "gen-model",
        )


def test_sources_shape():
    bedrock = _mock_bedrock([0.1] * 1024, "answer")

    rdsdata = MagicMock()
    rdsdata.execute_statement.return_value = _make_rds_response([
        (2, "text two", "documents/b.txt"),
    ])

    result = retrieve_and_answer(
        rdsdata, bedrock,
        "arn:cluster", "arn:secret", "rag",
        "question", "embed-model", "gen-model",
    )

    source = result["sources"][0]
    assert set(source.keys()) == {"source_key", "chunk_index", "text"}
    assert source["chunk_index"] == 2
    assert source["source_key"] == "documents/b.txt"


# ── retrieve_chunks ───────────────────────────────────────────────────────────

def test_retrieve_chunks_returns_list_of_dicts():
    bedrock = MagicMock()
    bedrock.invoke_model.return_value = _make_bedrock_embed_response([0.1] * 1024)

    rdsdata = MagicMock()
    rdsdata.execute_statement.return_value = _make_rds_response([
        (0, "text A", "documents/a.txt"),
        (1, "text B", "documents/a.txt"),
    ])

    result = retrieve_chunks(
        rdsdata, bedrock,
        "arn:cluster", "arn:secret", "rag",
        "what is A?", "embed-model",
        top_k=5,
    )

    assert len(result) == 2
    assert result[0] == {"source_key": "documents/a.txt", "chunk_index": 0, "text": "text A"}
    assert result[1] == {"source_key": "documents/a.txt", "chunk_index": 1, "text": "text B"}
    # No generation call
    bedrock.converse.assert_not_called()


def test_retrieve_chunks_empty_results():
    bedrock = MagicMock()
    bedrock.invoke_model.return_value = _make_bedrock_embed_response([0.0] * 1024)

    rdsdata = MagicMock()
    rdsdata.execute_statement.return_value = _make_rds_response([])

    result = retrieve_chunks(
        rdsdata, bedrock,
        "arn:cluster", "arn:secret", "rag",
        "unknown", "embed-model",
    )

    assert result == []
    bedrock.converse.assert_not_called()
