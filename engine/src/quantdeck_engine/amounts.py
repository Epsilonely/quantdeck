"""Amounts as text, for clients, logs, and records."""

from decimal import Decimal


def fixed_point(amount: Decimal) -> str:
    """Fixed-point text, never exponent notation such as `0E-8` (D-33)."""
    return format(amount, "f")
