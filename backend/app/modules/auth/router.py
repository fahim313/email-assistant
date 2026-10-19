from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.db.session import SessionDep
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.modules.auth.service import login_user, register_user

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    data: RegisterRequest,
    session: SessionDep,
) -> TokenResponse:
    return await register_user(session, data)


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest,
    session: SessionDep,
) -> TokenResponse:
    return await login_user(session, data)


@router.get("/me", response_model=UserResponse)
async def read_current_user(
    user: Annotated[User, Depends(get_current_user)],
) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        name=user.name,
        email=user.email,
        is_active=user.is_active,
    )