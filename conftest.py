# conftest.py — pytest configuration for Credent API test suite.
# Adds the project root to sys.path so that `from app.agents...` imports
# resolve correctly regardless of which directory pytest is invoked from.
import sys
import os

# Insert the Credent-api root (parent of this file) into sys.path.
sys.path.insert(0, os.path.dirname(__file__))
