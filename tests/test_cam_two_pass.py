"""The CAM is written in two passes because one does not fit.

sarvam-105b stops at 16,384 output tokens. Across every recorded call none
exceeded it, and four consecutive CAMs terminated there with
finish_reason="length" - so the ceiling belongs to the model, and raising
max_tokens (the provider accepts 32768 and 65536) changed nothing.

These tests pin the split and, more importantly, what happens when only half
of it succeeds.
"""

import asyncio
import json

import pytest

from app.agents.orchestration.cam_generator import CAMGeneratorAgent

FACTUAL = {
    "document_control": {"borrower_name": "Sahyadri Precision Castings Private Limited",
                         "status": "COMPLETE"},
    "executive_summary": {"industry": "Precision castings", "revenue": "Rs 38.42 Cr",
                          "ebitda": "Rs 8.48 Cr", "pat": "Rs 3.58 Cr",
                          "strengths": ["EBITDA margin 22.1%"],
                          "key_concerns": ["Receivables at 1,092 L"]},
    "borrower_profile": {"legal_name": "Sahyadri Precision Castings Private Limited"},
    "facility": {"requested_amount": "Rs 9.00 Cr"},
    "ratios": {"key_ratios": [{"name": "D/E", "value": "0.64"}]},
}

JUDGEMENT = {
    "five_cs": {k: {"evidence": "Schedule III figures", "assessment": "Adequate",
                    "risk_implication": "Moderate"}
                for k in ("character", "capacity", "capital", "collateral", "conditions")},
    "risk_assessment": {"overall": "Moderate"},
    "recommendation": {"decision": "APPROVE WITH CONDITIONS",
                       "rationale": "D/E of 0.64 and DSCR support the facility."},
    "evidence_register": [{"finding": "Revenue", "value": "Rs 38.42 Cr",
                           "source_document": "audited P&L", "page": "5",
                           "status": "VERIFIED"}],
}


def _agent():
    agent = CAMGeneratorAgent()
    agent.structured_llm = None
    return agent


class _Responder:
    """Answers each pass in turn. `fail_on` names a pass that returns nothing."""

    def __init__(self, fail_on=None, truncate_on=None):
        self.calls = []
        self.fail_on = fail_on
        self.truncate_on = truncate_on


def _run(agent, responder, monkeypatch):
    import app.agents.orchestration.cam_generator as cg

    monkeypatch.setenv("SARVAM_API_KEY", "offline-test-not-a-real-key")

    # httpx.AsyncClient is replaced with one that records which pass it was
    # handed, so the test can assert the second pass is not paid for when the
    # first fails.
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *args, **kwargs):
            return _Made(responder, kwargs)

    monkeypatch.setattr(cg.httpx, "AsyncClient", _Client)
    return asyncio.run(agent.generate_cam({"total_revenue": 384200000}, {}, {}, 72))


class _Made:
    status_code = 200

    def __init__(self, responder, kwargs):
        body = json.dumps(kwargs.get("json", {}))
        self.which = "judgement" if "five_cs" in body else "factual"
        responder.calls.append(self.which)
        self.responder = responder

    def raise_for_status(self):
        pass

    def json(self):
        if self.which == self.responder.truncate_on:
            return {"choices": [{"finish_reason": "length",
                                 "message": {"content": '{"five_cs": {',
                                             "reasoning_content": ""}}]}
        if self.which == self.responder.fail_on:
            return {"choices": [{"finish_reason": "stop",
                                 "message": {"content": "", "reasoning_content": ""}}]}
        payload = JUDGEMENT if self.which == "judgement" else FACTUAL
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": json.dumps(payload),
                                         "reasoning_content": ""}}]}


class TestTheSplitItself:
    def test_the_two_passes_partition_the_document(self):
        overlap = set(CAMGeneratorAgent.FACTUAL_SECTIONS) & set(
            CAMGeneratorAgent.JUDGEMENT_SECTIONS)
        assert overlap == set(), f"a section asked for twice wastes budget: {overlap}"

    def test_together_they_cover_every_section(self):
        from app.agents.orchestration.cam_generator import CAMDocument

        covered = set(CAMGeneratorAgent.FACTUAL_SECTIONS) | set(
            CAMGeneratorAgent.JUDGEMENT_SECTIONS)
        assert covered == set(CAMDocument.model_fields)

    def test_the_judgement_half_holds_the_recommendation(self):
        assert "recommendation" in CAMGeneratorAgent.JUDGEMENT_SECTIONS
        assert "five_cs" in CAMGeneratorAgent.JUDGEMENT_SECTIONS
        assert "recommendation" not in CAMGeneratorAgent.FACTUAL_SECTIONS


