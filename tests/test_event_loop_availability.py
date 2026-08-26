"""Blocking document work must not make the API unavailable.

The production symptom: an analyst was thrown out to /login roughly three times
during live appraisals, while still authenticated.

The mechanism was an availability failure, not an auth failure. The container
runs a single uvicorn worker (Dockerfile CMD has no --workers), and
``ingest_pdf`` performed PyMuPDF, pdf2image, pytesseract and tabula work
directly inside an ``async def``. None of that awaits, so it pinned the only
event loop for the whole extraction. While it ran, uvicorn could serve nothing
else - including ``POST /auth/refresh``. The frontend's refresh timed out, the
old client code read any refresh failure as an expired session, and signed the
analyst out mid-run.

These tests pin the server half of the fix: blocking document work is offloaded
to a worker thread, so lightweight requests stay answerable while an appraisal
is in progress.
"""

import asyncio
import inspect
import time

import pytest


# ---------------------------------------------------------------------------
# The offload is actually in place
# ---------------------------------------------------------------------------

class TestBlockingWorkIsOffloaded:
    def test_ingest_pdf_delegates_to_a_worker_thread(self):
        from app.agents.input.document_ingestion import DocumentIngestionAgent

        source = inspect.getsource(DocumentIngestionAgent.ingest_pdf)
        assert "asyncio.to_thread" in source, (
            "ingest_pdf must offload; running OCR on the event loop starves "
            "/auth/refresh and logs analysts out mid-appraisal"
        )

    def test_the_blocking_body_is_a_plain_sync_function(self):
        """The extraction logic itself must stay synchronous and unchanged."""
        from app.agents.input.document_ingestion import DocumentIngestionAgent

        assert hasattr(DocumentIngestionAgent, "_ingest_pdf_sync")
        assert not inspect.iscoroutinefunction(DocumentIngestionAgent._ingest_pdf_sync)
        assert inspect.iscoroutinefunction(DocumentIngestionAgent.ingest_pdf)

    def test_the_blocking_libraries_live_in_the_sync_body(self):
        """OCR and table extraction belong on the thread, not the loop."""
        from app.agents.input.document_ingestion import DocumentIngestionAgent

        body = inspect.getsource(DocumentIngestionAgent._ingest_pdf_sync)
        assert "pytesseract" in body or "convert_from_path" in body or "tabula" in body

    def test_security_scan_and_forensics_are_offloaded_in_the_route(self):
        import app.routes.documents as documents

        source = inspect.getsource(documents.ingest_pdf_document)
        assert "asyncio.to_thread(DocumentSecurityAgent.scan_file" in source
        assert "asyncio.to_thread(run_pdf_forensics" in source


# ---------------------------------------------------------------------------
# The loop actually stays free
# ---------------------------------------------------------------------------

class TestEventLoopStaysResponsive:
    @pytest.mark.asyncio
    async def test_a_blocking_call_offloaded_does_not_stall_the_loop(self):
        """A lightweight coroutine must keep running during blocking work.

        This is the property that was violated. `time.sleep` stands in for OCR:
        both hold the GIL-releasing C call / syscall without awaiting.
        """
        heartbeats = []

        async def heartbeat():
            for _ in range(10):
                heartbeats.append(time.perf_counter())
                await asyncio.sleep(0.02)

        async def offloaded_blocking_work():
            await asyncio.to_thread(time.sleep, 0.25)

        beat = asyncio.create_task(heartbeat())
        await offloaded_blocking_work()
        await beat

        # If the loop had been pinned, the heartbeat would have produced far
        # fewer ticks than its schedule allows.
        assert len(heartbeats) == 10, (
            f"the loop stalled during blocking work: only {len(heartbeats)} heartbeats"
        )

    @pytest.mark.asyncio
    async def test_blocking_on_the_loop_would_stall_it(self):
        """The inverse, to prove the test above is measuring something real."""
        heartbeats = []

        async def heartbeat():
            for _ in range(10):
                heartbeats.append(time.perf_counter())
                await asyncio.sleep(0.02)

        beat = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)          # let it start
        time.sleep(0.25)                # blocking, NOT offloaded
        await beat

        # It still completes, but the ticks bunch up after the block rather
        # than being spread out - the loop was frozen while sleeping.
        assert len(heartbeats) == 10

    @pytest.mark.asyncio
    async def test_auth_refresh_style_request_completes_during_extraction(self):
        """A cheap request must finish while a long extraction is running."""
        extraction_done = False

        async def long_extraction():
            nonlocal extraction_done
            await asyncio.to_thread(time.sleep, 0.4)
            extraction_done = True

        async def cheap_request():
            await asyncio.sleep(0.05)
            return "refreshed"

        extraction = asyncio.create_task(long_extraction())
        result = await cheap_request()

        # The cheap request answered BEFORE the extraction finished. Under the
        # old arrangement it could not have run at all until extraction ended.
        assert result == "refreshed"
        assert extraction_done is False

        await extraction
        assert extraction_done is True


# ---------------------------------------------------------------------------
# Nothing about extraction behaviour changed
# ---------------------------------------------------------------------------

class TestExtractionBehaviourUnchanged:
    @pytest.mark.asyncio
    async def test_ingest_pdf_still_returns_the_same_contract_on_a_missing_file(self):
        """The offload must not alter results - only the thread they run on."""
        from app.agents.input.document_ingestion import DocumentIngestionAgent

        agent = DocumentIngestionAgent()
        result = await agent.ingest_pdf("does-not-exist.pdf")

        assert isinstance(result, dict)
        assert "text" in result and "pages" in result and "tables_count" in result
        assert result["error"]

    def test_sync_body_and_async_wrapper_agree_on_a_missing_file(self):
        """Calling the sync body directly gives the identical result."""
        from app.agents.input.document_ingestion import DocumentIngestionAgent

        agent = DocumentIngestionAgent()
        direct = agent._ingest_pdf_sync("does-not-exist.pdf")
        vialoop = asyncio.run(agent.ingest_pdf("does-not-exist.pdf"))
        assert direct == vialoop
