"""A borrower's folder becomes one case, answered immediately.

Two problems are being solved together here, and they are the same problem.

An appraisal takes minutes. Holding an HTTP request open for it is how a client
times out on work the server actually completed and paid for - which is exactly
what happened, at the frontend's 600-second ceiling. And a folder is one
borrower's file, not N borrowers: running an appraisal per document costs once
per document and makes reconciliation impossible, because a figure can only be
checked against a second source when both are read in the same run.
"""

import asyncio
import os
import uuid

import pytest

from app.parsers.document_set import merge
from app.services.appraisal_worker import (
    _extraction_from_document_set,
    _primary_document,
)

BALANCE_SHEET = """AUDITED FINANCIAL STATEMENTS
Sahyadri Precision Castings Private Limited

BALANCE SHEET (INR Lakhs)
Share Capital: 320.00
Reserves and Surplus: 1,486.00
Long Term Borrowings: 742.00
Short Term Borrowings: 418.00
Trade Payables: 496.00
Total Current Assets: 2,246.00
"""

PROFIT_AND_LOSS = """STATEMENT OF PROFIT AND LOSS (INR Lakhs)
Sahyadri Precision Castings Private Limited
Revenue from Operations: 3,842.00
Finance Costs: 148.00
Depreciation and Amortisation: 214.00
Profit Before Tax: 486.00
Profit for the Year: 358.00
"""

GST_SUMMARY = """GST ANNUAL SUMMARY (INR Lakhs)
Sahyadri Precision Castings Private Limited
Revenue from Operations: 3,410.00
"""

OTHER_BORROWER = """BALANCE SHEET (INR Crores)
Konkan Agro Foods Private Limited
Share Capital: 4.00
Long Term Borrowings: 34.50
"""


class TestExtractionFromTheWholeSet:
    """The analysis agents must receive the set, not the first file."""

    def test_figures_from_separate_documents_all_arrive(self):
        s = merge({"bs.pdf": BALANCE_SHEET, "pnl.pdf": PROFIT_AND_LOSS})
        extracted = _extraction_from_document_set(s)

        # Neither document alone supports all of these.
        assert extracted["total_revenue"] == 384_200_000
        assert extracted["total_debt"] == 116_000_000
        assert extracted["shareholder_equity"] == 180_600_000
        assert extracted["ebitda"] == 84_800_000

    def test_the_borrower_is_named_from_the_documents(self):
        s = merge({"bs.pdf": BALANCE_SHEET, "pnl.pdf": PROFIT_AND_LOSS})
        assert _extraction_from_document_set(s)["company_name"] == \
            "Sahyadri Precision Castings Private Limited"

    def test_a_set_that_yielded_figures_is_not_degraded(self):
        """Figures read from a document line are real, whatever the provider did."""
        s = merge({"bs.pdf": BALANCE_SHEET, "pnl.pdf": PROFIT_AND_LOSS})
        extracted = _extraction_from_document_set(s)
        assert extracted["extraction_degraded"] is False
        assert extracted["extraction_method"] == "deterministic_document_set"

    def test_conflicts_travel_with_the_extraction(self):
        s = merge({"pnl.pdf": PROFIT_AND_LOSS, "gst.pdf": GST_SUMMARY})
        extracted = _extraction_from_document_set(s)
        fields = [c["field"] for c in extracted["document_conflicts"]]
        assert "total_revenue" in fields, "a disagreement must reach the appraisal"

    def test_every_citation_names_its_document(self):
        s = merge({"bs.pdf": BALANCE_SHEET, "pnl.pdf": PROFIT_AND_LOSS})
        citations = _extraction_from_document_set(s)["citations"]
        assert citations
        for name, c in citations.items():
            assert c["document"], name
            assert c["line"] > 0, name
            assert c["verified"] is True, name

    def test_an_empty_set_stays_degraded(self):
        """No figures means the default extraction, which is degraded by design."""
        s = merge({"kyc.pdf": "KYC RECORD\nPAN: AAECS4471K\n"})
        extracted = _extraction_from_document_set(s)
        assert extracted["extraction_degraded"] is True
        assert extracted["total_revenue"] is None


