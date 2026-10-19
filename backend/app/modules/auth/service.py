from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions.error import (
    AuthenticationError,
    ConflictError,
    ForbiddenError,
)
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)


async def register_user(
    session: AsyncSession,
    data: RegisterRequest,
) -> TokenResponse:
    email = str(data.email).lower()

    result = await session.execute(
        select(User).where(User.email == email)
    )
    existing_user = result.scalar_one_or_none()

    if existing_user:
        raise ConflictError("user", email)

    user = User(
        name=data.name.strip(),
        email=email,
        password_hash=hash_password(data.password),
    )

    session.add(user)

    try:
        await session.commit()
        await session.refresh(user)
    except Exception:
        await session.rollback()
        raise

    access_token = create_access_token(str(user.id))

    return TokenResponse(
        access_token=access_token,
        user=UserResponse(
            id=str(user.id),
            name=user.name,
            email=user.email,
            is_active=user.is_active,
        ),
    )


async def login_user(
    session: AsyncSession,
    data: LoginRequest,
) -> TokenResponse:
    email = str(data.email).lower()

    result = await session.execute(
        select(User).where(User.email == email)
    )
    user = result.scalar_one_or_none()

    if (
        user is None
        or user.password_hash is None
        or not verify_password(data.password, user.password_hash)
    ):
        raise AuthenticationError("Invalid email or password.")

    if not user.is_active:
        raise ForbiddenError("This account is inactive.")

    access_token = create_access_token(str(user.id))

    return TokenResponse(
        access_token=access_token,
        user=UserResponse(
            id=str(user.id),
            name=user.name,
            email=user.email,
            is_active=user.is_active,
        ),
    )