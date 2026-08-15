import os
from contextlib import contextmanager
from typing import Generator, Any
from sqlalchemy import create_engine, Engine, event
from sqlalchemy.orm import sessionmaker, Session

_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker | None = None

def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        db_url = os.getenv("DATABASE_URL")
        
        if db_url and not db_url.startswith("sqlite"):
            # PostgreSQL connection
            _ENGINE = create_engine(
                db_url,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True
            )
        else:
            # SQLite fallback connection
            sqlite_url = db_url if db_url else f"sqlite:///{os.path.abspath(os.path.join(os.path.dirname(__file__), 'credent.db'))}"
            _ENGINE = create_engine(
                sqlite_url,
                connect_args={"check_same_thread": False, "timeout": 30},
                echo=False
            )
            
            @event.listens_for(_ENGINE, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

    return _ENGINE

def get_session_factory() -> sessionmaker:
    global _SESSION_FACTORY
    if _SESSION_FACTORY is None:
        _SESSION_FACTORY = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False
        )
    return _SESSION_FACTORY

@contextmanager
def get_db_session(provided_session = None) -> Generator[Any, None, None]:
    """
    Returns a managed database session.
    Yields the provided_session directly if passed, otherwise creates a new one.
    Commits on success, rolls back on exception.
    """
    if provided_session:
        yield provided_session
        return

    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
