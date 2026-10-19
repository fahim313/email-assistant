class AppError(Exception):
    """Base exception for application errors."""


class NotFoundError(AppError):
    """Raised when a resource is not found."""

    def __init__(self, resource: str, key: str) -> None:
        self.resource: str = resource
        self.key: str = key
        super().__init__(f"'{resource}' not found with key '{key}'")


class ConflictError(AppError):
    """Raised when a resource conflict occurs."""

    def __init__(self, resource: str, key: str) -> None:
        self.resource: str = resource
        self.key: str = key
        super().__init__(f"Conflict with '{resource}' and key '{key}'")


class AuthenticationError(AppError):
    """Raised when authentication fails."""


class ForbiddenError(AppError):
    """Raised when access is forbidden."""


class LLMUnavailableError(AppError):
    """Raised when the LLM provider is unavailable."""