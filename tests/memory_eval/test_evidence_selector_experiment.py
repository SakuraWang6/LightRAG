from memory_eval_tests.experiments.evidence_selector_experiment import _make_candidates, _parse_selection


def test_selector_parses_only_known_ids_and_enforces_limit():
    candidates = _make_candidates(
        [
            {"entity": "FACT-00001", "type": "concept", "description": "one"},
            {"entity": "FACT-00002", "type": "concept", "description": "two"},
        ]
    )
    raw = '{"selected_evidence_ids": ["' + candidates[1]["evidence_id"] + '", "not-an-id"]}'
    assert _parse_selection(raw, candidates, 1) == [candidates[1]["evidence_id"]]


def test_selector_falls_back_to_ranked_candidates_for_invalid_json():
    candidates = _make_candidates(
        [
            {"entity": "FACT-00001", "type": "concept", "description": "one"},
            {"entity": "FACT-00002", "type": "concept", "description": "two"},
        ]
    )
    assert _parse_selection("not json", candidates, 1) == [candidates[0]["evidence_id"]]
