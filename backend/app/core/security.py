from datetime import UTC, datetime, timedelta

import jwt
from cryptography.fernet import Fernet
from pwdlib import PasswordHash

from app.core.config import get_settings

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)


def create_access_token(subject: str) -> str:
    settings = get_settings()
    payload = {
        "sub": subject,
        "exp": datetime.now(UTC)
        + timedelta(minutes=settings.SESSION_EXPIRE_MINUTES),
    }
    return jwt.encode(
        payload,
        settings.SECRET_KEY.get_secret_value(),
        algorithm="HS256",
    )


def decode_access_token(token: str) -> dict[str, object]:
    settings = get_settings()
    return jwt.decode(
        token,
        settings.SECRET_KEY.get_secret_value(),
        algorithms=["HS256"],
    )


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