class TestPromptScoping:
    def test_each_pass_asks_only_for_its_own_sections(self):
        agent = _agent()
        factual = agent._build_prompt(
            sections=CAMGeneratorAgent.FACTUAL_SECTIONS).messages[0].prompt.template
        judgement = agent._build_prompt(
            sections=CAMGeneratorAgent.JUDGEMENT_SECTIONS).messages[0].prompt.template

        assert "five_cs" not in factual
        assert "recommendation" not in factual
        assert "five_cs" in judgement
        assert "document_control" not in judgement

    def test_the_five_cs_directive_follows_the_section(self):
        """Telling a pass to populate a section it was told to omit is a contradiction."""
        agent = _agent()
        factual = agent._build_prompt(
            sections=CAMGeneratorAgent.FACTUAL_SECTIONS).messages[0].prompt.template
        assert "THE FIVE Cs ARE ANALYSIS" not in factual

    def test_the_judgement_pass_receives_the_factual_pass(self):
        agent = _agent()
        j = agent._build_prompt(sections=CAMGeneratorAgent.JUDGEMENT_SECTIONS,
                                prior=FACTUAL).messages[0].prompt.template
        assert "Rs 38.42 Cr" in j, "conclusions must rest on the figures already stated"

    def test_each_pass_is_smaller_than_the_single_call(self):
        agent = _agent()
        full = len(agent._build_prompt().messages[0].prompt.template)
        factual = len(agent._build_prompt(
            sections=CAMGeneratorAgent.FACTUAL_SECTIONS).messages[0].prompt.template)
        assert factual < full


class TestBothPassesSucceed:
    def test_the_merged_document_carries_both_halves(self, monkeypatch):
        r = _Responder()
        out = _run(_agent(), r, monkeypatch)
        assert r.calls == ["factual", "judgement"]
        assert out["executive_summary"]["revenue"] == "Rs 38.42 Cr"
        assert len(out["five_cs"]) == 5
        assert out["decision"] == "APPROVE WITH CONDITIONS"

    def test_the_gate_lets_a_complete_cam_through(self, monkeypatch):
        from app.core.appraisal_safety import gate_cam_response

        out = _run(_agent(), _Responder(), monkeypatch)
        extraction = {"extraction_degraded": False, "total_revenue": 384200000,
                      "total_debt": 116000000, "shareholder_equity": 180600000}
        _, summary = gate_cam_response(extraction, out)
        assert summary["decision_allowed"] is True


class TestOnlyHalfSucceeds:
    """The case the split exists to survive."""

    def test_a_failed_judgement_pass_keeps_the_figures(self, monkeypatch):
        r = _Responder(truncate_on="judgement")
        out = _run(_agent(), r, monkeypatch)

        # The factual half survives, with its figures intact.
        assert out["executive_summary"]["revenue"] == "Rs 38.42 Cr"
        assert out["document_control"]["status"] != "ERROR"

    def test_a_failed_judgement_pass_withholds_the_recommendation(self, monkeypatch):
        out = _run(_agent(), _Responder(truncate_on="judgement"), monkeypatch)
        assert out["decision"] == "MANUAL REVIEW"
        assert out["recommended_loan_amount"] == "Withheld"
        assert "no recommendation has been made" in out["decision_rationale"]

    def test_a_failed_factual_pass_stops_there(self, monkeypatch):
        """With no figures there is nothing to interpret, so the second pass is not spent."""
        r = _Responder(truncate_on="factual")
        out = _run(_agent(), r, monkeypatch)
        assert r.calls == ["factual"], "the judgement pass must not be paid for"
        assert out["document_control"]["status"] == "ERROR"

    def test_a_half_written_cam_is_never_decision_ready(self, monkeypatch):
        from app.core.appraisal_safety import gate_cam_response

        out = _run(_agent(), _Responder(truncate_on="judgement"), monkeypatch)
        extraction = {"extraction_degraded": False, "total_revenue": 384200000}
        _, summary = gate_cam_response(extraction, out)
        assert summary["decision_allowed"] is False, (
            "a memorandum without its 5Cs or recommendation is not an appraisal"
        )
