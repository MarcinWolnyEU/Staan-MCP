"""Request rate limiting: never start more upstream requests per second than Staan allows.

Staan caps an API key at 20 requests per second. One ``web_search`` over many markets sends one
request per market, two calls at the same time double that, and every retry is a request too, so
the budget is easy to exceed. :class:`RateLimiter` keeps to it by *delaying* requests, never by
refusing them.

It is a sliding-window log, not a token bucket: it remembers the start time of the last requests
and lets a new one begin only when fewer than ``capacity`` starts fall inside the preceding
window. A bucket that starts full and refills at the same time can pass about twice the rate in
one second (a full burst, then the refill); the log never does.

Standard library only, so the package and the standalone ``stdio_server.py`` share it. The clock
is injectable and nothing here sleeps: a caller books a slot with :meth:`RateLimiter.reserve`,
waits however it waits (``anyio.sleep``, ``threading.Event.wait``) until the slot's ``start`` and
only then sends. The window is a little longer than a second (see
:data:`~staan_mcp.config.RATE_LIMIT_SAFETY_MARGIN`).

A limiter belongs to one server. Separate server processes sharing one API key do not know about
each other, so their budgets add up.
"""

from __future__ import annotations

import bisect
import math
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from staan_mcp.config import RATE_LIMIT_SAFETY_MARGIN, RATE_LIMIT_WINDOW

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["RateLimiter", "Slot"]


@dataclass(frozen=True, slots=True)
class Slot:
    """A booked moment to send one request.

    Attributes:
        start: When the request may be sent, on the limiter's clock.
        delay: How long from the booking until ``start``; ``0.0`` when under budget.
    """

    start: float
    delay: float


class RateLimiter:
    """At most ``capacity`` request starts in any window, with FIFO slots and thread safety.

    ``rate`` is requests per second. From 1 upwards the limiter allows ``floor(rate)`` starts in
    any window of one second (a window cannot hold a fraction of a request, so 2.5 is treated as
    2). Below 1 it allows one start per ``1 / rate`` seconds (0.5 means one every two seconds).
    ``0`` turns the limiter off: every slot is immediate. The window is lengthened by
    :data:`~staan_mcp.config.RATE_LIMIT_SAFETY_MARGIN`.

    Slots are handed out in the order of the calls, each as early as the window allows, so
    waiting requests are served first come, first served. The booked starts are kept in a sorted
    list; the ones older than the window are forgotten on each booking.

    Args:
        rate: Requests per second, finite and not negative.
        clock: Monotonic clock in seconds; injectable for tests.

    Raises:
        ValueError: If ``rate`` is negative, infinite or not a number.
    """

    def __init__(self, rate: float, clock: Callable[[], float] = time.monotonic) -> None:
        """Create a limiter for ``rate`` requests per second."""
        if not math.isfinite(rate) or rate < 0:
            msg = f"The rate must be a finite number that is not negative, got {rate!r}."
            raise ValueError(msg)
        self._clock = clock
        self._lock = threading.Lock()
        self._starts: list[float] = []
        if rate == 0:
            self._capacity = 0
            self._window = 0.0
        elif rate >= 1:
            self._capacity = math.floor(rate)
            self._window = RATE_LIMIT_WINDOW + RATE_LIMIT_SAFETY_MARGIN
        else:
            self._capacity = 1
            self._window = RATE_LIMIT_WINDOW / rate + RATE_LIMIT_SAFETY_MARGIN

    @property
    def capacity(self) -> int:
        """Most starts allowed in one window; ``0`` when the limiter is off."""
        return self._capacity

    @property
    def window(self) -> float:
        """Length of the window in seconds, safety margin included; ``0.0`` when off."""
        return self._window

    def reserve(self, max_wait: float = math.inf) -> Slot | None:
        """Book the earliest start that keeps to the rate, atomically.

        Args:
            max_wait: The longest delay the caller can accept, for instance the time left before
                its deadline. If the slot would be later, nothing is booked.

        Returns:
            The booked :class:`Slot`, or ``None`` if its delay would exceed ``max_wait``.
        """
        with self._lock:
            now = self._clock()
            if self._capacity == 0:
                return Slot(now, 0.0)
            starts = self._starts
            del starts[: bisect.bisect_right(starts, now - self._window)]
            start = now
            if len(starts) >= self._capacity:
                start = max(now, starts[-self._capacity] + self._window)
            if start - now > max_wait:
                return None
            bisect.insort(starts, start)
            return Slot(start, start - now)

    def remaining(self, slot: Slot) -> float:
        """Seconds left until ``slot`` may be used, never negative.

        A caller waits in a loop (``while (left := limiter.remaining(slot)) > 0: sleep(left)``)
        because a sleep may end slightly early, and the request must not start before its slot.
        """
        return max(slot.start - self._clock(), 0.0)

    def release(self, slot: Slot) -> None:
        """Give back a slot that will not be used, if its start has not been reached.

        A caller that was cancelled while waiting uses it, so a request that never happens does
        not delay the ones behind it. The slots of other waiters do not move up, so a gap left in
        the middle of a queue stays empty until the window has passed; a slot at the end of the
        queue (the common case, a whole call cancelled) is simply free again. A slot whose start
        has already been reached is left alone: the moment has been used up.
        """
        with self._lock:
            if slot.start <= self._clock():
                return
            starts = self._starts
            index = bisect.bisect_left(starts, slot.start)
            if index < len(starts) and starts[index] == slot.start:
                del starts[index]
