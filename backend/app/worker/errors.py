"""
Plain-language descriptions of job failures, stored as the job's ``error``
and shown to users.

The raw exception (type, message, traceback, API request ID) is written to
the worker log when the job fails; the stored message says what happened and
what to do, without internals.
"""

import anthropic

from app.extraction.document_text import DocumentTooLargeError, EmptyDocumentError
from app.extraction.llm import (
    ExtractionConfigurationError,
    ExtractionIncompleteError,
    ExtractionRefusedError,
)
from app.processing.processor import DocumentNotProcessableError

ADMIN = "Please contact your administrator."
AI_UNAVAILABLE = "Invoice extraction is unavailable right now"


def _api_status_message(error: anthropic.APIStatusError) -> str:
    text = str(error).lower()
    status = error.status_code
    if "credit balance" in text or "billing" in text:
        return (
            f"{AI_UNAVAILABLE}: the AI service account has run out of credits. "
            f"{ADMIN} Once credits are added, click Retry."
        )
    if status == 401:
        return (
            f"{AI_UNAVAILABLE}: the AI service rejected the API key. "
            f"{ADMIN} Once the key is fixed, click Retry."
        )
    if status == 403:
        return (
            f"{AI_UNAVAILABLE}: the AI service account doesn't have access to "
            f"the configured model. {ADMIN}"
        )
    if status == 404:
        return f"{AI_UNAVAILABLE}: the configured AI model wasn't found. {ADMIN}"
    if status == 413:
        return "This document is too large for the AI service to read."
    if status == 429:
        return "The AI service is busy right now (too many requests)."
    if status >= 500:
        return "The AI service is temporarily unavailable."
    return f"The AI service couldn't process this document. {ADMIN}"


def describe_failure(error: BaseException) -> str:
    """What went wrong, for the person looking at the document."""
    if isinstance(error, anthropic.APIStatusError):
        return _api_status_message(error)
    if isinstance(error, anthropic.APIConnectionError):  # includes timeouts
        return "Couldn't reach the AI service."
    if isinstance(error, ExtractionConfigurationError):
        return f"{AI_UNAVAILABLE}: the AI service isn't set up. {ADMIN}"
    if isinstance(error, anthropic.AnthropicError):
        return f"{AI_UNAVAILABLE}: the AI service is misconfigured. {ADMIN}"
    if isinstance(error, ExtractionRefusedError):
        return "The AI service declined to process this document."
    if isinstance(error, ExtractionIncompleteError):
        return (
            "The invoice details couldn't be read completely; the document may "
            "be too long or unusual."
        )
    if isinstance(error, DocumentTooLargeError):
        return "This document has too much text to extract invoice details from."
    if isinstance(error, EmptyDocumentError):
        return (
            "No readable text was found in this document, so there is nothing "
            "to extract. If it's a scan, try a clearer copy."
        )
    if isinstance(error, FileNotFoundError):
        return "The original file is missing from storage, so it can't be processed."
    if isinstance(error, DocumentNotProcessableError):
        text = str(error)
        if "disabled" in text:
            return f"Invoice extraction is disabled on this server. {ADMIN}"
        if "not found" in text:
            return "This document no longer exists."
        if "No extractor" in text:
            return "This file type can't be processed."
        return "This document can't be processed."
    return "Something went wrong while processing this document."


def failure_message(error: BaseException, *, will_retry: bool) -> str:
    """``describe_failure`` plus what happens next."""
    message = describe_failure(error)
    if will_retry:
        return f"{message} We'll try again automatically."
    if "Retry" in message:  # already says when to retry
        return message
    return f"{message} Click Retry to try again."