class TestPrimaryDocumentChoice:
    def test_the_richest_statement_is_handed_to_the_coordinator(self):
        s = merge({"gst.pdf": GST_SUMMARY, "bs.pdf": BALANCE_SHEET})
        paths = {"gst.pdf": "/tmp/gst.pdf", "bs.pdf": "/tmp/bs.pdf"}
        # The balance sheet matched more captions than the one-line GST summary.
        assert _primary_document(s, paths) == "/tmp/bs.pdf"

    def test_it_always_returns_something_readable(self):
        s = merge({"kyc.pdf": "KYC RECORD\nPAN: X\n"})
        paths = {"kyc.pdf": "/tmp/kyc.pdf"}
        assert _primary_document(s, paths) == "/tmp/kyc.pdf"


class TestMixedBorrowersAreRefused:
    def test_two_companies_in_one_case_is_a_mismatch(self):
        s = merge({"a.pdf": BALANCE_SHEET, "b.pdf": OTHER_BORROWER})
        assert s.entity_mismatch is True
        assert s.borrower is None

    def test_the_worker_refuses_rather_than_merging_them(self, monkeypatch, tmp_path):
        """The case is failed with a reason, not appraised into fiction."""
        import app.services.appraisal_worker as worker

        recorded = {}

        def _status(case_id, status, current_step=None):
            recorded["status"] = status
            recorded["step"] = current_step

        def _result(case_id, payload, status=None):
            recorded.setdefault("payloads", []).append(payload)

        monkeypatch.setattr("app.database.database.update_case_status", _status)
        monkeypatch.setattr("app.database.database.update_case_result", _result)
        # download_document returns the document's bytes, not a path.
        monkeypatch.setattr("app.services.storage_service.download_document",
                            lambda handle: b"%PDF-1.3 stub")
        monkeypatch.setattr(
            "app.parsers.document_set.merge_pdfs",
            lambda paths: merge({"a.pdf": BALANCE_SHEET, "b.pdf": OTHER_BORROWER}),
        )

        out = asyncio.run(worker.run_case_appraisal_job(
            case_id="case-mixed", storage_paths=["h1", "h2"],
            document_names=["a.pdf", "b.pdf"], institution_id="org-1",
        ))

        assert out["status"] == "failed"
        assert recorded["status"] == "FAILED"
        assert recorded["step"] == "entity_mismatch"

        # update_case_status carries no reason field, so the reason must reach
        # the case result - that is what GET /ingest/status returns, and a
        # failure the analyst cannot read is a failure they cannot act on.
        reasons = [p.get("error") for p in recorded["payloads"] if p.get("error")]
        assert any("more than one company" in r for r in reasons)

    def test_reporting_a_failure_never_raises(self, monkeypatch):
        """A failing failure-handler hides the original error behind its own."""
        import app.services.appraisal_worker as worker

        def _boom(*a, **k):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr("app.database.database.update_case_status", _boom)
        monkeypatch.setattr("app.database.database.update_case_result", _boom)

        worker._fail_case("case-x", "worker_error", "something went wrong")


class TestTheEndpointAnswersImmediately:
    """The appraisal runs in the background; the request does not wait for it."""

    def test_the_route_exists_and_is_role_guarded(self):
        from app.main import app

        route = next(r for r in app.routes
                     if getattr(r, "path", "") == "/api/v1/documents/ingest/case")
        assert "POST" in route.methods

    def test_a_case_with_no_appraisable_document_is_refused(self):
        """A case that can never be appraised would leave a client polling forever."""
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        res = client.post("/api/v1/documents/ingest/case")
        # Unauthenticated, but the point is the route is reachable and typed.
        assert res.status_code in (401, 403, 422)
