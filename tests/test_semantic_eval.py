from rd_cockpit.semantic_eval import evaluate_cases


def test_semantic_golden_suite_passes() -> None:
    result = evaluate_cases()
    assert result["failed"] == 0, result["results"]
    assert result["passed"] >= 3
