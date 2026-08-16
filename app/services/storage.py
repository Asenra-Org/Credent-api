import os
import string
import tempfile
from abc import ABC, abstractmethod
from typing import Optional

# =============================================================================
# STORAGE EXCEPTIONS
# =============================================================================

class StorageValidationError(Exception):
    pass

class StorageConfigurationError(Exception):
    pass

class StorageProviderUnavailableError(Exception):
    pass

class StorageTimeoutError(Exception):
    pass

class StorageUploadError(Exception):
    pass

class StorageDownloadError(Exception):
    pass

class StorageDeleteError(Exception):
    pass

class StorageNotFoundError(Exception):
    pass

# =============================================================================
# STORAGE INTERFACE
# =============================================================================

class StorageService(ABC):
    """
    Application-level Storage Abstraction.
    Follows frozen Phase 4 architecture for provider-agnostic storage operations.
    """

    @abstractmethod
    def upload_file(self, tenant_id: str, case_id: str, document_id: str, filename: str, content: bytes, content_type: str) -> str:
        """
        Uploads a file and returns the canonical storage key.
        """
        pass

    @abstractmethod
    def download_file(self, tenant_id: str, storage_key: str) -> bytes:
        """
        Downloads a file securely checking tenant boundaries.
        """
        pass

    @abstractmethod
    def delete_file(self, tenant_id: str, storage_key: str) -> None:
        """
        Physically deletes a file securely checking tenant boundaries.
        """
        pass

    @abstractmethod
    def file_exists(self, tenant_id: str, storage_key: str) -> bool:
        """
        Checks physical existence of a file securely checking tenant boundaries.
        """
        pass

    @abstractmethod
    def create_signed_url(self, tenant_id: str, storage_key: str, expires_in: int = 3600) -> str:
        """
        Creates a short-lived signed URL for the document.
        """
        pass

    def build_storage_key(self, tenant_id: str, case_id: str, document_id: str, filename: str) -> str:
        """
        Constructs a tenant-safe canonical object key and validates path safety.
        Format: {tenant_id}/{case_id}/{document_id}/{filename}
        """
        self._validate_component(tenant_id, "tenant_id")
        self._validate_component(case_id, "case_id")
        self._validate_component(document_id, "document_id")
        clean_filename = self._sanitize_filename(filename)
        return f"{tenant_id}/{case_id}/{document_id}/{clean_filename}"

    def _validate_component(self, component: str, name: str) -> None:
        if not component or not component.strip():
            raise StorageValidationError(f"Path component '{name}' cannot be empty.")
        if any(c in component for c in ['/', '\\', '..']):
            raise StorageValidationError(f"Path component '{name}' contains illegal characters or traversal attempts.")

    def _sanitize_filename(self, filename: str) -> str:
        if not filename or not filename.strip():
            raise StorageValidationError("Filename cannot be empty.")
        if any(ord(c) < 32 for c in filename):
            raise StorageValidationError("Filename contains control characters.")
        if '..' in filename or '/' in filename or '\\' in filename:
            raise StorageValidationError("Filename contains path traversal sequences.")
        if len(filename) > 255:
            raise StorageValidationError("Filename exceeds maximum length of 255 characters.")
        
        allowed_chars = set(string.ascii_letters + string.digits + ".-_")
        if not all(c in allowed_chars for c in filename):
            raise StorageValidationError("Filename contains illegal or encoded characters. Only alphanumeric, dash, dot, and underscore are allowed.")
            
        return filename

    def _validate_tenant_access(self, tenant_id: str, storage_key: str) -> None:
        if not storage_key.startswith(f"{tenant_id}/"):
            raise StorageValidationError("Enforce defense-in-depth: Tenant must only access keys starting with their tenant_id/. Cross-tenant storage access is forbidden.")

# =============================================================================
# LOCAL STORAGE ADAPTER
# =============================================================================

class LocalStorageAdapter(StorageService):
    """
    Local file-based mock storage for testing.
    """
    
    def __init__(self):
        self.base_dir = os.path.join(tempfile.gettempdir(), 'credent_storage_mock')
        os.makedirs(self.base_dir, exist_ok=True)

    def upload_file(self, tenant_id: str, case_id: str, document_id: str, filename: str, content: bytes, content_type: str) -> str:
        storage_key = self.build_storage_key(tenant_id, case_id, document_id, filename)
        safe_path = os.path.join(self.base_dir, storage_key.replace('/', os.sep))
        
        os.makedirs(os.path.dirname(safe_path), exist_ok=True)
        with open(safe_path, 'wb') as f:
            f.write(content)
            
        return storage_key

    def download_file(self, tenant_id: str, storage_key: str) -> bytes:
        self._validate_tenant_access(tenant_id, storage_key)
        safe_path = os.path.join(self.base_dir, storage_key.replace('/', os.sep))
        
        if not os.path.exists(safe_path):
            raise StorageNotFoundError(f"Object not found: {storage_key}")
            
        with open(safe_path, 'rb') as f:
            return f.read()

    def delete_file(self, tenant_id: str, storage_key: str) -> None:
        self._validate_tenant_access(tenant_id, storage_key)
        safe_path = os.path.join(self.base_dir, storage_key.replace('/', os.sep))
        
        if os.path.exists(safe_path):
            os.remove(safe_path)

    def file_exists(self, tenant_id: str, storage_key: str) -> bool:
        self._validate_tenant_access(tenant_id, storage_key)
        safe_path = os.path.join(self.base_dir, storage_key.replace('/', os.sep))
        return os.path.exists(safe_path)

    def create_signed_url(self, tenant_id: str, storage_key: str, expires_in: int = 3600) -> str:
        self._validate_tenant_access(tenant_id, storage_key)
        return f"http://localhost:8000/mock-storage/{storage_key}"

