"""Demo package."""

from __future__ import annotations

import asyncio


def clamp(value: int, low: int = 1, high: int = 50) -> int:
    """Clamp ``value`` into ``[low, high]``.

    Args:
        value: The number to clamp.
        low: Lower bound.
        high: Upper bound.

    Returns:
        The clamped value.
    """
    return max(low, min(high, value))


async def double(value: int) -> int:
    """Double a number asynchronously.

    Args:
        value: The number.

    Returns:
        Twice the number.
    """
    await asyncio.sleep(0)
    return value * 2


def main() -> None:
    """Entry point."""
    print(clamp(100))  # noqa: T201
