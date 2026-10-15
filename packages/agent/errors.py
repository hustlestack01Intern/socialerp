"""Shared agent errors."""

from __future__ import annotations


class AgentError(Exception):
    """Base error for the agent subsystem."""


class IntentParseError(AgentError):
    """The model's structured output could not be parsed/validated."""


__all__ = ["AgentError", "IntentParseError"]
