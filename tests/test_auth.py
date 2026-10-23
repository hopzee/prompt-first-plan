"""Authentication, registration and authorisation tests."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from farmtrace import domain
from farmtrace.authentication import auth
from farmtrace.authentication.auth import RegistrationData
from farmtrace.database import repository
from farmtrace.database.models import AuditLog, UserCredential, UserProfile, UserRole
from farmtrace.errors import AuthenticationError, PermissionDenied, ValidationError
from farmtrace.services import admin_service, farmer_service
from farmtrace.services.farmer_service import FarmInput

from tests.conftest import PASSWORD, register


def test_password_is_hashed_with_argon2id(session, farmer):
    credential = session.get(UserCredential, farmer.id)
    assert credential.password_hash.startswith("$argon2id$")
    assert PASSWORD not in credential.password_hash
    assert auth.verify_password(credential.password_hash, PASSWORD)
    assert not auth.verify_password(credential.password_hash, "WrongPass1")


def test_credentials_profile_and_role_are_stored_separately(session, farmer):
    assert session.get(UserProfile, farmer.id).full_name == "Adebayo Ogunleye"
    roles = session.execute(select(UserRole.role).where(UserRole.user_id == farmer.id)).scalars().all()
    assert roles == [domain.ROLE_FARMER]
    assert session.get(UserCredential, farmer.id) is not None


def test_valid_login_succeeds_and_returns_roles(session, farmer):
    user = auth.authenticate(session, farmer.email.upper(), PASSWORD)
    assert user.id == farmer.id
    assert user.roles == frozenset({domain.ROLE_FARMER})


def test_invalid_login_is_rejected_with_generic_message(session, farmer):
    with pytest.raises(AuthenticationError) as wrong_password:
        auth.authenticate(session, farmer.email, "NotThePassword1")
    with pytest.raises(AuthenticationError) as unknown_email:
        auth.authenticate(session, "nobody@example.com", PASSWORD)
    assert wrong_password.value.user_message == unknown_email.value.user_message == auth.GENERIC_LOGIN_ERROR


def test_public_registration_cannot_create_administrator(session):
    with pytest.raises(PermissionDenied):
        auth.register_user(
            session,
            RegistrationData(full_name="Sneaky User", email="sneaky@example.com", password=PASSWORD,
                             confirm_password=PASSWORD, role=domain.ROLE_ADMIN),
        )
    assert repository.get_user_by_email(session, "sneaky@example.com") is None


def test_duplicate_email_is_rejected(session, farmer):
    with pytest.raises(ValidationError):
        register(session, domain.ROLE_CONSUMER, email=farmer.email)


def test_weak_password_and_bad_email_are_rejected(session):
    with pytest.raises(ValidationError):
        auth.register_user(session, RegistrationData("Valid Name", "valid@example.com", "short", "short", "consumer"))
    with pytest.raises(ValidationError):
        auth.register_user(session, RegistrationData("Valid Name", "not-an-email", PASSWORD, PASSWORD, "consumer"))


def test_driver_registration_requires_vehicle_details(session):
    with pytest.raises(ValidationError):
        register(session, domain.ROLE_DRIVER, driver=False)


def test_account_locks_after_repeated_failures(session, farmer):
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        with pytest.raises(AuthenticationError):
            auth.authenticate(session, farmer.email, "WrongPass99")
    with pytest.raises(AuthenticationError) as locked:
        auth.authenticate(session, farmer.email, PASSWORD)
    assert "Too many" in locked.value.user_message
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=auth.LOCKOUT_MINUTES + 1)
    assert auth.authenticate(session, farmer.email, PASSWORD, now=later).id == farmer.id


def test_deactivated_user_cannot_sign_in_or_keep_session(session, admin, farmer):
    admin_service.set_user_active(session, admin, farmer.id, False, "Left the cooperative")
    session.commit()
    with pytest.raises(AuthenticationError):
        auth.authenticate(session, farmer.email, PASSWORD)
    assert auth.load_current_user(session, farmer.id) is None


def test_unauthorized_service_calls_are_rejected(session, consumer, farmer):
    farm_input = FarmInput("Consumer Farm", "Somewhere road", "Lagos", "Ikeja")
    with pytest.raises(PermissionDenied):
        farmer_service.create_farm(session, consumer, farm_input)
    with pytest.raises(PermissionDenied):
        farmer_service.create_farm(session, None, farm_input)
    with pytest.raises(PermissionDenied):
        admin_service.list_users(session, farmer)


def test_farmer_cannot_read_another_farmers_farm(session, farmer, other_farmer, farm):
    with pytest.raises(PermissionDenied):
        farmer_service.get_farm(session, other_farmer, farm.id)


def test_admin_role_management_rules_and_audit(session, admin, farmer):
    admin_service.grant_role(session, admin, farmer.id, domain.ROLE_DRIVER)
    session.commit()
    assert domain.ROLE_DRIVER in auth.load_current_user(session, farmer.id).roles
    with pytest.raises(ValidationError):
        admin_service.revoke_role(session, admin, admin.id, domain.ROLE_ADMIN)  # own / last admin
    actions = session.execute(select(AuditLog.action)).scalars().all()
    assert "role_granted" in actions


def test_change_password_requires_current_password(session, farmer):
    with pytest.raises(ValidationError):
        auth.change_password(session, farmer, "WrongCurrent1", "NewHarvest2027", "NewHarvest2027")
    auth.change_password(session, farmer, PASSWORD, "NewHarvest2027", "NewHarvest2027")
    session.commit()
    assert auth.authenticate(session, farmer.email, "NewHarvest2027").id == farmer.id


def test_bootstrap_creates_admin_and_promotes_existing(session, farmer):
    user, created = auth.bootstrap_administrator(
        session, email="root@example.com", full_name="Root Admin", password=PASSWORD
    )
    assert created and domain.ROLE_ADMIN in user.role_names
    promoted, created_again = auth.bootstrap_administrator(session, email=farmer.email, full_name=None, password=None)
    session.commit()
    assert not created_again
    assert auth.load_current_user(session, promoted.id).roles == frozenset({domain.ROLE_FARMER, domain.ROLE_ADMIN})
