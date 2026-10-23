"""Input validation. Every validator returns a normalised value or raises ``ValidationError``."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

from farmtrace.errors import ValidationError
from farmtrace.utils.helpers import NIGERIAN_STATES

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{20,100}$")
_LICENCE_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-/]{4,29}$")
_PLATE_RE = re.compile(r"^[A-Z0-9][A-Z0-9\- ]{3,19}$")

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


def require_text(value: str | None, field: str, *, max_length: int = 255, min_length: int = 1) -> str:
    text = (value or "").strip()
    if len(text) < min_length:
        if min_length <= 1:
            raise ValidationError(f"{field} is required.")
        raise ValidationError(f"{field} must be at least {min_length} characters.")
    if len(text) > max_length:
        raise ValidationError(f"{field} must be at most {max_length} characters.")
    return text


def optional_text(value: str | None, field: str, *, max_length: int = 1000) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > max_length:
        raise ValidationError(f"{field} must be at most {max_length} characters.")
    return text


def validate_email(value: str | None) -> str:
    email = (value or "").strip().lower()
    if not email:
        raise ValidationError("Email address is required.")
    if len(email) > 254 or not _EMAIL_RE.match(email) or ".." in email:
        raise ValidationError("Enter a valid email address, for example name@example.com.")
    return email


def validate_password(password: str | None, confirm: str | None = None) -> str:
    password = password or ""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValidationError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters long.")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValidationError(f"Password must be at most {PASSWORD_MAX_LENGTH} characters long.")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValidationError("Password must contain at least one letter and one number.")
    if confirm is not None and password != confirm:
        raise ValidationError("The two passwords do not match.")
    return password


def validate_nigerian_phone(value: str | None, *, required: bool = False) -> str | None:
    """Accepts ``08031234567``, ``+2348031234567`` or ``2348031234567``; returns ``+234...``."""
    raw = re.sub(r"[\s\-()]", "", value or "")
    if not raw:
        if required:
            raise ValidationError("Phone number is required.")
        return None
    if raw.startswith("+234"):
        local = raw[4:]
    elif raw.startswith("234"):
        local = raw[3:]
    elif raw.startswith("0"):
        local = raw[1:]
    else:
        raise ValidationError("Enter a Nigerian phone number such as 08031234567 or +2348031234567.")
    if not (local.isdigit() and len(local) == 10 and local[0] in "789"):
        raise ValidationError("Enter a Nigerian phone number such as 08031234567 or +2348031234567.")
    return "+234" + local


def validate_choice(value: str | None, choices: Iterable[str], field: str) -> str:
    choices = tuple(choices)
    if value not in choices:
        raise ValidationError(f"Select a valid {field.lower()}.")
    return value  # type: ignore[return-value]


def validate_state(value: str | None) -> str:
    return validate_choice(value, NIGERIAN_STATES, "State")


def _to_decimal(value, field: str) -> Decimal:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValidationError(f"{field} is required.")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError(f"{field} must be a number.") from None
    if not number.is_finite():
        raise ValidationError(f"{field} must be a number.")
    return number


def validate_positive_decimal(value, field: str, *, maximum: Decimal | int = 10_000_000) -> Decimal:
    number = _to_decimal(value, field)
    if number <= 0:
        raise ValidationError(f"{field} must be greater than zero.")
    if number > Decimal(maximum):
        raise ValidationError(f"{field} is unrealistically large (maximum {maximum:,}).")
    return number.quantize(Decimal("0.01"))


def validate_non_negative_decimal(value, field: str, *, maximum: Decimal | int = 1_000_000_000) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    number = _to_decimal(value, field)
    if number < 0:
        raise ValidationError(f"{field} cannot be negative.")
    if number > Decimal(maximum):
        raise ValidationError(f"{field} is unrealistically large.")
    return number.quantize(Decimal("0.01"))


def validate_date(value, field: str) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    raise ValidationError(f"{field} is required.")


def validate_date_not_future(value, field: str, today: dt.date) -> dt.date:
    day = validate_date(value, field)
    if day > today:
        raise ValidationError(f"{field} cannot be in the future.")
    if day < today - dt.timedelta(days=366):
        raise ValidationError(f"{field} cannot be more than a year ago.")
    return day


def validate_date_not_past(value, field: str, today: dt.date, *, max_days_ahead: int = 180) -> dt.date:
    day = validate_date(value, field)
    if day < today:
        raise ValidationError(f"{field} cannot be in the past.")
    if day > today + dt.timedelta(days=max_days_ahead):
        raise ValidationError(f"{field} must be within the next {max_days_ahead} days.")
    return day


def validate_licence_number(value: str | None) -> str:
    licence = re.sub(r"\s+", "", (value or "")).upper()
    if not _LICENCE_RE.match(licence):
        raise ValidationError("Enter a valid driver's licence number (5-30 letters, digits, '-' or '/').")
    return licence


def validate_vehicle_registration(value: str | None) -> str:
    plate = re.sub(r"\s+", " ", (value or "").strip()).upper()
    if not _PLATE_RE.match(plate):
        raise ValidationError("Enter a valid vehicle registration number, for example LSD-123-AB.")
    return plate


def validate_token_format(token: str | None) -> str:
    token = (token or "").strip()
    if not _TOKEN_RE.match(token):
        raise ValidationError("This verification code is not in a valid format.")
    return token
