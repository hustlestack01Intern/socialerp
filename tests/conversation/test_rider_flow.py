"""Unit tests for the rider WhatsApp quick-reply flow."""

from __future__ import annotations

from packages.conversation import rider_flow


def test_parse_rider_command_basic() -> None:
    assert rider_flow.parse_rider_command("picked up") is not None
    cmd = rider_flow.parse_rider_command("nimefika")
    assert cmd is not None and cmd.action == "arrived" and cmd.language == "sw"
    cmd2 = rider_flow.parse_rider_command("nimekabidhi")
    assert cmd2 is not None and cmd2.action == "delivered"
    cmd3 = rider_flow.parse_rider_command("failed no answer")
    assert cmd3 is not None and cmd3.action == "failed"
    assert cmd3.reason is not None  # type: ignore[union-attr]
    cmd4 = rider_flow.parse_rider_command("status")
    assert cmd4 is not None and cmd4.action == "status"


def test_negated_positive_does_not_transition() -> None:
    """A rider saying they have NOT arrived/moved must not trigger a transition.

    A false `arrived` on a POD order fires an M-Pesa prompt to the customer, so
    negations return None (the flow replies with the quick-reply menu).
    """
    for text in (
        "not arrived",
        "not yet arrived",
        "haven't arrived",
        "not picked up",
        "haven't picked up yet",
        "bado nimefika",
        "can't arrive",
        "cannot arrive",
        "won't arrive",
        "didn't arrive",
    ):
        assert rider_flow.parse_rider_command(text) is None, text


def test_failure_reports_still_parse_under_negation_guard() -> None:
    """Explicit failure reports (which read like negations) must still fail."""
    for text in ("not delivered", "undelivered", "not done", "no answer", "wasn't delivered"):
        cmd = rider_flow.parse_rider_command(text)
        assert cmd is not None and cmd.action == "failed", text


def test_positive_phrases_still_parse_without_negation() -> None:
    """The negation guard must not break normal positive quick replies."""
    for text in ("arrived", "nimefika", "picked up", "nimechukua", "delivered", "nimekabidhi"):
        cmd = rider_flow.parse_rider_command(text)
        assert cmd is not None, text
