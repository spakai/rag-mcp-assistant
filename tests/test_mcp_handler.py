from unittest.mock import MagicMock, patch

from src.mcp.handler import ask_question, search_documents


# ── Helpers ──────────────────────────────────────────────────────────────────

SAMPLE_CHUNKS = [
    {"source_key": "documents/doc.txt", "chunk_index": 0, "text": "Chunk text A."},
    {"source_key": "documents/doc.txt", "chunk_index": 1, "text": "Chunk text B."},
]

SAMPLE_ANSWER = {"answer": "The answer is 42.", "sources": SAMPLE_CHUNKS}


def _set_env(monkeypatch):
    monkeypatch.setenv("BEDROCK_EMBEDDING_MODEL_ID", "embed-model")
    monkeypatch.setenv("BEDROCK_GENERATION_MODEL_ID", "gen-model")
    monkeypatch.setenv("AURORA_CLUSTER_ARN", "arn:aws:rds:us-east-1:123:cluster:rag")
    monkeypatch.setenv("AURORA_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123:secret:rag")
    monkeypatch.setenv("AURORA_DATABASE", "rag")
    monkeypatch.setenv("RETRIEVAL_TOP_K", "5")


# ── search_documents ──────────────────────────────────────────────────────────

def test_search_documents_not_configured_returns_empty(monkeypatch):
    monkeypatch.delenv("BEDROCK_EMBEDDING_MODEL_ID", raising=False)
    result = search_documents("what is X?")
    assert result == []


def test_search_documents_returns_chunks(monkeypatch):
    _set_env(monkeypatch)
    with (
        patch("src.mcp.handler.retrieve_chunks", return_value=SAMPLE_CHUNKS) as mock_rc,
        patch("src.mcp.handler._clients", return_value=(MagicMock(), MagicMock())),
    ):
        result = search_documents("what is the answer?")

    assert result == SAMPLE_CHUNKS
    mock_rc.assert_called_once()
    call_kwargs = mock_rc.call_args
    assert call_kwargs.args[5] == "what is the answer?"  # query positional arg
    assert call_kwargs.args[6] == "embed-model"


def test_search_documents_empty_results(monkeypatch):
    _set_env(monkeypatch)
    with (
        patch("src.mcp.handler.retrieve_chunks", return_value=[]),
        patch("src.mcp.handler._clients", return_value=(MagicMock(), MagicMock())),
    ):
        result = search_documents("unknown topic")

    assert result == []


def test_search_documents_passes_top_k(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.setenv("RETRIEVAL_TOP_K", "3")
    with (
        patch("src.mcp.handler.retrieve_chunks", return_value=[]) as mock_rc,
        patch("src.mcp.handler._clients", return_value=(MagicMock(), MagicMock())),
    ):
        search_documents("query")

    call_kwargs = mock_rc.call_args
    assert call_kwargs.args[7] == 3  # top_k positional arg


# ── ask_question ──────────────────────────────────────────────────────────────

def test_ask_question_not_configured_returns_graceful_message(monkeypatch):
    monkeypatch.delenv("BEDROCK_EMBEDDING_MODEL_ID", raising=False)
    result = ask_question("what is X?")
    assert "not configured" in result["answer"].lower()
    assert result["sources"] == []


def test_ask_question_returns_answer_and_sources(monkeypatch):
    _set_env(monkeypatch)
    with (
        patch("src.mcp.handler.retrieve_and_answer", return_value=SAMPLE_ANSWER) as mock_raa,
        patch("src.mcp.handler._clients", return_value=(MagicMock(), MagicMock())),
    ):
        result = ask_question("What is the answer?")

    assert result["answer"] == "The answer is 42."
    assert len(result["sources"]) == 2
    mock_raa.assert_called_once()


def test_ask_question_no_relevant_docs(monkeypatch):
    _set_env(monkeypatch)
    no_docs = {"answer": "No relevant documents found.", "sources": []}
    with (
        patch("src.mcp.handler.retrieve_and_answer", return_value=no_docs),
        patch("src.mcp.handler._clients", return_value=(MagicMock(), MagicMock())),
    ):
        result = ask_question("unrelated question")

    assert result["answer"] == "No relevant documents found."
    assert result["sources"] == []


def test_ask_question_passes_generation_model(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.setenv("BEDROCK_GENERATION_MODEL_ID", "nova-model")
    with (
        patch("src.mcp.handler.retrieve_and_answer", return_value=SAMPLE_ANSWER) as mock_raa,
        patch("src.mcp.handler._clients", return_value=(MagicMock(), MagicMock())),
    ):
        ask_question("question")

    call_kwargs = mock_raa.call_args
    assert call_kwargs.args[7] == "nova-model"  # generation_model_id positional arg
