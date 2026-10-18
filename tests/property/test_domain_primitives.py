"""
Property-based tests for domain primitives.

These tests derive from invariants rather than hand-picked examples.
Run with: pytest tests/property/ -v
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from packages.core.domain import (
    SKU,
    CustomerPhone,
    Money,
    Quantity,
    generate_conversation_id,
    generate_customer_id,
    generate_delivery_id,
    generate_message_id,
    generate_order_id,
    generate_payment_id,
    generate_product_id,
    generate_reservation_id,
    generate_rider_id,
    generate_tenant_id,
    generate_user_id,
    generate_variant_id,
)

# =============================================================================
# CustomerPhone Properties
# =============================================================================


class TestCustomerPhone:
    """Property tests for CustomerPhone invariant: always 2547XXXXXXXX or 2541XXXXXXXX."""

    @given(st.text(min_size=1, max_size=20))
    def test_normalization_always_produces_valid_format_or_raises(self, raw: str):
        """Any input either normalizes to valid format or raises ValueError."""
        try:
            phone = CustomerPhone.from_raw(raw)
            # If it succeeds, it must match the pattern
            assert phone.value.startswith("254")
            assert len(phone.value) == 12
            assert phone.value[3] in ("7", "1")
            assert phone.value[4:].isdigit()
        except ValueError:
            # Invalid inputs correctly raise
            pass

    @given(st.integers(min_value=70000000, max_value=79999999))
    def test_safaricom_7xx_normalizes_correctly(self, number: int):
        """Safaricom 7xx numbers normalize to 2547xxxxxxxx."""
        raw = f"07{number:08d}"
        phone = CustomerPhone.from_raw(raw)
        assert phone.value == f"2547{number:08d}"

    @given(st.integers(min_value=10000000, max_value=19999999))
    def test_airtel_1xx_normalizes_correctly(self, number: int):
        """Airtel 1xx numbers normalize to 2541xxxxxxxx."""
        raw = f"01{number:08d}"
        phone = CustomerPhone.from_raw(raw)
        assert phone.value == f"2541{number:08d}"

    @given(st.sampled_from(["+254712345678", "254712345678", "0712345678", "712345678"]))
    def test_various_formats_produce_same_result(self, raw: str):
        """All common formats for same number produce identical normalized value."""
        phone = CustomerPhone.from_raw(raw)
        assert phone.value == "254712345678"

    def test_invalid_prefix_raises(self):
        """Numbers not starting with 7 or 1 after 254 raise ValueError."""
        with pytest.raises(ValueError):
            CustomerPhone.from_raw("254912345678")

    def test_invalid_length_raises(self):
        """Wrong length raises ValueError."""
        with pytest.raises(ValueError):
            CustomerPhone.from_raw("25471234567")  # 11 digits
        with pytest.raises(ValueError):
            CustomerPhone.from_raw("2547123456789")  # 13 digits

    def test_immutability(self):
        """CustomerPhone is immutable (frozen dataclass)."""
        phone = CustomerPhone.from_raw("0712345678")
        with pytest.raises(AttributeError):
            phone.value = "254700000000"

    def test_masked_logging(self):
        """masked() returns masked version for safe logging."""
        phone = CustomerPhone.from_raw("0712345678")
        masked = phone.masked()
        # masked() masks last 4 digits: 254712345678 -> 25471234****
        assert masked == "25471234****"
        assert "5678" not in masked


# =============================================================================
# Money Properties
# =============================================================================


class TestMoney:
    """Property tests for Money invariant: integer cents, no negative, precise arithmetic."""

    @given(st.integers(min_value=0, max_value=10_000_000))
    def test_from_cents_roundtrip(self, cents: int):
        """Money.from_cents(cents).cents == cents"""
        money = Money.from_cents(cents)
        assert money.cents == cents

    @given(st.integers(min_value=0, max_value=100_000), st.integers(min_value=0, max_value=99))
    def test_from_kes_precision(self, kes: int, cents_part: int):
        """Money.from_kes preserves precision to the cent."""
        money = Money.from_kes(kes + cents_part / 100)
        expected = kes * 100 + cents_part
        assert money.cents == expected

    @pytest.mark.parametrize(
        ("raw_kes", "expected_cents"),
        [
            # 1 + 82/100 has nearest double 1.8199999999999998; truncation gave 181.
            (1 + 82 / 100, 182),
            (1 + 57 / 100, 157),
            (1.82, 182),
            (Decimal("1.82"), 182),
            (100.50, 10050),
            (100, 10000),
            (0, 0),
            (0.01, 1),
            (0.005, 1),  # half-up rounds up
            (2.125, 213),  # 2.125 -> 212.5 cents -> half-up 213
        ],
    )
    def test_from_kes_regression_no_truncation(self, raw_kes, expected_cents):
        """Regression: from_kes must round to nearest cent, never truncate.

        Guards the money bug where int(kes * 100) dropped a cent for floats whose
        shortest repr lands just below the true value.
        """
        assert Money.from_kes(raw_kes).cents == expected_cents

    @given(
        st.integers(min_value=0, max_value=1_000_000), st.integers(min_value=0, max_value=1_000_000),
    )
    def test_addition_commutative(self, a: int, b: int):
        """Money addition is commutative."""
        m1 = Money.from_cents(a)
        m2 = Money.from_cents(b)
        assert (m1 + m2).cents == (m2 + m1).cents

    @given(
        st.integers(min_value=0, max_value=1_000_000), st.integers(min_value=0, max_value=1_000_000),
    )
    def test_addition_associative(self, a: int, b: int):
        """Money addition is associative."""
        m1 = Money.from_cents(a)
        m2 = Money.from_cents(b)
        m3 = Money.from_cents(100)
        assert ((m1 + m2) + m3).cents == (m1 + (m2 + m3)).cents

    @given(st.integers(min_value=0, max_value=1_000_000), st.integers(min_value=1, max_value=100))
    def test_multiplication_by_int(self, cents: int, multiplier: int):
        """Multiplication by integer scales correctly."""
        money = Money.from_cents(cents)
        result = money * multiplier
        assert result.cents == cents * multiplier

    @given(st.integers(min_value=100, max_value=1_000_000), st.integers(min_value=1, max_value=100))
    def test_division_by_int(self, cents: int, divisor: int):
        """Integer division truncates correctly."""
        money = Money.from_cents(cents)
        result = money / divisor
        assert result.cents == cents // divisor

    @given(
        st.integers(min_value=0, max_value=1_000_000), st.integers(min_value=0, max_value=1_000_000),
    )
    def test_subtraction_no_negative(self, a: int, b: int):
        """Subtraction never produces negative (raises instead)."""
        m1 = Money.from_cents(a)
        m2 = Money.from_cents(b)
        if a >= b:
            result = m1 - m2
            assert result.cents == a - b
        else:
            with pytest.raises(ValueError):
                m1 - m2

    def test_negative_construction_raises(self):
        """Negative cents raises ValueError."""
        with pytest.raises(ValueError):
            Money.from_cents(-1)

    @given(
        st.integers(min_value=0, max_value=1_000_000), st.integers(min_value=0, max_value=1_000_000),
    )
    def test_comparison_operators(self, a: int, b: int):
        """Comparison operators work correctly."""
        m1 = Money.from_cents(a)
        m2 = Money.from_cents(b)
        assert (m1 < m2) == (a < b)
        assert (m1 <= m2) == (a <= b)
        assert (m1 > m2) == (a > b)
        assert (m1 >= m2) == (a >= b)
        assert (m1 == m2) == (a == b)

    def test_kes_property(self):
        """kes property returns correct Decimal."""
        money = Money.from_cents(12345)
        assert money.kes == Decimal("123.45")

    def test_str_representation(self):
        """String representation shows KES with 2 decimals."""
        assert str(Money.from_cents(100)) == "KES 1.00"
        assert str(Money.from_cents(12345)) == "KES 123.45"

    def test_immutability(self):
        """Money is immutable."""
        money = Money.from_cents(100)
        with pytest.raises(AttributeError):
            money.cents = 200


# =============================================================================
# Quantity Properties
# =============================================================================


class TestQuantity:
    """Property tests for Quantity invariant: non-negative integer."""

    @given(st.integers(min_value=0, max_value=10_000))
    def test_construction(self, value: int):
        """Valid quantities construct successfully."""
        q = Quantity(value)
        assert q.value == value

    @given(st.integers(max_value=-1))
    def test_negative_raises(self, value: int):
        """Negative quantities raise ValueError."""
        with pytest.raises(ValueError):
            Quantity(value)

    @given(st.integers(min_value=0, max_value=1000), st.integers(min_value=0, max_value=1000))
    def test_addition(self, a: int, b: int):
        """Quantity addition works."""
        assert (Quantity(a) + Quantity(b)).value == a + b

    @given(st.integers(min_value=0, max_value=1000), st.integers(min_value=0, max_value=1000))
    def test_subtraction_no_negative(self, a: int, b: int):
        """Subtraction never produces negative."""
        q1 = Quantity(a)
        q2 = Quantity(b)
        if a >= b:
            assert (q1 - q2).value == a - b
        else:
            with pytest.raises(ValueError):
                q1 - q2

    @given(st.integers(min_value=0, max_value=100), st.integers(min_value=0, max_value=10))
    def test_multiplication(self, value: int, multiplier: int):
        """Multiplication by int works."""
        assert (Quantity(value) * multiplier).value == value * multiplier

    def test_immutability(self):
        """Quantity is immutable."""
        q = Quantity(10)
        with pytest.raises(AttributeError):
            q.value = 20


# =============================================================================
# SKU Properties
# =============================================================================


class TestSKU:
    """Property tests for SKU invariant: alphanumeric, hyphen, underscore, max 50 chars."""

    @given(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("Lu", "Nd"),
                whitelist_characters="_-",
                min_codepoint=65,
                max_codepoint=122,
            ),
            min_size=1,
            max_size=50,
        ),
    )
    def test_valid_sku_constructs(self, value: str):
        """Valid SKUs construct and normalize to uppercase."""
        sku = SKU(value)
        assert sku.value == value.upper()

    @given(st.text(min_size=51, max_size=100))
    def test_too_long_raises(self, value: str):
        """SKUs longer than 50 chars raise ValueError."""
        with pytest.raises(ValueError):
            SKU(value)

    @given(st.text(alphabet="!@#$%^&*()+=[]{}|;':\",./<>?`~ ", min_size=1, max_size=20))
    def test_invalid_chars_raise(self, value: str):
        """Invalid characters raise ValueError."""
        with pytest.raises(ValueError):
            SKU(value)

    def test_empty_raises(self):
        """Empty SKU raises ValueError."""
        with pytest.raises(ValueError):
            SKU("")

    def test_whitespace_stripped(self):
        """Leading/trailing whitespace is stripped."""
        sku = SKU("  ABC-123  ")
        assert sku.value == "ABC-123"

    def test_case_insensitive(self):
        """SKU is case-insensitive (stored uppercase)."""
        assert SKU("abc-123").value == "ABC-123"
        assert SKU("ABC-123").value == "ABC-123"


# =============================================================================
# ID Generation Properties
# =============================================================================


class TestIDGeneration:
    """Property tests for ID generation."""

    def test_tenant_id_unique(self):
        """Generated TenantIds are unique."""
        ids = {generate_tenant_id() for _ in range(1000)}
        assert len(ids) == 1000

    def test_all_id_types_unique(self):
        """All ID generation functions produce unique values."""
        for gen_func in [
            generate_tenant_id,
            generate_user_id,
            generate_customer_id,
            generate_product_id,
            generate_variant_id,
            generate_order_id,
            generate_reservation_id,
            generate_payment_id,
            generate_delivery_id,
            generate_rider_id,
            generate_conversation_id,
            generate_message_id,
        ]:
            ids = {gen_func() for _ in range(100)}
            assert len(ids) == 100, f"{gen_func.__name__} produced duplicates"


# =============================================================================
# Cross-Type Interaction Properties
# =============================================================================


class TestCrossTypeInteractions:
    """Property tests for interactions between domain types."""

    @given(st.integers(min_value=0, max_value=1000), st.integers(min_value=0, max_value=1000))
    def test_money_quantity_multiplication(self, price_cents: int, qty: int):
        """Money * Quantity = Money (line total)."""
        price = Money.from_cents(price_cents)
        Quantity(qty)
        line_total = price * qty
        assert line_total.cents == price_cents * qty

    @given(st.integers(min_value=0, max_value=10000), st.integers(min_value=0, max_value=100))
    def test_order_total_from_items(self, item_price: int, item_qty: int):
        """Order total equals sum of line items."""
        price = Money.from_cents(item_price)
        Quantity(item_qty)
        line_total = price * item_qty

        # Two items same price/qty
        total = line_total + line_total
        assert total.cents == 2 * item_price * item_qty


# =============================================================================
# Settings for Property Tests
# =============================================================================

# Reduce examples for faster feedback in CI; increase locally for thoroughness
settings.register_profile("ci", max_examples=100, suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("dev", max_examples=500, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("ci")
