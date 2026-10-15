"""
Security Utilities

HMAC verification for webhooks, field-level encryption for sensitive data,
and other security primitives.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from typing import Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# =============================================================================
# HMAC Verification (Meta Webhooks, Daraja Callbacks)
# =============================================================================


def verify_hmac_sha256(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verify HMAC-SHA256 signature.

    Used for:
    - Meta webhook verification (X-Hub-Signature-256)
    - Daraja callback verification

    Args:
        payload: Raw request body bytes
        signature: Signature header value (e.g., "sha256=abc123...")
        secret: Shared secret (app secret / consumer secret)

    Returns:
        True if signature matches, False otherwise
    """
    if not signature.startswith("sha256="):
        return False

    expected_sig = signature[7:]  # Remove "sha256=" prefix
    computed_sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    # Use constant-time comparison to prevent timing attacks
    return hmac.compare_digest(computed_sig, expected_sig)


def compute_hmac_sha256(payload: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature for outbound requests."""
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


# =============================================================================
# Field-Level Encryption (Fernet / AES-256-GCM)
# =============================================================================


class FieldEncryption:
    """
    Field-level encryption for sensitive data (tokens, passwords, PII).

    Uses Fernet (AES-128-CBC with HMAC-SHA256) for simplicity and safety.
    For higher security requirements, can be swapped to AES-256-GCM.

    Key derivation: PBKDF2 with 100,000 iterations from master key.
    """

    def __init__(self, master_key: str | bytes) -> None:
        if isinstance(master_key, str):
            master_key = master_key.encode("utf-8")
        self._fernet = Fernet(self._derive_key(master_key))

    def _derive_key(self, master_key: bytes) -> bytes:
        """Derive Fernet key from master key using PBKDF2."""
        # Use a fixed salt for deterministic derivation (in production, use key rotation)
        salt = b"socialerp-field-encryption-salt-v1"
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(master_key))
        return key

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string, return base64-encoded ciphertext."""
        if not plaintext:
            return ""
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a base64-encoded ciphertext, return plaintext."""
        if not ciphertext:
            return ""
        token = self._fernet.decrypt(ciphertext.encode("utf-8"))
        return token.decode("utf-8")

    def encrypt_bytes(self, plaintext: bytes) -> bytes:
        return self._fernet.encrypt(plaintext)

    def decrypt_bytes(self, ciphertext: bytes) -> bytes:
        return self._fernet.decrypt(ciphertext)


def generate_encryption_key() -> str:
    """Generate a new Fernet key for field encryption."""
    return Fernet.generate_key().decode("utf-8")


# =============================================================================
# Secure Token Generation
# =============================================================================


def generate_secure_token(length: int = 32) -> str:
    """Generate a cryptographically secure random token."""
    return secrets.token_urlsafe(length)


def generate_api_key(prefix: str = "sk") -> str:
    """Generate an API key with prefix."""
    return f"{prefix}_{generate_secure_token(24)}"


def generate_webhook_secret() -> str:
    """Generate a webhook signing secret."""
    return f"whsec_{generate_secure_token(32)}"


# =============================================================================
# Password Hashing (for owner/staff auth)
# =============================================================================


# Imported lazily to avoid bcrypt dependency in test environments without it


def get_password_hasher() -> Any:
    """Get password hasher (bcrypt)."""
    try:
        import bcrypt

        return bcrypt
    except ImportError as e:
        raise RuntimeError("bcrypt not installed. Install with: pip install bcrypt") from e


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    bcrypt = get_password_hasher()
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    if isinstance(hashed, bytes):
        return hashed.decode("utf-8")
    return str(hashed)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its bcrypt hash."""
    bcrypt = get_password_hasher()
    result = bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    return bool(result)


# =============================================================================
# Input Sanitization (Prompt Injection Defense)
# =============================================================================

INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"disregard\s+previous\s+instructions",
    r"forget\s+previous\s+instructions",
    r"system\s+prompt",
    r"you\s+are\s+an\s+ai",
    r"as\s+an\s+ai\s+model",
    r"pretend\s+to\s+be",
    r"roleplay\s+as",
    r"simulate\s+being",
    r"act\s+as\s+if",
    r"your\s+instructions\s+are",
    r"new\s+instructions",
    r"override\s+instructions",
    r"bypass\s+safety",
    r"ignore\s+safety",
    r"reveal\s+prompt",
    r"show\s+prompt",
    r"what\s+is\s+your\s+prompt",
    r"print\s+prompt",
]


def sanitize_customer_input(text: str) -> str:
    """
    Sanitize customer input to prevent prompt injection.

    This is a defense-in-depth measure. The primary defense is:
    - Customer text is NEVER used as instructions
    - Tools receive only validated, scoped parameters
    - System prompts are never exposed to the model

    This function strips obvious injection attempts and logs them.
    """
    if not text:
        return text

    sanitized = text
    for pattern in INJECTION_PATTERNS:
        # Case-insensitive replacement
        import re

        sanitized = re.sub(pattern, "[FILTERED]", sanitized, flags=re.IGNORECASE)

    return sanitized


def detect_injection_attempt(text: str) -> bool:
    """Detect if input contains potential prompt injection."""
    if not text:
        return False

    return any(re.search(pattern, text, re.IGNORECASE) for pattern in INJECTION_PATTERNS)


# =============================================================================
# PII Redaction for Logging
# =============================================================================

PHONE_PATTERN = re.compile(r"(?:\+?254|0)[17]\d{8}")
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
MPESA_RECEIPT_PATTERN = re.compile(r"[A-Z0-9]{10}")  # M-Pesa receipt format


def redact_pii(text: str) -> str:
    """Redact PII from log messages."""
    if not text:
        return text

    # Redact phone numbers
    text = PHONE_PATTERN.sub("[PHONE]", text)

    # Redact emails
    text = EMAIL_PATTERN.sub("[EMAIL]", text)

    # Redact M-Pesa receipts (approximate)
    text = MPESA_RECEIPT_PATTERN.sub("[RECEIPT]", text)

    return text