# =============================================================================
# SUPABASE STORAGE ADAPTER
# =============================================================================

class SupabaseStorageAdapter(StorageService):
    """
    Supabase Storage Provider implementation.
    """
    def __init__(self):
        import os
        from supabase import create_client, Client
        
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            raise StorageConfigurationError("SUPABASE_URL and SUPABASE_KEY must be configured for SupabaseStorageAdapter.")
            
        try:
            self.client: Client = create_client(url, key)
        except Exception as e:
            raise StorageConfigurationError(f"Failed to initialize Supabase client: {str(e)}")
            
        self.bucket_name = os.environ.get("SUPABASE_STORAGE_BUCKET")
        if not self.bucket_name:
            raise StorageConfigurationError("Supabase storage bucket is not configured.")

    def upload_file(self, tenant_id: str, case_id: str, document_id: str, filename: str, content: bytes, content_type: str) -> str:
        if not content:
            raise StorageValidationError("Cannot upload empty file.")
            
        storage_key = self.build_storage_key(tenant_id, case_id, document_id, filename)
        
        try:
            res = self.client.storage.from_(self.bucket_name).upload(
                storage_key, 
                content,
                file_options={"content-type": content_type}
            )
            if hasattr(res, 'error') and res.error:
                raise StorageUploadError(f"Provider rejected upload: {res.error}")
        except Exception as e:
            err = str(e).lower()
            if 'timeout' in err:
                raise StorageTimeoutError("Storage provider timed out during upload.")
            if 'duplicate' in err or 'already exists' in err:
                raise StorageUploadError(f"Provider rejected upload due to duplicate key: {str(e)}")
            raise StorageProviderUnavailableError(f"Storage provider upload failed: {str(e)}")
            
        return storage_key

    def download_file(self, tenant_id: str, storage_key: str) -> bytes:
        self._validate_tenant_access(tenant_id, storage_key)
        try:
            res = self.client.storage.from_(self.bucket_name).download(storage_key)
            return res
        except Exception as e:
            err = str(e).lower()
            if 'not found' in err or 'not_found' in err or '404' in err:
                raise StorageNotFoundError(f"Object not found: {storage_key}")
            if 'timeout' in err:
                raise StorageTimeoutError("Storage provider timed out during download.")
            raise StorageDownloadError(f"Storage provider download failed: {str(e)}")

    def delete_file(self, tenant_id: str, storage_key: str) -> None:
        self._validate_tenant_access(tenant_id, storage_key)
        try:
            res = self.client.storage.from_(self.bucket_name).remove([storage_key])
            if hasattr(res, 'error') and res.error:
                raise StorageDeleteError(f"Provider rejected deletion: {res.error}")
        except Exception as e:
            err = str(e).lower()
            if 'timeout' in err:
                raise StorageTimeoutError("Storage provider timed out during delete.")
            raise StorageDeleteError(f"Storage provider delete failed: {str(e)}")

    def file_exists(self, tenant_id: str, storage_key: str) -> bool:
        self._validate_tenant_access(tenant_id, storage_key)
        try:
            parts = storage_key.split('/')
            name = parts[-1]
            path_prefix = '/'.join(parts[:-1])
            files = self.client.storage.from_(self.bucket_name).list(path_prefix)
            if isinstance(files, list):
                for f in files:
                    if f.get('name') == name:
                        return True
            return False
        except Exception as e:
            err = str(e).lower()
            if 'timeout' in err:
                raise StorageTimeoutError("Storage provider timed out during exist check.")
            raise StorageProviderUnavailableError(f"Failed to check file existence: {str(e)}")

    def create_signed_url(self, tenant_id: str, storage_key: str, expires_in: int = 3600) -> str:
        self._validate_tenant_access(tenant_id, storage_key)
        try:
            res = self.client.storage.from_(self.bucket_name).create_signed_url(storage_key, expires_in)
            if hasattr(res, 'error') and res.error:
                raise StorageProviderUnavailableError(f"Provider rejected signed URL: {res.error}")
            url = res.get('signedURL')
            if not url:
                raise StorageProviderUnavailableError("Provider returned empty signed URL.")
            return url
        except Exception as e:
            err = str(e).lower()
            if 'timeout' in err:
                raise StorageTimeoutError("Storage provider timed out creating signed URL.")
            if 'not found' in err or '404' in err:
                raise StorageNotFoundError(f"Object not found: {storage_key}")
            raise StorageProviderUnavailableError(f"Failed to create signed URL: {str(e)}")

# =============================================================================
# FACTORY
# =============================================================================

def get_storage_service() -> StorageService:
    import os
    if os.environ.get("ENVIRONMENT") == "production":
        return SupabaseStorageAdapter()
    return LocalStorageAdapter()
