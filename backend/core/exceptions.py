"""One error shape everywhere.

Clients branch on ``code``, never on ``message``. A cross-tenant record
returns 404 rather than 403 so existence is not confirmed.

See docs/07-api.md.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

log = logging.getLogger(__name__)


class DomainError(APIException):
    """A business rule was violated. 422, not 400."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "domain_error"
    default_detail = "This action is not allowed in the current state."

    def __init__(self, message: str | None = None, *, code: str | None = None, meta=None):
        self.message = message or self.default_detail
        self.code = code or self.default_code
        self.meta = meta or {}
        super().__init__(self.message, self.code)


class InsufficientStock(DomainError):
    default_code = "insufficient_stock"
    default_detail = "Not enough stock available."


class PrescriptionRequired(DomainError):
    default_code = "prescription_required"
    default_detail = "This sale contains a prescription-only product."


class LicenceInvalid(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "licence_invalid"
    default_detail = "This branch does not hold a valid licence for that action."


class RegistrationInvalid(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "registration_invalid"
    default_detail = "A current pharmacist registration is required."


class StateConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "state_conflict"
    default_detail = "This record has already moved on."


def _envelope(code: str, message: str, *, detail="", field=None, meta=None, errors=None):
    body = {"error": {"code": code, "message": message, "detail": detail, "field": field}}
    if meta:
        body["error"]["meta"] = meta
    if errors:
        body["error"]["errors"] = errors
    return body


class Duplicate(DomainError):
    """This already exists. 409, because the request was well formed."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "duplicate"
    default_detail = "That already exists."


#: Constraint name → what the person actually typed. Only the ones a
#: person can hit from a form; anything else falls back to a generic
#: message rather than showing them a constraint name.
DUPLICATE_MESSAGES = {
    "uq_location_code": ("code", "A location already uses that code."),
    "uq_till_code": ("code", "A till already uses that code."),
    "uq_branch_code": ("code", "A branch already uses that code."),
    "uq_manufacturer_name": ("name", "That manufacturer is already listed."),
    "uq_expense_category_code": ("code", "That category code is taken."),
    "uq_product_type": ("code", "That product type already exists."),
    "uq_licence_number": ("number", "That licence number is already recorded."),
    "uq_council_number": ("council_number", "That council number is already recorded."),
    "uq_device_code": ("code", "A device already uses that code."),
    "uq_registration_number": (
        "registration_number",
        "That registration number is already recorded.",
    ),
    "uq_batch_number": ("batch_number", "That batch number already exists."),
    "uq_member_number": ("member_number", "That member number is already recorded."),
    "uq_listing_per_vendor_product": (
        "product",
        "This product is already listed. Edit the existing offer.",
    ),
    "uq_one_open_shift": ("till", "This till already has an open shift."),
    "uq_one_primary_image": ("is_primary", "This product already has a main picture."),
}


def _as_duplicate(exc) -> Duplicate | None:
    """Turn a unique violation into something a person can act on.

    The constraint name is the only reliable identifier — the driver's
    message wording differs between backends and versions.
    """
    text = str(exc)
    for constraint, (field, message) in DUPLICATE_MESSAGES.items():
        if constraint in text:
            return Duplicate(message, code="duplicate", meta={"field": field})
    if "duplicate key value" in text or "UNIQUE constraint failed" in text:
        return Duplicate()
    return None


def exception_handler(exc, context):
    """Map every exception onto the documented envelope."""
    if isinstance(exc, IntegrityError):
        # A constraint the serializer could not check, because the
        # organization is injected after validation.
        duplicate = _as_duplicate(exc)
        if duplicate is not None:
            exc = duplicate
        else:
            log.exception("Unhandled integrity error", exc_info=exc)
            return Response(
                _envelope("conflict", "That change conflicts with existing data."),
                status=status.HTTP_409_CONFLICT,
            )

    if isinstance(exc, Http404):
        return Response(
            _envelope("not_found", "Not found."), status=status.HTTP_404_NOT_FOUND
        )

    if isinstance(exc, PermissionDenied):
        return Response(
            _envelope("forbidden", "You do not have access to this."),
            status=status.HTTP_403_FORBIDDEN,
        )

    if isinstance(exc, DjangoValidationError):
        exc = ValidationError(detail=exc.message_dict if hasattr(exc, "message_dict") else exc.messages)

    if isinstance(exc, DomainError):
        return Response(
            _envelope(exc.code, exc.message, meta=exc.meta), status=exc.status_code
        )

    if isinstance(exc, ValidationError):
        errors = []
        detail = exc.detail if isinstance(exc.detail, dict) else {"non_field_errors": exc.detail}
        for field, messages in detail.items():
            for message in messages if isinstance(messages, list) else [messages]:
                errors.append(
                    {
                        "field": None if field == "non_field_errors" else field,
                        "code": getattr(message, "code", "invalid"),
                        "message": str(message),
                    }
                )
        return Response(
            _envelope("validation_error", "Check the highlighted fields.", errors=errors),
            status=status.HTTP_400_BAD_REQUEST,
        )

    response = drf_exception_handler(exc, context)
    if response is not None:
        code = getattr(exc, "default_code", "error")
        response.data = _envelope(code, str(exc.detail) if hasattr(exc, "detail") else str(exc))[
            "error"
        ]
        response.data = {"error": response.data}
        return response

    log.exception("Unhandled exception", exc_info=exc)
    return Response(
        _envelope("server_error", "Something went wrong. Try again."),
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
