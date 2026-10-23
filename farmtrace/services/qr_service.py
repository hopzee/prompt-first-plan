"""QR-code generation and public produce verification.

* Each batch gets an unpredictable token from ``secrets.token_urlsafe(32)``
  (256 bits of randomness) — never a database id.
* The QR image encodes ``{PUBLIC_APP_URL}/?verify={token}``. If PUBLIC_APP_URL
  is not configured, no QR code is produced (so nobody prints a dead link).
* Public verification returns a :class:`PublicBatchView`, built from an explicit
  allow-list of fields. It never contains names, emails, phone numbers, licence
  numbers, internal ids or free-text notes.
"""

from __future__ import annotations

import datetime as dt
import io
import secrets
from dataclasses import dataclass, field
from urllib.parse import urlencode

import qrcode
from PIL import Image, ImageDraw, ImageFont
from qrcode.constants import ERROR_CORRECT_M
from sqlalchemy.orm import Session

from farmtrace import domain
from farmtrace.authentication.permissions import CurrentUser, ensure_owner_or_admin, require_admin
from farmtrace.config.settings import normalize_public_url
from farmtrace.database import repository
from farmtrace.database.models import ProduceBatch, RevokedQrToken
from farmtrace.errors import ConfigurationError, NotFoundError, ValidationError
from farmtrace.services.audit_service import record_audit
from farmtrace.utils.helpers import format_quantity, utcnow
from farmtrace.utils.validators import require_text, validate_token_format

TOKEN_BYTES = 32

#: The only batch information that may ever appear on the public page.
PUBLIC_FIELDS = (
    "batch_number",
    "produce_name",
    "produce_category",
    "quantity",
    "harvest_date",
    "origin_farm",
    "origin_state",
    "origin_lga",
    "destination_market",
    "batch_status",
    "delivery_status",
    "is_delayed",
    "dispatch_time",
    "delivery_time",
    "is_demo",
    "events",
)


def generate_qr_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def build_verification_url(base_url: str | None, token: str) -> str:
    base = normalize_public_url(base_url)
    if not base:
        raise ConfigurationError(
            "QR codes are disabled until PUBLIC_APP_URL is set to the address where this app is reachable "
            "(for example https://your-app.streamlit.app)."
        )
    return f"{base}/?{urlencode({'verify': token})}"


def build_qr(url: str) -> qrcode.QRCode:
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    return qr


def qr_png_bytes(url: str) -> bytes:
    image = build_qr(url).make_image(fill_color="black", back_color="white").convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - Pillow < 10.1
        return ImageFont.load_default()


