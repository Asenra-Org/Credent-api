# =============================================================================
# CREDENT — Concurrent Upload / WAL Mode Verification
# Linear: ASE-51 [QA-W6]
# =============================================================================
"""
Verifies the acceptance criteria: "Concurrent uploads complete without
SQLite lock errors" — building directly on ASE-46's WAL mode + busy_timeout
work.

This test genuinely spins up real OS threads writing to the real local
SQLite database concurrently, rather than mocking anything — the whole
point of this test is to catch real locking behavior, which a mock can't do.
"""

import threading
import time
import random
import pytest
from app.database.database import init_db, save_appraisal, get_recent_appraisals


@pytest.fixture(autouse=True)
def ensure_db_initialized():
    init_db()


class TestConcurrentUploads:

    def test_25_concurrent_writes_produce_zero_lock_errors(self):
        """
        Core acceptance criteria for ASE-51. Fires 25 real, concurrent
        threads writing to SQLite simultaneously and confirms WAL mode +
        busy_timeout (from ASE-46) actually prevents "database is locked"
        errors, rather than just trusting the PRAGMA was set correctly.
        """
        results = {"success": 0, "locked_errors": 0, "other_errors": []}
        lock = threading.Lock()

        def do_write(i):
            try:
                save_appraisal({
                    "company_id": f"CONCURRENT_TEST_{i}",
                    "company_name": f"Concurrent Test Co {i}",
                    "base_score": random.randint(0, 100),
                })
                with lock:
                    results["success"] += 1
            except Exception as e:
                with lock:
                    if "locked" in str(e).lower():
                        results["locked_errors"] += 1
                    else:
                        results["other_errors"].append(str(e))

        threads = [threading.Thread(target=do_write, args=(i,)) for i in range(25)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert results["locked_errors"] == 0, (
            f"{results['locked_errors']} SQLite 'database is locked' errors occurred "
            f"during concurrent writes — WAL mode/busy_timeout is not effectively "
            f"preventing lock contention."
        )
        assert results["other_errors"] == [], f"Unexpected errors: {results['other_errors']}"
        assert results["success"] == 25

    def test_concurrent_writes_all_persist_correctly(self):
        """Beyond 'no errors' — confirms all 25 concurrent writes actually
        landed in the database, not silently dropped."""
        unique_suffix = f"PERSIST_TEST_{int(time.time())}"

        def do_write(i):
            save_appraisal({
                "company_id": f"{unique_suffix}_{i}",
                "company_name": f"{unique_suffix} Co {i}",
                "base_score": 50,
            })

        threads = [threading.Thread(target=do_write, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        recent = get_recent_appraisals(limit=50)
        matching = [r for r in recent if unique_suffix in r.get("company_name", "")]
        assert len(matching) == 10, (
            f"Expected 10 persisted records, found {len(matching)} — "
            f"some concurrent writes were lost."
        )

    def test_wal_mode_and_busy_timeout_pragmas_are_actually_set(self):
        """Directly verifies the PRAGMA values themselves, not just behavior."""
        from app.database.database import get_sqlite_connection

        conn = get_sqlite_connection()
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        conn.close()

        assert journal_mode.lower() == "wal"
        assert busy_timeout >= 30000
