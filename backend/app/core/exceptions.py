"""
Application exceptions mapped to HTTP error responses.
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


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


async def _app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers that turn AppError subclasses into JSON responses."""
    app.add_exception_handler(AppError, _app_error_handler)