def label_png_bytes(url: str, batch: ProduceBatch) -> bytes:
    """Printable packaging label: QR code plus batch reference and produce name."""
    qr_image = build_qr(url).make_image(fill_color="black", back_color="white").convert("RGB")
    qr_image = qr_image.resize((520, 520), Image.NEAREST)
    width, height = 600, 780
    label = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(label)
    draw.rectangle([(8, 8), (width - 9, height - 9)], outline=(46, 125, 50), width=6)
    draw.text((width // 2, 48), "FarmTrace · Scan to verify", fill=(46, 125, 50), font=_font(30), anchor="mm")
    label.paste(qr_image, ((width - 520) // 2, 80))
    draw.text((width // 2, 640), batch.batch_number, fill="black", font=_font(36), anchor="mm")
    produce = f"{batch.produce_name} · {format_quantity(batch.quantity, batch.unit_of_measurement)}"
    draw.text((width // 2, 690), produce[:40], fill=(60, 60, 60), font=_font(26), anchor="mm")
    draw.text((width // 2, 735), f"Harvested {batch.harvest_date:%d %b %Y}", fill=(90, 90, 90), font=_font(22), anchor="mm")
    buffer = io.BytesIO()
    label.save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass(frozen=True)
class QrBundle:
    url: str
    png: bytes
    label_png: bytes


def get_batch_qr(session: Session, actor: CurrentUser | None, batch_id: int, base_url: str | None) -> QrBundle:
    batch = repository.get_batch(session, int(batch_id))
    if batch is None:
        raise NotFoundError("Produce batch not found.")
    ensure_owner_or_admin(actor, batch.farmer_user_id, "produce batch")
    if batch.qr_revoked_at is not None:
        raise ValidationError("This batch's QR code has been revoked. An administrator can reissue a new one.")
    url = build_verification_url(base_url, batch.qr_token)
    return QrBundle(url=url, png=qr_png_bytes(url), label_png=label_png_bytes(url, batch))


# ---------------------------------------------------------------- verification
@dataclass(frozen=True)
class PublicEvent:
    label: str
    status: str | None
    timestamp: dt.datetime
    location: str | None
    location_note: str | None


@dataclass(frozen=True)
class PublicBatchView:
    batch_number: str
    produce_name: str
    produce_category: str
    quantity: str
    harvest_date: dt.date
    origin_farm: str
    origin_state: str
    origin_lga: str
    destination_market: str
    batch_status: str
    delivery_status: str | None
    is_delayed: bool
    dispatch_time: dt.datetime | None
    delivery_time: dt.datetime | None
    is_demo: bool
    events: tuple[PublicEvent, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class VerificationResult:
    status: str  # "verified" | "revoked" | "unknown" | "invalid"
    message: str
    view: PublicBatchView | None = None
    notice: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "verified"


def _public_view(session: Session, batch: ProduceBatch) -> tuple[PublicBatchView, str | None]:
    latest = repository.get_latest_request_for_batch(session, batch.id)
    events = tuple(
        PublicEvent(
            label=domain.EVENT_LABELS.get(event.event_type, event.event_type.replace("_", " ").title()),
            status=event.new_status,
            timestamp=event.event_timestamp,
            location=event.recorded_location,
            location_note=domain.LOCATION_SOURCE_LABEL if event.recorded_location else None,
        )
        for event in repository.history_for_batch(session, batch.id)
        if event.event_type != domain.EVENT_DETAILS_UPDATED
    )
    view = PublicBatchView(
        batch_number=batch.batch_number,
        produce_name=batch.produce_name,
        produce_category=batch.produce_category,
        quantity=format_quantity(batch.quantity, batch.unit_of_measurement),
        harvest_date=batch.harvest_date,
        origin_farm=batch.farm.farm_name,
        origin_state=batch.farm.state,
        origin_lga=batch.farm.local_government_area,
        destination_market=batch.destination_market,
        batch_status=batch.batch_status,
        delivery_status=latest.status if latest else None,
        is_delayed=bool(latest and latest.is_delayed and latest.status in domain.ACTIVE_STATUSES),
        dispatch_time=latest.actual_dispatch_time if latest else None,
        delivery_time=latest.actual_delivery_time if latest else None,
        is_demo=batch.is_demo,
        events=events,
    )
    notice = None
    if batch.batch_status == domain.BATCH_CANCELLED:
        notice = "This produce batch was withdrawn and is not in circulation. Do not treat it as delivered."
    elif latest and latest.status == domain.STATUS_CANCELLED:
        notice = "The most recent transport for this batch was cancelled before delivery."
    elif view.is_delayed:
        notice = "This delivery is currently reported as delayed."
    elif latest is None:
        notice = "This batch is registered but transport has not been requested yet."
    return view, notice


def verify_token(session: Session, token: str | None) -> VerificationResult:
    """Public (no login) lookup of a QR token against real database records."""
    try:
        token = validate_token_format(token)
    except ValidationError:
        return VerificationResult("invalid", "This code is not a valid FarmTrace verification code.")
    batch = repository.get_batch_by_token(session, token)
    if batch is None:
        if repository.get_revoked_token(session, token) is not None:
            return VerificationResult(
                "revoked", "This QR code has been withdrawn and replaced. Ask the seller for the current label."
            )
        return VerificationResult("unknown", "No produce batch matches this code. It may be counterfeit or mistyped.")
    if batch.qr_revoked_at is not None:
        return VerificationResult(
            "revoked", "This QR code has been revoked by FarmTrace administrators and can no longer be trusted."
        )
    view, notice = _public_view(session, batch)
    return VerificationResult("verified", "Genuine FarmTrace batch record found.", view, notice)


# --------------------------------------------------------- admin token management
def revoke_qr(session: Session, actor: CurrentUser | None, batch_id: int, reason: str) -> ProduceBatch:
    actor = require_admin(actor)
    batch = repository.get_batch(session, int(batch_id))
    if batch is None:
        raise NotFoundError("Produce batch not found.")
    if batch.qr_revoked_at is not None:
        raise ValidationError("This QR code is already revoked.")
    reason = require_text(reason, "Reason", max_length=255, min_length=5)
    batch.qr_revoked_at = utcnow()
    session.flush()
    record_audit(
        session, actor_user_id=actor.id, action="qr_revoked", target_type="produce_batch", target_id=batch.id,
        details={"batch_number": batch.batch_number, "reason": reason},
    )
    return batch


def reissue_qr(session: Session, actor: CurrentUser | None, batch_id: int, reason: str) -> ProduceBatch:
    """Replace the batch token; the old token is kept so scans can explain it was replaced."""
    actor = require_admin(actor)
    batch = repository.get_batch(session, int(batch_id))
    if batch is None:
        raise NotFoundError("Produce batch not found.")
    reason = require_text(reason, "Reason", max_length=255, min_length=5)
    session.add(
        RevokedQrToken(produce_batch_id=batch.id, token=batch.qr_token, reason=reason, revoked_by_user_id=actor.id)
    )
    batch.qr_token = generate_qr_token()
    batch.qr_revoked_at = None
    session.flush()
    record_audit(
        session, actor_user_id=actor.id, action="qr_reissued", target_type="produce_batch", target_id=batch.id,
        details={"batch_number": batch.batch_number, "reason": reason},
    )
    return batch
