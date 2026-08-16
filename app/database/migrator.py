import os
import sqlite3
from pathlib import Path
from app.models.base import Base
from app.database.session import get_engine

def run_ase_schema_migration(db_url: str = None):
    """
    Idempotently executes Phase 7 schema creation.
    For production PostgreSQL, this would use advisory locks.
    For local SQLite, it uses SQLAlchemy metadata safely.
    """
    engine = get_engine()
    
    # Safely create Phase 7 tables without dropping Phase 5/6 tables
    # create_all() is inherently safe (IF NOT EXISTS)
    Base.metadata.create_all(bind=engine)
    print("Schema migration executed idempotently.")
