"""
Base Model Class

Base class for all SQLAlchemy models.
"""

import enum
from collections.abc import Sequence

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all models."""


def enum_values(enum_cls: type[enum.Enum]) -> Sequence[str]:
    """Persist enum *values* (lowercase) rather than member *names*.

    SQLAlchemy's ``Enum`` defaults to storing member names (e.g. ``RECEIVE``);
    the Alembic migrations define these columns as ``String`` with lowercase
    ``server_default``s (e.g. ``'receive'``). Pass this as ``values_callable`` so
    the ORM and the schema agree and values read back cleanly.
    """
    return [member.value for member in enum_cls]
