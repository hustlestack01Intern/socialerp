"""CRM memory: append-only events, sourced consent, versioned evidence-backed traits."""

from packages.core.crm.repository import CRMRepository
from packages.core.crm.service import (
    ALLOWED_TRAITS,
    PURPOSE_MARKETING,
    SENSITIVE_TRAIT_NAMES,
    CRMService,
    CustomerNotFoundError,
    TraitNotAllowedError,
    erasure_tombstone,
)

__all__ = [
    "ALLOWED_TRAITS",
    "PURPOSE_MARKETING",
    "SENSITIVE_TRAIT_NAMES",
    "CRMRepository",
    "CRMService",
    "CustomerNotFoundError",
    "TraitNotAllowedError",
    "erasure_tombstone",
]
