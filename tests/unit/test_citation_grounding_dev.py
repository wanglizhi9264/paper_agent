from eval.citation_grounding_dev import classify_citation_failure, unparsed_source_marker_indices


def test_missing_citation_is_classified_without_guessing_a_source() -> None:
    assert classify_citation_failure(cited=[], relevant={"gold"}, gold_group_count=1) == [
        "GOLD_IN_CONTEXT_NOT_CITED",
        "MISSING_CITATION",
    ]


def test_wrong_and_excessive_citations_are_non_exclusive() -> None:
    labels = classify_citation_failure(
        cited=["a", "b", "c", "d"],
        relevant={"gold"},
        gold_group_count=1,
    )

    assert labels == [
        "GOLD_IN_CONTEXT_NOT_CITED",
        "WRONG_CONTEXT_CITED",
        "OVER_CITATION",
    ]


def test_partial_gold_citation_is_not_called_wrong_context() -> None:
    labels = classify_citation_failure(
        cited=["gold-a"],
        relevant={"gold-a", "gold-b"},
        gold_group_count=1,
    )

    assert labels == ["GOLD_IN_CONTEXT_NOT_CITED"]


def test_source_style_markers_are_reported_but_not_treated_as_valid_citations() -> None:
    assert unparsed_source_marker_indices("Claim [Source 2], repeated [source 7].") == [2, 7]
