"""Domain vocabulary shared by models, services and the interface.

Delivery workflow
-----------------
The delivery *stage* is stored in ``transport_requests.status`` and follows::

    Pending -> Approved -> Assigned -> Dispatched -> In-Transit -> Delivered
       \\________\\__________\\___________\\______________\\-> Cancelled

``Delayed`` is deliberately **not** a stage. It is stored as a separate flag
(``transport_requests.is_delayed`` plus ``delay_reason``/``delay_reported_at``)
and recorded in the history as ``delay_reported`` / ``delay_resolved`` events.
This keeps the underlying stage (for example ``In-Transit``) intact, so the
workflow resumes exactly where it was once the delay is resolved.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- roles
ROLE_FARMER = "farmer"
ROLE_DRIVER = "driver"
ROLE_CONSUMER = "consumer"
ROLE_ADMIN = "admin"

ALL_ROLES = (ROLE_FARMER, ROLE_DRIVER, ROLE_CONSUMER, ROLE_ADMIN)
#: Roles a visitor may choose on the public registration form. Never includes admin.
PUBLIC_REGISTRATION_ROLES = (ROLE_FARMER, ROLE_DRIVER, ROLE_CONSUMER)
ROLE_LABELS = {
    ROLE_FARMER: "Farmer",
    ROLE_DRIVER: "Driver",
    ROLE_CONSUMER: "Consumer / Buyer",
    ROLE_ADMIN: "Administrator",
}

# --------------------------------------------------------------- delivery stages
STATUS_PENDING = "Pending"
STATUS_APPROVED = "Approved"
STATUS_ASSIGNED = "Assigned"
STATUS_DISPATCHED = "Dispatched"
STATUS_IN_TRANSIT = "In-Transit"
STATUS_DELIVERED = "Delivered"
STATUS_CANCELLED = "Cancelled"
#: Display label for the delay flag (not a stage).
DELAYED_LABEL = "Delayed"

DELIVERY_STATUSES = (
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_ASSIGNED,
    STATUS_DISPATCHED,
    STATUS_IN_TRANSIT,
    STATUS_DELIVERED,
    STATUS_CANCELLED,
)
ACTIVE_STATUSES = (
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_ASSIGNED,
    STATUS_DISPATCHED,
    STATUS_IN_TRANSIT,
)
TERMINAL_STATUSES = (STATUS_DELIVERED, STATUS_CANCELLED)
#: Stages in which a driver is attached and a delay can be reported.
DELAYABLE_STATUSES = (STATUS_ASSIGNED, STATUS_DISPATCHED, STATUS_IN_TRANSIT)
#: Stages in which the assigned driver is on the road.
ON_ROAD_STATUSES = (STATUS_DISPATCHED, STATUS_IN_TRANSIT)
#: Stages that require an assigned driver (enforced by a database check constraint too).
DRIVER_REQUIRED_STATUSES = (STATUS_ASSIGNED, STATUS_DISPATCHED, STATUS_IN_TRANSIT, STATUS_DELIVERED)

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_PENDING: frozenset({STATUS_APPROVED, STATUS_CANCELLED}),
    STATUS_APPROVED: frozenset({STATUS_ASSIGNED, STATUS_CANCELLED}),
    STATUS_ASSIGNED: frozenset({STATUS_DISPATCHED, STATUS_CANCELLED}),
    STATUS_DISPATCHED: frozenset({STATUS_IN_TRANSIT, STATUS_CANCELLED}),
    STATUS_IN_TRANSIT: frozenset({STATUS_DELIVERED, STATUS_CANCELLED}),
    STATUS_DELIVERED: frozenset(),
    STATUS_CANCELLED: frozenset(),
}

#: The progressions only the assigned driver may perform.
DRIVER_PROGRESSIONS: dict[str, str] = {
    STATUS_ASSIGNED: STATUS_DISPATCHED,
    STATUS_DISPATCHED: STATUS_IN_TRANSIT,
    STATUS_IN_TRANSIT: STATUS_DELIVERED,
}
#: Stages from which a farmer may cancel their own request.
FARMER_CANCELLABLE_STATUSES = (STATUS_PENDING, STATUS_APPROVED)


def is_transition_allowed(current: str, new: str) -> bool:
    return new in ALLOWED_TRANSITIONS.get(current, frozenset())


# ----------------------------------------------------------------- batch status
BATCH_REGISTERED = "Registered"
BATCH_AWAITING_TRANSPORT = "Awaiting Transport"
BATCH_IN_TRANSPORT = "In Transport"
BATCH_DELIVERED = "Delivered"
BATCH_CANCELLED = "Cancelled"
BATCH_STATUSES = (
    BATCH_REGISTERED,
    BATCH_AWAITING_TRANSPORT,
    BATCH_IN_TRANSPORT,
    BATCH_DELIVERED,
    BATCH_CANCELLED,
)

# ------------------------------------------------------------ driver availability
AVAILABILITY_AVAILABLE = "available"
AVAILABILITY_BUSY = "busy"
AVAILABILITY_UNAVAILABLE = "unavailable"
AVAILABILITY_STATUSES = (AVAILABILITY_AVAILABLE, AVAILABILITY_BUSY, AVAILABILITY_UNAVAILABLE)
AVAILABILITY_LABELS = {
    AVAILABILITY_AVAILABLE: "Available",
    AVAILABILITY_BUSY: "Busy (on a trip)",
    AVAILABILITY_UNAVAILABLE: "Unavailable",
}

# ------------------------------------------------------------------ history events
EVENT_REQUEST_CREATED = "request_created"
EVENT_REQUEST_APPROVED = "request_approved"
EVENT_DRIVER_ASSIGNED = "driver_assigned"
EVENT_DRIVER_REASSIGNED = "driver_reassigned"
EVENT_REQUEST_ACCEPTED = "request_accepted"
EVENT_DISPATCHED = "dispatched"
EVENT_IN_TRANSIT = "in_transit"
EVENT_DELIVERED = "delivered"
EVENT_CANCELLED = "cancelled"
EVENT_DELAY_REPORTED = "delay_reported"
EVENT_DELAY_RESOLVED = "delay_resolved"
EVENT_CHECKPOINT = "checkpoint_recorded"
EVENT_DETAILS_UPDATED = "details_updated"

EVENT_LABELS = {
    EVENT_REQUEST_CREATED: "Transport requested",
    EVENT_REQUEST_APPROVED: "Request approved",
    EVENT_DRIVER_ASSIGNED: "Driver assigned",
    EVENT_DRIVER_REASSIGNED: "Driver reassigned",
    EVENT_REQUEST_ACCEPTED: "Request accepted by driver",
    EVENT_DISPATCHED: "Dispatched from pickup",
    EVENT_IN_TRANSIT: "In transit",
    EVENT_DELIVERED: "Delivered",
    EVENT_CANCELLED: "Cancelled",
    EVENT_DELAY_REPORTED: "Delay reported",
    EVENT_DELAY_RESOLVED: "Delay resolved",
    EVENT_CHECKPOINT: "Checkpoint recorded",
    EVENT_DETAILS_UPDATED: "Logistics details updated",
}

STATUS_EVENT_TYPES = {
    STATUS_DISPATCHED: EVENT_DISPATCHED,
    STATUS_IN_TRANSIT: EVENT_IN_TRANSIT,
    STATUS_DELIVERED: EVENT_DELIVERED,
}

#: Recorded locations are typed in by people; FarmTrace never fabricates GPS data.
LOCATION_SOURCE_MANUAL = "manual"
LOCATION_SOURCE_LABEL = "Manually recorded (no GPS)"
