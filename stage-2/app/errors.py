"""The API's error envelope (§5).

Every 4xx and 5xx response carries `{"error": {"code": ..., "message": ...}}`.
"""
from __future__ import annotations


class ApiError(Exception):
    """A request that ends in the §5 envelope: a status and one of the specified codes."""

    def __init__(self, status: int, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message or code

    def body(self) -> dict:
        """The response body for this failure."""
        return {"error": {"code": self.code, "message": self.message}}


def malformed(message: str = "the request body is not valid JSON of the expected shape") -> ApiError:
    """400 `malformed_request`: an unparseable body, or a field of the wrong JSON type."""
    return ApiError(400, "malformed_request", message)


def missing_key() -> ApiError:
    """400 `missing_idempotency_key`: the required header is absent or empty."""
    return ApiError(400, "missing_idempotency_key", "an Idempotency-Key header is required")


def unauthenticated() -> ApiError:
    """401 `unauthenticated`."""
    return ApiError(401, "unauthenticated", "a valid bearer token is required")


def not_found(message: str = "no such resource") -> ApiError:
    """404 `not_found`."""
    return ApiError(404, "not_found", message)


def key_reuse() -> ApiError:
    """409 `idempotency_key_reuse`: this key was used with a different request body."""
    return ApiError(409, "idempotency_key_reuse",
                    "this Idempotency-Key was used with a different request")


def email_taken() -> ApiError:
    """409 `email_taken`."""
    return ApiError(409, "email_taken", "that email is already registered")


def validation(message: str = "the request is not valid") -> ApiError:
    """422 `validation_failed`."""
    return ApiError(422, "validation_failed", message)
