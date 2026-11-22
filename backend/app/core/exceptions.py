"""
Application exceptions mapped to HTTP error responses.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)

# Shown to end users: plain language, no internals. The technical cause is
# logged by the handlers below.
DATABASE_UNAVAILABLE = (
    "The service is temporarily unavailable. Please try again in a moment."
)
INTERNAL_ERROR = "Something went wrong on our side. Please try again."


class AppError(Exception):
    """Base error carrying an HTTP status code and a client-safe message."""

    status_code: int = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    """The requested resource does not exist."""

    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    """The request conflicts with the resource's current state."""

    status_code = status.HTTP_409_CONFLICT


class ServiceUnavailableError(AppError):
    """A feature is disabled or its backing service is unavailable."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class DatabaseUnavailableError(ServiceUnavailableError):
    """PostgreSQL cannot be reached (down, starting up or refusing logins)."""

    retry_after_seconds = 5

    def __init__(self, message: str = DATABASE_UNAVAILABLE) -> None:
        super().__init__(message)


class UnsupportedFileTypeError(AppError):
    """The uploaded file is not an accepted type."""

    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


class EmptyFileError(AppError):
    """The uploaded file has no content."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class CorruptedFileError(AppError):
    """The uploaded file is the right type but damaged or unreadable."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class FileTooLargeError(AppError):
    """The uploaded file exceeds the size limit."""

    status_code = status.HTTP_413_CONTENT_TOO_LARGE


class TooManyFilesError(AppError):
    """The request contains more files than allowed."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    headers = None
    if isinstance(exc, DatabaseUnavailableError):
        logger.warning(
            "Database unavailable on %s %s: %s",
            request.method,
            request.url.path,
            exc.__cause__ or exc,
        )
        headers = {"Retry-After": str(exc.retry_after_seconds)}
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.message}, headers=headers
    )


async def _database_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """A connection lost mid-request (e.g. PostgreSQL restarted) is a 503;
    any other database error is a bug, reported as a generic 500."""
    assert isinstance(exc, DBAPIError)
    if exc.connection_invalidated:
        unavailable = DatabaseUnavailableError()
        unavailable.__cause__ = exc
        return await _app_error_handler(request, unavailable)
    return await _unhandled_error_handler(request, exc)


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: a JSON body (not Starlette's plain text) so clients can
    show a message. Details stay in the server log."""
    logger.error(
        "Unhandled error on %s %s", request.method, request.url.path, exc_info=exc
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": INTERNAL_ERROR},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers that turn errors into JSON ``{"detail": ...}`` responses."""
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(DBAPIError, _database_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
