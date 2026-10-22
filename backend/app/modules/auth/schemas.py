from typing import ClassVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class GoogleIdTokenRequest(BaseModel):
    """For frontends that use Google Identity Services directly."""

    id_token: str = Field(min_length=1, max_length=4096)


class GoogleLoginUrlResponse(BaseModel):
    authorization_url: str


class UserResponse(BaseModel):
    id: str
    name: str
    email: EmailStr
    is_active: bool

    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token lifetime, seconds
    user: UserResponse
