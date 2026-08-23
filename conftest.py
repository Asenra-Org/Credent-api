# conftest.py — pytest configuration for Credent API test suite.
# Adds the project root to sys.path so that `from app.agents...` imports
# resolve correctly regardless of which directory pytest is invoked from.
import sys
import os

# Insert the Credent-api root (parent of this file) into sys.path.
sys.path.insert(0, os.path.dirname(__file__))

import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """[P1-3] Clear rate-limit counters between tests.

    The limiter is process-global by design, and the suite drives many logins
    from a single TestClient address. Without a reset, one test module
    exhausts the auth tier and every later test sees 429/401 instead of the
    behaviour it is asserting. Resetting keeps the limits genuinely active
    (they are still enforced within each test) without leaking state across
    tests.
    """
    from app.core.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()
