from memory_eval_tests.online.answer_eval import _canonical_formula, _formula_match, score_answer


def test_formula_normalization_accepts_latex_unicode_and_fraction_variants():
    expected = r"E_{5}=P_{5}T_{5}/\eta_{5}"
    variants = (
        "E_5 = P_5 T_5 / eta_5",
        r"E_{5}=\frac{P_{5}T_{5}}{\eta_{5}}",
        r"E_5 = \frac{P_5 T_5}{η_5}",
        "E_{5} = (P_{5} * T_{5}) / η_{5}",
    )
    assert [_canonical_formula(value) for value in variants] == [_canonical_formula(expected)] * len(variants)
    assert all(_formula_match(expected, value) for value in variants)


def test_formula_normalization_keeps_operator_structure():
    expected = r"E_{5}=P_{5}T_{5}/\eta_{5}"
    assert not _formula_match(expected, "E_5 = P_5 + T_5 / eta_5")


def test_abstention_synonyms_are_recognized_deterministically():
    question = {"question_type": "abstain", "expected_behavior": "abstain"}
    variants = (
        "The document does not mention this approval code.",
        "It cannot be addressed from the provided context.",
        "There is insufficient information to determine the answer.",
        "The context does not contain the requested appendix.",
    )
    assert all(
        score_answer(
            answer_text=value,
            expected="The document does not provide this information.",
            question=question,
            evidence_facts=[],
            references_blob="",
        )["abstention_correct"]
        for value in variants
    )


def test_evidence_and_citation_metrics_are_not_conflated():
    fact = {"fact_id": "FACT-00001", "answer": "9021 QMU", "expected_text": "9021 QMU"}
    base = dict(
        expected="9021 QMU",
        question={"question_type": "direct_numeric", "expected_behavior": "answer"},
        evidence_facts=[fact],
        references_blob="Evidence: FACT-00001 = 9021 QMU",
    )
    no_citation = score_answer(answer_text="The answer is 9021 QMU.", **base)
    cited = score_answer(answer_text="The answer is 9021 QMU (FACT-00001).", **base)

    assert no_citation["evidence_available"] is True
    assert no_citation["citation_presence"] is False
    assert no_citation["citation_correctness"] is None
    assert no_citation["grounded"] is True
    assert cited["citation_presence"] is True
    assert cited["citation_correctness"] is True
