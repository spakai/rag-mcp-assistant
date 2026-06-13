import pytest

from src.ingestion.chunker import chunk_text


def test_empty_text_yields_nothing():
    assert list(chunk_text("")) == []


def test_single_chunk_shorter_than_size():
    text = "hello world"
    chunks = list(chunk_text(text, chunk_size=100, overlap=10))
    assert len(chunks) == 1
    assert chunks[0]["text"] == text
    assert chunks[0]["char_count"] == len(text)
    assert chunks[0]["chunk_index"] == 0


def test_chunk_size_exact_multiple():
    text = "a" * 100
    chunks = list(chunk_text(text, chunk_size=50, overlap=0))
    assert len(chunks) == 2
    assert all(c["char_count"] == 50 for c in chunks)


def test_overlap_produces_correct_boundaries():
    text = "abcdefghij"  # 10 chars
    # chunk_size=6, overlap=2 -> windows start at 0, 4, 8
    chunks = list(chunk_text(text, chunk_size=6, overlap=2))
    assert chunks[0]["text"] == "abcdef"
    assert chunks[1]["text"] == "efghij"
    assert len(chunks) == 2


def test_final_short_chunk():
    text = "a" * 110
    chunks = list(chunk_text(text, chunk_size=100, overlap=10))
    # window 0: 0-100, window 1: 90-110 (20 chars)
    assert len(chunks) == 2
    assert chunks[-1]["char_count"] == 20


def test_chunk_indexes_are_sequential():
    text = "x" * 500
    chunks = list(chunk_text(text, chunk_size=100, overlap=20))
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))


def test_char_count_matches_text_length():
    text = "The quick brown fox jumps over the lazy dog. " * 5
    for chunk in chunk_text(text, chunk_size=50, overlap=10):
        assert chunk["char_count"] == len(chunk["text"])


@pytest.mark.parametrize("size,overlap", [(1000, 100), (500, 50)])
def test_default_like_sizes(size, overlap):
    text = "word " * 600  # ~3000 chars
    chunks = list(chunk_text(text, chunk_size=size, overlap=overlap))
    for c in chunks[:-1]:
        assert c["char_count"] == size
    assert chunks[-1]["char_count"] <= size
