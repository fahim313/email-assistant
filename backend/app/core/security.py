import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.fernet import Fernet
from pwdlib import PasswordHash

from app.core.config import get_settings

ACCESS_TOKEN_TYPE = "access"

password_hash = PasswordHash.recommended()

_DUMMY_HASH = password_hash.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)


def verify_dummy_password(password: str) -> None:
    _ = password_hash.verify(password, _DUMMY_HASH)


def create_access_token(subject: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.SESSION_EXPIRE_MINUTES),
    }
    return jwt.encode(
        payload,
        settings.SECRET_KEY.get_secret_value(),
        algorithm="HS256",
    )


def decode_access_token(token: str) -> dict[str, object]:
    settings = get_settings()
    payload: dict[str, object] = jwt.decode(
        token,
        settings.SECRET_KEY.get_secret_value(),
        algorithms=["HS256"],
        options={"require": ["exp", "sub"]},
    )
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise jwt.InvalidTokenError("Not an access token.")
    return payload


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _get_token_cipher() -> Fernet:
    settings = get_settings()
    key = settings.TOKEN_ENCRYPTION_KEY.get_secret_value()

    if not key:
        raise ValueError("TOKEN_ENCRYPTION_KEY is not configured")

    return Fernet(key.encode())


def encrypt_token(token: str) -> str:
    return _get_token_cipher().encrypt(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    return _get_token_cipher().decrypt(
        encrypted_token.encode()
    ).decode()