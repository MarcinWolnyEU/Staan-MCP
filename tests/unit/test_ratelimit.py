"""The request rate limiter: exact slots on a fake clock, and the invariant it exists for.

The invariant is that no window of one second (a little more, see the safety margin) holds more
than ``capacity`` booked starts. It is checked on the booked schedule itself, for hand-made
bursts, for random sequences of bookings, waits and releases (Hypothesis) and for many threads
booking at the same time against the real clock.
"""
# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

import math
import re
import threading
import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from staan_mcp.config import (
    DEFAULT_RATE_LIMIT,
    RATE_LIMIT_SAFETY_MARGIN,
    RATE_LIMIT_WINDOW,
    Settings,
)
from staan_mcp.ratelimit import RateLimiter, Slot

WINDOW = 1.05
"""One second plus the 50 ms safety margin, written out so a changed constant fails a test."""
EPSILON = 1e-9


class Clock:
    """A clock that only moves when the test says so."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def booked(limiter: RateLimiter, count: int) -> list[Slot]:
    slots = [limiter.reserve() for _ in range(count)]
    assert None not in slots
    return [slot for slot in slots if slot is not None]


def assert_within_budget(starts: list[float], capacity: int, window: float) -> None:
    """No ``window`` holds more than ``capacity`` of the (half-open) ``starts``."""
    ordered = sorted(starts)
    for first, later in zip(ordered, ordered[capacity:], strict=False):
        assert later - first >= window - EPSILON, (first, later, capacity, window)


def test_the_defaults_are_staans_cap_of_twenty_a_second_and_a_small_margin() -> None:
    assert DEFAULT_RATE_LIMIT == 20.0
    assert RATE_LIMIT_WINDOW == 1.0
    assert RATE_LIMIT_SAFETY_MARGIN == 0.05
    assert Settings(api_key=None).rate_limit == 20.0


@pytest.mark.parametrize(
    ("rate", "capacity", "window"),
    [
        (20, 20, 1.05),
        (2.5, 2, 1.05),  # a fraction of a request does not fit in a second
        (1, 1, 1.05),
        (0.5, 1, 2.05),  # one request every two seconds
        (0, 0, 0.0),
    ],
)
def test_the_shape_of_the_window_follows_the_rate(
    rate: float, capacity: int, window: float
) -> None:
    limiter = RateLimiter(rate)

    assert limiter.capacity == capacity
    assert limiter.window == pytest.approx(window)


@pytest.mark.parametrize("rate", [-1.0, -0.001, math.nan, math.inf, -math.inf])
def test_a_rate_that_is_negative_or_not_finite_is_refused(rate: float) -> None:
    message = f"The rate must be a finite number that is not negative, got {rate!r}."
    with pytest.raises(ValueError, match=re.escape(message)):
        RateLimiter(rate)


def test_requests_within_the_budget_are_not_delayed() -> None:
    limiter = RateLimiter(20, Clock())

    slots = booked(limiter, 20)

    assert [slot.delay for slot in slots] == [0.0] * 20
    assert [slot.start for slot in slots] == [1000.0] * 20


def test_the_request_over_the_budget_waits_for_the_first_start_plus_the_window() -> None:
    limiter = RateLimiter(20, Clock())
    booked(limiter, 20)

    slot = limiter.reserve()

    assert slot is not None
    assert slot.start == pytest.approx(1000.0 + WINDOW)
    assert slot.delay == pytest.approx(WINDOW)


def test_a_large_burst_gets_fifo_slots_in_groups_of_the_capacity() -> None:
    limiter = RateLimiter(20, Clock())

    slots = booked(limiter, 65)

    delays = [slot.delay for slot in slots]
    assert delays == sorted(delays)  # first come, first served
    for group in range(3):
        expected = group * WINDOW
        assert delays[group * 20 : group * 20 + 20] == [pytest.approx(expected)] * 20
    assert delays[60:] == [pytest.approx(3 * WINDOW)] * 5


def test_a_late_arrival_waits_only_for_what_is_left_of_the_window() -> None:
    clock = Clock()
    limiter = RateLimiter(20, clock)
    booked(limiter, 20)
    clock.advance(1.0)

    slot = limiter.reserve()

    assert slot is not None
    assert slot.delay == pytest.approx(0.05)


def test_the_budget_is_back_once_the_window_has_passed() -> None:
    clock = Clock()
    limiter = RateLimiter(20, clock)
    booked(limiter, 40)  # the second twenty are booked a window ahead
    clock.advance(2 * WINDOW)

    slots = booked(limiter, 20)

    assert [slot.delay for slot in slots] == [0.0] * 20


def test_a_start_exactly_one_window_old_no_longer_counts() -> None:
    clock = Clock()
    limiter = RateLimiter(1, clock)
    booked(limiter, 1)
    clock.advance(WINDOW)

    slot = limiter.reserve()

    assert slot is not None
    assert slot.delay == 0.0


def test_a_rate_of_two_and_a_half_allows_two_a_second() -> None:
    limiter = RateLimiter(2.5, Clock())

    delays = [slot.delay for slot in booked(limiter, 5)]

    assert delays == [0.0, 0.0, pytest.approx(WINDOW), pytest.approx(WINDOW), pytest.approx(2.1)]


def test_a_rate_below_one_spaces_the_requests_by_its_reciprocal() -> None:
    limiter = RateLimiter(0.5, Clock())

    delays = [slot.delay for slot in booked(limiter, 3)]

    assert delays == [0.0, pytest.approx(2.05), pytest.approx(4.1)]


def test_a_limiter_that_is_off_never_delays_or_remembers() -> None:
    clock = Clock()
    limiter = RateLimiter(0, clock)

    slots = booked(limiter, 1000)
    limiter.release(slots[0])

    assert {slot.delay for slot in slots} == {0.0}
    assert len(limiter._starts) == 0


def test_a_slot_that_would_come_after_max_wait_is_refused_and_books_nothing() -> None:
    limiter = RateLimiter(2, Clock())
    booked(limiter, 2)

    refused = limiter.reserve(max_wait=0.5)
    next_slot = limiter.reserve()

    assert refused is None
    assert next_slot is not None
    assert next_slot.delay == pytest.approx(WINDOW)  # not pushed back by the refused one


def test_a_slot_that_comes_exactly_at_max_wait_is_granted() -> None:
    limiter = RateLimiter(1, Clock(0.0))  # at zero, the delay is exactly the window
    booked(limiter, 1)

    slot = limiter.reserve(max_wait=limiter.window)

    assert slot is not None
    assert slot.delay == limiter.window


def test_an_immediate_slot_is_granted_whatever_max_wait_is() -> None:
    slot = RateLimiter(1, Clock()).reserve(max_wait=0.0)

    assert slot is not None
    assert slot.delay == 0.0


def test_remaining_counts_down_to_the_slot_and_stops_at_zero() -> None:
    clock = Clock()
    limiter = RateLimiter(1, clock)
    booked(limiter, 1)
    slot = limiter.reserve()
    assert slot is not None

    assert limiter.remaining(slot) == pytest.approx(WINDOW)
    clock.advance(0.5)
    assert limiter.remaining(slot) == pytest.approx(WINDOW - 0.5)
    clock.advance(10.0)
    assert limiter.remaining(slot) == 0.0


def test_a_released_slot_at_the_end_of_the_queue_is_free_again() -> None:
    limiter = RateLimiter(1, Clock())
    booked(limiter, 1)
    unused = limiter.reserve()
    assert unused is not None

    limiter.release(unused)
    again = limiter.reserve()

    assert again == unused


def test_releasing_a_whole_cancelled_burst_gives_its_waiting_slots_back() -> None:
    limiter = RateLimiter(20, Clock())
    booked(limiter, 12)
    cancelled = booked(limiter, 30)  # 8 start now, 20 a window ahead, 2 two windows ahead
    for slot in cancelled:
        limiter.release(slot)  # the 8 whose start was reached stay booked: they were due

    delays = [slot.delay for slot in booked(limiter, 12)]

    assert delays == [pytest.approx(WINDOW)] * 12  # not 2 windows or more behind the cancelled
    assert len(limiter._starts) == 20 + 12


def test_a_released_slot_in_the_middle_leaves_a_gap_that_stays_empty() -> None:
    limiter = RateLimiter(1, Clock())
    first, middle, last = booked(limiter, 3)

    limiter.release(middle)
    after = limiter.reserve()

    assert first.delay == 0.0
    assert after is not None
    assert after.start == pytest.approx(last.start + WINDOW)  # one window after the last
    assert limiter._starts == [first.start, last.start, after.start]


def test_a_slot_whose_start_has_been_reached_cannot_be_released() -> None:
    clock = Clock()
    limiter = RateLimiter(1, clock)
    (used,) = booked(limiter, 1)

    limiter.release(used)
    slot = limiter.reserve()

    assert slot is not None
    assert slot.delay == pytest.approx(WINDOW)


@pytest.mark.parametrize("start", [1000.5, 1001.5, 5000.0])
def test_releasing_a_slot_that_was_never_booked_changes_nothing(start: float) -> None:
    limiter = RateLimiter(1, Clock())
    first, second = booked(limiter, 2)  # at 1000.0 and 1001.05

    limiter.release(Slot(start, start - 1000.0))

    assert limiter._starts == [first.start, second.start]


def test_the_default_clock_is_the_monotonic_one() -> None:
    before = time.monotonic()

    slot = RateLimiter(5).reserve()
    after = time.monotonic()

    assert slot is not None
    assert before <= slot.start <= after


def test_the_clock_is_read_once_per_booking() -> None:
    reads: list[float] = []

    def clock() -> float:
        reads.append(1.0)
        return 1000.0

    RateLimiter(5, clock).reserve()

    assert len(reads) == 1


def test_old_starts_are_forgotten_so_memory_stays_bounded() -> None:
    clock = Clock()
    limiter = RateLimiter(20, clock)

    for _ in range(50):
        booked(limiter, 20)
        clock.advance(WINDOW)

    assert len(limiter._starts) <= 20


@st.composite
def schedules(draw: st.DrawFn) -> tuple[float, list[tuple[str, float]]]:
    rate = draw(st.sampled_from([0.25, 0.5, 1, 1.5, 2, 3, 5, 7.5, 20]))
    steps = draw(
        st.lists(
            st.one_of(
                st.tuples(st.just("reserve"), st.just(0.0)),
                st.tuples(st.just("wait"), st.floats(0.0, 3.0)),
                st.tuples(st.just("release"), st.floats(0.0, 1.0)),
            ),
            max_size=60,
        )
    )
    return rate, steps


@settings(deadline=None, max_examples=200)
@given(schedules())
def test_no_window_ever_holds_more_than_the_capacity(
    schedule: tuple[float, list[tuple[str, float]]],
) -> None:
    rate, steps = schedule
    clock = Clock()
    limiter = RateLimiter(rate, clock)
    live: dict[int, Slot] = {}
    previous_start = -math.inf
    released = False
    for number, (action, value) in enumerate(steps):
        if action == "wait":
            clock.advance(value)
        elif action == "reserve":
            slot = limiter.reserve()
            assert slot is not None
            assert slot.start >= clock.now - EPSILON
            assert slot.delay == pytest.approx(slot.start - clock.now)
            assert released or slot.start >= previous_start - EPSILON  # first come, first served
            previous_start = slot.start
            live[number] = slot
        elif live:
            key = sorted(live)[int(value * (len(live) - 1))]
            slot = live[key]
            limiter.release(slot)
            if slot.start > clock.now:
                del live[key]
                released = True
        assert_within_budget(
            [slot.start for slot in live.values()], limiter.capacity, limiter.window
        )


def test_many_threads_booking_at_once_never_exceed_the_budget() -> None:
    limiter = RateLimiter(20)
    slots: list[Slot] = []
    start_together = threading.Barrier(16)

    def hammer() -> None:
        start_together.wait()
        mine = [limiter.reserve() for _ in range(40)]
        slots.extend(slot for slot in mine if slot is not None)

    threads = [threading.Thread(target=hammer) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(slots) == 16 * 40
    starts = sorted(slot.start for slot in slots)
    assert_within_budget(starts, 20, limiter.window)
    assert starts[-1] - starts[0] == pytest.approx((640 / 20 - 1) * limiter.window, abs=0.5)
    assert len(set(starts)) > 20  # booked in many different groups, not all at once
