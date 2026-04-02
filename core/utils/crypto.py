"""
Field-level encryption using Fernet (AES-128-CBC) derived from Django SECRET_KEY.
Used to encrypt sensitive values like SMTP passwords stored in the database.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _get_fernet():
    """Derive a Fernet key from Django's SECRET_KEY."""
    key_bytes = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)


def encrypt_value(plaintext):
    """Encrypt a string value. Returns base64 encoded ciphertext."""
    if not plaintext:
        return ''
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext):
    """Decrypt a base64 encoded ciphertext. Returns plaintext string."""
    if not ciphertext:
        return ''
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception):
        return ''
