def score_retrieval(golden_set: list[dict], retrieved_results: list[dict]) -> float:
    """Hit rate: fraction of questions where at least one expected_source_key is returned."""
    if not golden_set:
        return 0.0
    results_by_id = {r["id"]: r["returned_source_keys"] for r in retrieved_results}
    hits = sum(
        1
        for item in golden_set
        if any(k in results_by_id.get(item["id"], []) for k in item["expected_source_keys"])
    )
    return hits / len(golden_set)


def score_answer(golden_set: list[dict], generated_answers: list[dict]) -> float:
    """Average keyword-presence fraction across all questions (case-insensitive).

    An entry with no expected_answer_keywords counts as 1.0.
    """
    if not golden_set:
        return 0.0
    answers_by_id = {a["id"]: a["answer"].lower() for a in generated_answers}
    total = 0.0
    for item in golden_set:
        answer = answers_by_id.get(item["id"], "")
        keywords = item.get("expected_answer_keywords", [])
        if not keywords:
            total += 1.0
            continue
        total += sum(1 for kw in keywords if kw.lower() in answer) / len(keywords)
    return total / len(golden_set)
