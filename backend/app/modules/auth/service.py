import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions.error import (
    AuthenticationError,
    ConflictError,
    ForbiddenError,
)
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_dummy_password,
    verify_password,
)
from app.modules.auth.google import GoogleIdentity
from app.modules.auth.models import RefreshToken, User
from app.modules.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        name=user.name,
        email=user.email,
        is_active=user.is_active,
    )



async def _issue_session(
    session: AsyncSession,
    user: User,
    family_id: uuid.UUID | None = None,
) -> TokenResponse:
    settings = get_settings()
    raw_refresh = generate_refresh_token()

    session.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id or uuid.uuid4(),
            token_hash=hash_refresh_token(raw_refresh),
            expires_at=datetime.now(UTC)
            + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        )
    )
    await session.commit()

    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=raw_refresh,
        expires_in=settings.SESSION_EXPIRE_MINUTES * 60,
        user=_user_response(user),
    )


async def _revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    _ = await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )


async def _revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    _ = await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )


async def refresh_session(
    session: AsyncSession,
    raw_refresh_token: str,
) -> TokenResponse:
    """Rotate a refresh token: the old one dies, a new pair is issued."""
    settings = get_settings()
    now = datetime.now(UTC)

    row = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(raw_refresh_token)
        )
    )
    if row is None:
        raise AuthenticationError("Invalid refresh token.")

    if row.revoked_at is not None:
       
        grace = timedelta(seconds=settings.REFRESH_REUSE_GRACE_SECONDS)
        if now - row.revoked_at > grace:
            await _revoke_family(session, row.family_id)
            await session.commit()
        raise AuthenticationError("Invalid refresh token.")

    if row.expires_at <= now:
        raise AuthenticationError("Refresh token expired. Please log in again.")

    # Atomic compare-and-set so two concurrent refreshes can't both win.
    claimed = await session.scalar(
        update(RefreshToken)
        .where(RefreshToken.id == row.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
        .returning(RefreshToken.id)
    )
    if claimed is None:
        await session.rollback()
        raise AuthenticationError("Invalid refresh token.")

    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        await _revoke_family(session, row.family_id)
        await session.commit()
        raise AuthenticationError("Invalid or inactive account.")

    return await _issue_session(session, user, family_id=row.family_id)


async def logout(session: AsyncSession, raw_refresh_token: str) -> None:
    """Revoke the whole session family. Idempotent: unknown token is fine."""
    row = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(raw_refresh_token)
        )
    )
    if row is None:
        return
    await _revoke_family(session, row.family_id)
    await session.commit()


async def purge_expired_tokens(session: AsyncSession) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=7)
    result = await session.execute(
        delete(RefreshToken).where(RefreshToken.expires_at < cutoff)
    )
    await session.commit()
    return result.rowcount if isinstance(result, CursorResult) else 0


async def register_user(
    session: AsyncSession,
    data: RegisterRequest,
) -> TokenResponse:
    email = str(data.email).lower()

    existing = await session.scalar(select(User).where(User.email == email))
    if existing:
        raise ConflictError("user", email)

    user = User(
        name=data.name.strip(),
        email=email,
        password_hash=hash_password(data.password),
    )
    session.add(user)

    try:
        await session.commit()
    except IntegrityError:
        # Lost a race with a concurrent sign-up for the same email.
        await session.rollback()
        raise ConflictError("user", email) from None
    except Exception:
        await session.rollback()
        raise

    await session.refresh(user)
    return await _issue_session(session, user)


async def login_user(
    session: AsyncSession,
    data: LoginRequest,
) -> TokenResponse:
    email = str(data.email).lower()

    user = await session.scalar(select(User).where(User.email == email))

    if user is None or user.password_hash is None:
        verify_dummy_password(data.password)
        raise AuthenticationError("Invalid email or password.")

    if not verify_password(data.password, user.password_hash):
        raise AuthenticationError("Invalid email or password.")

    if not user.is_active:
        raise ForbiddenError("This account is inactive.")

    return await _issue_session(session, user)


async def login_with_google(
    session: AsyncSession,
    identity: GoogleIdentity,
) -> TokenResponse:
    """Find, link, or create the user for a verified Google identity."""
    user = await session.scalar(
        select(User).where(User.google_sub == identity.sub)
    )

    if user is None:
        user = await session.scalar(
            select(User).where(User.email == identity.email)
        )

        if user is None:
            user = User(
                name=identity.name,
                email=identity.email,
                google_sub=identity.sub,
                email_verified=True,
            )
            session.add(user)
        else:
            if user.google_sub is not None and user.google_sub != identity.sub:
                raise ConflictError("user", identity.email)

            
            if user.password_hash is not None and not user.email_verified:
                user.password_hash = None
                await _revoke_all_for_user(session, user.id)

            user.google_sub = identity.sub
            user.email_verified = True

        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise ConflictError("user", identity.email) from None

        await session.refresh(user)

    if not user.is_active:
        raise ForbiddenError("This account is inactive.")

    return await _issue_session(session, user)
