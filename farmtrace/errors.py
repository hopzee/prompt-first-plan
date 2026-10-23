"""Application exception hierarchy.

Every exception carries a ``user_message`` that is safe to show in the interface.
Technical details (SQL, stack traces, connection strings) are only written to logs.
"""

from __future__ import annotations


class FarmTraceError(Exception):
    """Base class for expected, user-presentable application errors."""

    default_message = "The operation could not be completed."

    def __init__(self, message: str | None = None):
        self.user_message = message or self.default_message
        super().__init__(self.user_message)


class ValidationError(FarmTraceError):
    default_message = "Some of the information provided is not valid."


class AuthenticationError(FarmTraceError):
    default_message = "Invalid email or password."


class PermissionDenied(FarmTraceError):
    default_message = "You do not have permission to perform this action."


class NotFoundError(FarmTraceError):
    default_message = "The requested record was not found."


class ConflictError(FarmTraceError):
    default_message = "This record was changed by someone else. Refresh the page and try again."


class InvalidTransitionError(ConflictError):
    default_message = "That status change is not allowed for this delivery."


class ConfigurationError(FarmTraceError):
    default_message = "The application is not configured correctly."


class DatabaseUnavailableError(FarmTraceError):
    default_message = (
        "The database is currently unavailable. Please try again shortly or contact the administrator."
    )


class AppendOnlyViolation(FarmTraceError):
    default_message = "Tracking history and audit records cannot be edited or deleted."
