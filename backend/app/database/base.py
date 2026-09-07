"""Declarative base shared by all PostgreSQL models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for persisted domain entities."""
