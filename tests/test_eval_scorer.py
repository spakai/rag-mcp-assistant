from evals.scorer import score_answer, score_retrieval

GOLDEN = [
    {
        "id": "q001",
        "expected_source_keys": ["documents/doc-a.txt"],
        "expected_answer_keywords": ["alpha", "beta", "gamma"],
    },
    {
        "id": "q002",
        "expected_source_keys": ["documents/doc-b.txt", "documents/doc-c.txt"],
        "expected_answer_keywords": ["delta"],
    },
]


# --- score_retrieval ---


def test_score_retrieval_all_hits():
    retrieved = [
        {"id": "q001", "returned_source_keys": ["documents/doc-a.txt"]},
        {"id": "q002", "returned_source_keys": ["documents/doc-c.txt"]},
    ]
    assert score_retrieval(GOLDEN, retrieved) == 1.0


def test_score_retrieval_no_hits():
    retrieved = [
        {"id": "q001", "returned_source_keys": ["documents/wrong.txt"]},
        {"id": "q002", "returned_source_keys": []},
    ]
    assert score_retrieval(GOLDEN, retrieved) == 0.0


def test_score_retrieval_partial():
    retrieved = [
        {"id": "q001", "returned_source_keys": ["documents/doc-a.txt"]},
        {"id": "q002", "returned_source_keys": ["documents/wrong.txt"]},
    ]
    assert score_retrieval(GOLDEN, retrieved) == 0.5


def test_score_retrieval_or_logic_second_key_matches():
    retrieved = [
        {"id": "q001", "returned_source_keys": ["documents/doc-a.txt"]},
        {"id": "q002", "returned_source_keys": ["documents/doc-b.txt"]},
    ]
    assert score_retrieval(GOLDEN, retrieved) == 1.0


def test_score_retrieval_missing_id_treated_as_empty():
    retrieved = [
        {"id": "q001", "returned_source_keys": ["documents/doc-a.txt"]},
        # q002 is absent from results
    ]
    assert score_retrieval(GOLDEN, retrieved) == 0.5


def test_score_retrieval_empty_golden_set():
    assert score_retrieval([], []) == 0.0


# --- score_answer ---


def test_score_answer_all_keywords_present():
    answers = [
        {"id": "q001", "answer": "The values are alpha, beta, and gamma."},
        {"id": "q002", "answer": "Delta is the key concept."},
    ]
    assert score_answer(GOLDEN, answers) == 1.0


def test_score_answer_no_keywords_in_entry():
    golden = [{"id": "q001", "expected_source_keys": [], "expected_answer_keywords": []}]
    answers = [{"id": "q001", "answer": "anything at all"}]
    assert score_answer(golden, answers) == 1.0


def test_score_answer_partial_match():
    answers = [
        {"id": "q001", "answer": "alpha and beta are here"},  # 2/3 keywords
        {"id": "q002", "answer": "delta found"},              # 1/1 keyword
    ]
    expected = (2 / 3 + 1.0) / 2
    assert abs(score_answer(GOLDEN, answers) - expected) < 1e-9


def test_score_answer_case_insensitive():
    answers = [
        {"id": "q001", "answer": "ALPHA, BETA, and GAMMA are all present"},
        {"id": "q002", "answer": "DELTA is here"},
    ]
    assert score_answer(GOLDEN, answers) == 1.0


def test_score_answer_missing_id_treated_as_empty_answer():
    answers = [
        {"id": "q001", "answer": "alpha beta gamma"},
        # q002 absent → answer treated as "" → 0/1 keywords
    ]
    expected = (1.0 + 0.0) / 2
    assert abs(score_answer(GOLDEN, answers) - expected) < 1e-9


def test_score_answer_empty_golden_set():
    assert score_answer([], []) == 0.0
