"""Clearly-labelled demonstration data.

Demo data is **never** loaded automatically. It is created only by running
``python scripts/load_demo_data.py`` against a database you choose — ideally a
separate demo database, never the production one.

Every demo farm, batch, driver record and transport request has ``is_demo = TRUE``
and is shown with a "DEMO RECORD" badge; demo accounts use ``@example.com``
addresses (a domain reserved for documentation) so they cannot belong to anyone.
The workflow is driven through the normal services, so the same validation,
permission checks, tracking history and audit records apply.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy.orm import Session

from farmtrace import domain
from farmtrace.authentication import auth
from farmtrace.authentication.auth import DriverDetails, RegistrationData
from farmtrace.database import repository
from farmtrace.errors import ConflictError
from farmtrace.services import farmer_service, transport_service
from farmtrace.services.farmer_service import BatchInput, FarmInput
from farmtrace.utils.helpers import local_today

DEMO_FARMER_EMAIL = "demo-farmer@example.com"
DEMO_DRIVER_EMAIL = "demo-driver@example.com"
DEMO_CONSUMER_EMAIL = "demo-consumer@example.com"
DEMO_ADMIN_EMAIL = "demo-admin@example.com"
DEMO_EMAILS = (DEMO_FARMER_EMAIL, DEMO_DRIVER_EMAIL, DEMO_CONSUMER_EMAIL, DEMO_ADMIN_EMAIL)


@dataclass(frozen=True)
class DemoSummary:
    accounts: tuple[str, ...]
    batch_numbers: tuple[str, ...]
    request_numbers: tuple[str, ...]
    qr_tokens: tuple[str, ...]


def demo_data_present(session: Session) -> bool:
    return any(repository.get_user_by_email(session, email) is not None for email in DEMO_EMAILS)


def load_demo_data(session: Session, password: str) -> DemoSummary:
    """Create demo accounts and a small, realistic workflow. Caller commits."""
    if demo_data_present(session):
        raise ConflictError("Demo data has already been loaded into this database.")

    farmer = auth.register_user(session, RegistrationData(
        full_name="Demo Farmer (Adebayo)", email=DEMO_FARMER_EMAIL, password=password, confirm_password=password,
        role=domain.ROLE_FARMER, phone_number="08000000001",
    ))
    driver_user = auth.register_user(session, RegistrationData(
        full_name="Demo Driver (Musa)", email=DEMO_DRIVER_EMAIL, password=password, confirm_password=password,
        role=domain.ROLE_DRIVER, phone_number="08000000002",
        driver=DriverDetails("DEMO0000001", "DEMO-001-AA", "Mini truck", "3000"),
    ))
    auth.register_user(session, RegistrationData(
        full_name="Demo Consumer (Funke)", email=DEMO_CONSUMER_EMAIL, password=password, confirm_password=password,
        role=domain.ROLE_CONSUMER, phone_number=None,
    ))
    admin_user, _ = auth.bootstrap_administrator(
        session, email=DEMO_ADMIN_EMAIL, full_name="Demo Administrator", password=password,
    )
    admin = auth.to_current_user(admin_user)
    driver_record = repository.get_driver_by_user(session, driver_user.id)
    driver_record.is_demo = True

    farm = farmer_service.create_farm(session, farmer, FarmInput(
        farm_name="Demo Green Acres Farm", farm_location="Km 5 Ogbomoso-Ilorin Road", state="Oyo",
        local_government_area="Ogbomoso North", contact_information="Demo farm office",
    ), is_demo=True)

    today = local_today()

    def batch(produce: str, category: str, quantity: str, unit: str, days_ago: int, market: str, place: str):
        return farmer_service.create_batch(session, farmer, BatchInput(
            farm_id=farm.id, produce_name=produce, produce_category=category, quantity=quantity,
            unit_of_measurement=unit, harvest_date=today - dt.timedelta(days=days_ago),
            pickup_location="Demo Green Acres farm gate, Ogbomoso", destination_market=market,
            destination_location=place, additional_notes="Demonstration batch — not a real consignment.",
        ), is_demo=True)

    tomatoes = batch("Tomatoes", "Vegetables", "120", "crates", 3, "Mile 12 Market", "Mile 12, Kosofe, Lagos")
    yams = batch("Yam tubers", "Tubers & Roots", "400", "tubers", 2, "Bodija Market", "Bodija, Ibadan, Oyo")
    peppers = batch("Scotch bonnet peppers", "Spices & Peppers", "35", "baskets", 1, "Oyingbo Market",
                    "Oyingbo, Ebute Metta, Lagos")

    def request_for(produce_batch):
        return transport_service.create_request(
            session, farmer, batch_id=produce_batch.id, requested_pickup_date=today + dt.timedelta(days=1),
            notes="Demonstration request.", is_demo=True,
        )

    # 1) Completed delivery: Pending → Approved → Assigned → Dispatched → In-Transit → Delivered.
    delivered = request_for(tomatoes)
    transport_service.approve_request(session, admin, delivered.id, transport_cost="45000")
    transport_service.assign_driver(session, admin, delivered.id, driver_record.id)
    transport_service.advance_delivery(session, driver_user, delivered.id, domain.STATUS_DISPATCHED,
                                       location="Ogbomoso farm gate")
    transport_service.advance_delivery(session, driver_user, delivered.id, domain.STATUS_IN_TRANSIT,
                                       location="Oyo toll gate")
    transport_service.record_checkpoint(session, driver_user, delivered.id, location="Lagos-Ibadan Expressway")
    transport_service.advance_delivery(session, driver_user, delivered.id, domain.STATUS_DELIVERED,
                                       location="Mile 12 Market", notes="Received by market agent.")

    # 2) On the road with a reported delay (stage stays In-Transit).
    on_road = request_for(yams)
    transport_service.approve_request(session, admin, on_road.id, transport_cost="30000")
    transport_service.assign_driver(session, admin, on_road.id, driver_record.id)
    transport_service.advance_delivery(session, driver_user, on_road.id, domain.STATUS_DISPATCHED,
                                       location="Ogbomoso farm gate")
    transport_service.advance_delivery(session, driver_user, on_road.id, domain.STATUS_IN_TRANSIT,
                                       location="Oyo town")
    transport_service.report_delay(session, driver_user, on_road.id, "Heavy traffic near Ibadan toll gate.",
                                   location="Ibadan toll gate")

    # 3) Waiting for administrator review.
    pending = request_for(peppers)
    session.flush()

    return DemoSummary(
        accounts=DEMO_EMAILS,
        batch_numbers=(tomatoes.batch_number, yams.batch_number, peppers.batch_number),
        request_numbers=(delivered.request_number, on_road.request_number, pending.request_number),
        qr_tokens=(tomatoes.qr_token, yams.qr_token, peppers.qr_token),
    )
