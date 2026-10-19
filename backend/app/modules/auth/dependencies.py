from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions.error import AuthenticationError
from app.core.security import decode_access_token
from app.db.session import SessionDep
from app.modules.auth.models import User

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    session: SessionDep,
) -> User:
    if credentials is None:
        raise AuthenticationError("Authentication required.")

    try:
        payload = decode_access_token(credentials.credentials)
        subject = payload.get("sub")

        if not isinstance(subject, str):
            raise AuthenticationError("Invalid access token.")

        user_id = UUID(subject)

    except (jwt.InvalidTokenError, ValueError):
        raise AuthenticationError("Invalid or expired access token.") from None

    user = await session.get(User, user_id)

    if user is None or not user.is_active:
        raise AuthenticationError("Invalid or inactive account.")

    return user