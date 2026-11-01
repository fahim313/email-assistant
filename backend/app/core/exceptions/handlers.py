from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions.error import (
    AppError,
    AuthenticationError,
    ConflictError,
    ForbiddenError,
    LLMUnavailableError,
    NotFoundError,
    RateLimitError,
    ReconnectRequiredError,
)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def not_found_handler(
        _request: Request,
        exc: NotFoundError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ConflictError)
    async def conflict_handler(
        _request: Request,
        exc: ConflictError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc)},
        )

    @app.exception_handler(AuthenticationError)
    async def authentication_handler(
        _request: Request,
        exc: AuthenticationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"detail": str(exc) or "Authentication failed."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(ForbiddenError)
    async def forbidden_handler(
        _request: Request,
        exc: ForbiddenError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"detail": str(exc) or "Access forbidden."},
        )

    @app.exception_handler(RateLimitError)
    async def rate_limit_handler(
        _request: Request,
        exc: RateLimitError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Please slow down."},
            headers={"Retry-After": str(exc.retry_after)},
        )

    @app.exception_handler(ReconnectRequiredError)
    async def reconnect_required_handler(
        _request: Request,
        exc: ReconnectRequiredError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc) or "This Gmail account must be reconnected.",
                "code": "reconnect_required",
            },
        )

    @app.exception_handler(LLMUnavailableError)
    async def llm_unavailable_handler(
        _request: Request,
        _exc: LLMUnavailableError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "The assistant is unavailable. Try again later."},
        )

    @app.exception_handler(AppError)
    async def app_error_handler(
        _request: Request,
        exc: AppError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc) or "Application error."},
        )
