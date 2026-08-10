# =============================================================================
# CREDENT — Document Security Validation & Sandboxing Agent (ASE-55)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
import os
import re
from dataclasses import dataclass, field
from typing import List, Tuple
from pypdf import PdfReader


@dataclass
class SecurityScanResult:
    is_safe: bool
    flags: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class DocumentSecurityAgent:
    """
    Mandatory first gate in the ingestion pipeline.
    Validates file integrity and sanitises prompt injection patterns before LLM processing.
    """

    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
    MAX_PAGES = 500
    
    # Hardcoded v1 injection blocklist. TODO: move to a config file in v2.
    INJECTION_PATTERNS = [
        re.compile(r"ignore\s+(?:all\s+)?(?:previous\s+)?instructions?", re.IGNORECASE),
        re.compile(r"disregard\s+(?:all\s+)?(?:prior\s+)?(?:prompts?|instructions?)", re.IGNORECASE),
        re.compile(r"forget\s+(?:your\s+)?(?:role|instructions)", re.IGNORECASE),
        re.compile(r"you\s+are\s+now\s+(?:a|an)\s+", re.IGNORECASE),
        re.compile(r"jailbreak", re.IGNORECASE),
        re.compile(r"\{\s*\"role\"\s*:\s*\"system\"\s*", re.IGNORECASE),
        re.compile(r"system\s+override", re.IGNORECASE)
    ]

    @classmethod
    def scan_file(cls, file_path: str) -> SecurityScanResult:
        """
        Validates file metadata, magic bytes, and potential PDF bomb conditions.
        Returns a strict fail (is_safe=False) if basic structural integrity is violated.
        """
        if not os.path.exists(file_path):
            return SecurityScanResult(is_safe=False, flags=["FILE_NOT_FOUND"])

        # 1. Size check
        if os.path.getsize(file_path) > cls.MAX_FILE_SIZE:
            return SecurityScanResult(is_safe=False, flags=["FILE_TOO_LARGE"])
            
        # 2. Magic byte check for PDF
        try:
            with open(file_path, "rb") as f:
                header = f.read(5)
                if header != b"%PDF-":
                    return SecurityScanResult(is_safe=False, flags=["INVALID_MAGIC_BYTES"])
        except Exception:
            return SecurityScanResult(is_safe=False, flags=["FILE_READ_ERROR"])

        # 3. PDF Bomb check (Page Count)
        try:
            reader = PdfReader(file_path)
            num_pages = len(reader.pages)
            if num_pages > cls.MAX_PAGES:
                return SecurityScanResult(is_safe=False, flags=["PDF_BOMB_SUSPECTED_PAGES"])
        except Exception as e:
            # We don't fail outright if PyPDF2 fails to read, as it might be OCR-able,
            # but we flag it. The actual text extraction failure is handled later.
            return SecurityScanResult(is_safe=True, warnings=[f"PYPDF2_PARSE_WARNING: {str(e)}"])
            
        return SecurityScanResult(is_safe=True)

    @classmethod
    def sanitize_text(cls, text: str) -> Tuple[str, List[str]]:
        """
        Scans for prompt injection patterns and strips them.
        Returns the sanitized text and a list of warnings (if any).
        """
        warnings = []
        sanitized_text = text

        for pattern in cls.INJECTION_PATTERNS:
            if pattern.search(sanitized_text):
                warnings.append(f"INJECTION_PATTERN_DETECTED: {pattern.pattern}")
                # Redact the pattern to neutralize the injection attempt
                sanitized_text = pattern.sub("[REDACTED_SECURITY_POLICY_VIOLATION]", sanitized_text)
                
        # Entropy check: basic heuristic for high density of non-word chars (potential encoded payloads)
        if len(sanitized_text) > 100:
            alnum_count = sum(c.isalnum() for c in sanitized_text)
            entropy_ratio = alnum_count / len(sanitized_text)
            if entropy_ratio < 0.2:  # Less than 20% alphanumeric
                warnings.append("HIGH_ENTROPY_DETECTED_POTENTIAL_PAYLOAD")

        return sanitized_text, warnings
