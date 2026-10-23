"""Formatting, time-zone and reference-number helpers."""

from __future__ import annotations

import datetime as dt
import secrets
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Africa/Lagos"

NIGERIAN_STATES = (
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue", "Borno",
    "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu", "Federal Capital Territory",
    "Gombe", "Imo", "Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Kogi", "Kwara", "Lagos",
    "Nasarawa", "Niger", "Ogun", "Ondo", "Osun", "Oyo", "Plateau", "Rivers", "Sokoto",
    "Taraba", "Yobe", "Zamfara",
)

PRODUCE_CATEGORIES = (
    "Vegetables",
    "Fruits",
    "Tubers & Roots",
    "Grains & Cereals",
    "Legumes",
    "Spices & Peppers",
    "Other",
)

UNITS_OF_MEASUREMENT = ("kg", "tonnes", "crates", "baskets", "bags", "tubers", "pieces")

VEHICLE_TYPES = (
    "Pickup truck",
    "Mini truck",
    "Lorry (10-20 tonnes)",
    "Trailer",
    "Refrigerated truck",
    "Van",
    "Cargo tricycle",
)

# Unambiguous characters for human-readable references (no 0/O, 1/I).
_REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def utcnow() -> dt.datetime:
    """Timezone-aware current UTC time. All timestamps are stored in UTC."""
    return dt.datetime.now(dt.timezone.utc)


def ensure_utc(value: dt.datetime | None) -> dt.datetime | None:
    """Return ``value`` as an aware UTC datetime (naive values are assumed to be UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def to_local(value: dt.datetime | None, tz_name: str = DEFAULT_TIMEZONE) -> dt.datetime | None:
    value = ensure_utc(value)
    return value.astimezone(ZoneInfo(tz_name)) if value else None


def local_today(tz_name: str = DEFAULT_TIMEZONE) -> dt.date:
    return utcnow().astimezone(ZoneInfo(tz_name)).date()


def local_datetime_to_utc(day: dt.date, time: dt.time, tz_name: str = DEFAULT_TIMEZONE) -> dt.datetime:
    """Combine a local date and time chosen in the interface into an aware UTC datetime."""
    local = dt.datetime.combine(day, time).replace(tzinfo=ZoneInfo(tz_name))
    return local.astimezone(dt.timezone.utc)


def format_datetime(value: dt.datetime | None, tz_name: str = DEFAULT_TIMEZONE) -> str:
    local = to_local(value, tz_name)
    return local.strftime("%d %b %Y, %I:%M %p") if local else "—"


def format_date(value: dt.date | dt.datetime | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, dt.datetime):
        value = value.date()
    return value.strftime("%d %b %Y")


def format_quantity(quantity: Decimal | float | int | None, unit: str | None) -> str:
    if quantity is None:
        return "—"
    number = Decimal(str(quantity))
    text = f"{number:,.2f}".rstrip("0").rstrip(".")
    return f"{text} {unit or ''}".strip()


def format_naira(amount: Decimal | float | int | None) -> str:
    if amount is None:
        return "—"
    try:
        return f"₦{Decimal(str(amount)):,.2f}"
    except (InvalidOperation, ValueError):
        return "—"


def generate_reference(prefix: str, today: dt.date | None = None, length: int = 6) -> str:
    """Human-friendly unique-looking reference such as ``FT-B-261023-K7Q2MX``.

    Uniqueness is guaranteed by a database unique constraint; callers retry on collision.
    """
    today = today or local_today()
    suffix = "".join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(length))
    return f"{prefix}-{today:%y%m%d}-{suffix}"


def mask_identifier(value: str | None, visible: int = 4) -> str:
    """Mask private identifiers (licence numbers) for list views: ``*****1234``."""
    if not value:
        return "—"
    if len(value) <= visible:
        return "*" * len(value)
    return "*" * (len(value) - visible) + value[-visible:]
