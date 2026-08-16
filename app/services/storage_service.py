# =============================================================================
# CREDENT — Document Storage Service (ASE-52)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
"""
StorageService — Abstraction over Supabase Storage for encrypted document persistence.

Design Principles (Google SWE standards):
  1. Supabase Storage is the primary backend (AES-256 at-rest encryption, RLS-ready).
  2. Local filesystem is the fallback for development (no cloud credentials needed).
  3. All filenames are UUID-prefixed to prevent path traversal and collisions.
  4. The caller never handles raw file paths — only opaque `storage_path` handles.
  5. All errors are surfaced with structured logging for debuggability.
"""
import io
import os
import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# Supabase Storage bucket name — must exist in your Supabase project.
# Create it at: Supabase Dashboard → Storage → New Bucket → "credent-documents" (private)
STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "credent-documents")

# Local fallback directory — used when Supabase is not configured
LOCAL_STORAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "document_store")


def _get_supabase_client():
    """Lazily creates a Supabase client. Returns None if credentials are missing."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as e:
        logger.warning(f"[StorageService] Could not create Supabase client: {e}")
        return None


def _build_storage_path(tenant_id: str, filename: str) -> str:
    """
    Constructs a deterministic, collision-resistant object key.
    Format: {tenant_id}/{uuid4_hex}_{sanitized_filename}
    
    Using tenant_id as a path prefix prepares the storage structure
    for Supabase RLS policies in Week 8 RBAC work.
    """
    safe_filename = os.path.basename(filename).replace("..", "").replace("/", "").replace("\\", "")
    unique_prefix = uuid.uuid4().hex
    return f"{tenant_id}/{unique_prefix}_{safe_filename}"


def upload_document(file_bytes: bytes, original_filename: str, tenant_id: str = "default") -> str:
    """
    Upload a document to Supabase Storage (primary) or local filesystem (fallback).

    Args:
        file_bytes: Raw bytes of the uploaded file.
        original_filename: Original filename from the upload form.
        tenant_id: Tenant identifier used as the storage path prefix.

    Returns:
        storage_path: An opaque string handle for this document (use to download/delete).

    Raises:
        RuntimeError: If both Supabase and local storage fail.
    """
    storage_path = _build_storage_path(tenant_id, original_filename)
    
    # --- Attempt 1: Supabase Storage ---
    client = _get_supabase_client()
    if client:
        try:
            client.storage.from_(STORAGE_BUCKET).upload(
                path=storage_path,
                file=io.BytesIO(file_bytes),
                file_options={"content-type": _infer_content_type(original_filename)}
            )
            logger.info(f"[StorageService] ✓ Uploaded to Supabase: {storage_path}")
            return f"supabase://{storage_path}"
        except Exception as e:
            logger.warning(f"[StorageService] Supabase upload failed, falling back to local: {e}")

    # --- Attempt 2: Local Filesystem Fallback ---
    try:
        local_path = os.path.join(LOCAL_STORAGE_DIR, storage_path)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(file_bytes)
        logger.info(f"[StorageService] ✓ Saved to local storage: {local_path}")
        return f"local://{storage_path}"
    except Exception as e:
        logger.error(f"[StorageService] Both Supabase and local storage failed: {e}")
        raise RuntimeError(f"Document storage failed: {e}") from e


def download_document(storage_path_handle: str) -> bytes:
    """
    Download a document given its opaque storage_path handle.

    Args:
        storage_path_handle: The handle returned by upload_document().

    Returns:
        Raw bytes of the document.

    Raises:
        FileNotFoundError: If the document cannot be found.
        RuntimeError: If the download fails for an unexpected reason.
    """
    if storage_path_handle.startswith("supabase://"):
        path = storage_path_handle[len("supabase://"):]
        client = _get_supabase_client()
        if client:
            try:
                response = client.storage.from_(STORAGE_BUCKET).download(path)
                logger.info(f"[StorageService] ✓ Downloaded from Supabase: {path}")
                return response
            except Exception as e:
                logger.error(f"[StorageService] Supabase download failed: {e}")
                raise RuntimeError(f"Failed to download from Supabase: {e}") from e
        raise RuntimeError("Supabase client unavailable but handle points to Supabase storage.")

    elif storage_path_handle.startswith("local://"):
        path = storage_path_handle[len("local://"):]
        local_path = os.path.join(LOCAL_STORAGE_DIR, path)
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local document not found: {local_path}")
        with open(local_path, "rb") as f:
            return f.read()

    raise ValueError(f"Unknown storage handle scheme: {storage_path_handle}")


def delete_document(storage_path_handle: str) -> None:
    """
    Delete a document from storage. Best-effort — does not raise on failure.
    Used during cleanup of temporary upload files.
    """
    try:
        if storage_path_handle.startswith("supabase://"):
            path = storage_path_handle[len("supabase://"):]
            client = _get_supabase_client()
            if client:
                client.storage.from_(STORAGE_BUCKET).remove([path])
                logger.info(f"[StorageService] ✓ Deleted from Supabase: {path}")

        elif storage_path_handle.startswith("local://"):
            path = storage_path_handle[len("local://"):]
            local_path = os.path.join(LOCAL_STORAGE_DIR, path)
            if os.path.exists(local_path):
                os.remove(local_path)
                logger.info(f"[StorageService] ✓ Deleted from local storage: {local_path}")
    except Exception as e:
        logger.warning(f"[StorageService] Non-fatal: Failed to delete {storage_path_handle}: {e}")


def _infer_content_type(filename: str) -> str:
    """Maps common file extensions to MIME types for Supabase metadata."""
    ext = os.path.splitext(filename)[1].lower()
    return {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
    }.get(ext, "application/octet-stream")
