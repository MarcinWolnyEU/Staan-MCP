"""Unit tests for the coverage-guided fuzz harnesses under ``fuzz/``.

Each target's ``test_one_input`` is driven with fixed interesting inputs and with ``hypothesis``
binary fuzz. The harnesses carry the invariants themselves (documented exceptions only,
idempotence, no input mutation) and assert them on every input, so these tests only prove the
harnesses run cleanly. They must pass without atheris installed: atheris ships no Windows
wheels, and the targets import it only inside ``main()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fuzz.fuzz_citations_to_gfm import test_one_input as fuzz_citations_to_gfm
from fuzz.fuzz_error_from_response import test_one_input as fuzz_error_from_response
from fuzz.fuzz_expand_markets import test_one_input as fuzz_expand_markets
from fuzz.fuzz_json_object import test_one_input as fuzz_json_object
from fuzz.fuzz_normalize_domain import test_one_input as fuzz_normalize_domain

if TYPE_CHECKING:
    from collections.abc import Callable

TARGETS: dict[str, Callable[[bytes], None]] = {
    "normalize_domain": fuzz_normalize_domain,
    "expand_markets": fuzz_expand_markets,
    "error_from_response": fuzz_error_from_response,
    "json_object": fuzz_json_object,
    "citations_to_gfm": fuzz_citations_to_gfm,
}

INTERESTING_INPUTS: tuple[bytes, ...] = (
    b"",
    b"\x00",
    b"\x00\x01\x02\x03",
    b"\xff\xfe\xfd\xfc",
    b"example.com",
    b"https://www.Example.com:443/a?b",
    b"www.www.example.com",
    b'{"message": "boom", "code": "KEY_DISABLED"}',
    b'{"message": ["a", 3, " b "], "url": "https://example.org/x"}',
    b"*",
    b"en-g*",
    b"one.[^1] two",
)


@pytest.mark.parametrize("target", TARGETS.values(), ids=TARGETS.keys())
@pytest.mark.parametrize("data", INTERESTING_INPUTS)
def test_targets_accept_fixed_inputs(target: Callable[[bytes], None], data: bytes) -> None:
    """Every harness runs cleanly on the fixed interesting inputs, including empty bytes."""
    target(data)


@given(st.binary(max_size=256))
def test_normalize_domain_accepts_arbitrary_bytes(data: bytes) -> None:
    """The domain harness accepts arbitrary bytes without violating its invariants."""
    fuzz_normalize_domain(data)


@given(st.binary(max_size=256))
def test_expand_markets_accepts_arbitrary_bytes(data: bytes) -> None:
    """The market harness accepts arbitrary bytes without violating its invariants."""
    fuzz_expand_markets(data)


@given(st.binary(max_size=256))
def test_error_from_response_accepts_arbitrary_bytes(data: bytes) -> None:
    """The error-mapping harness accepts arbitrary bytes without violating its invariants."""
    fuzz_error_from_response(data)


@given(st.binary(max_size=256))
def test_json_object_accepts_arbitrary_bytes(data: bytes) -> None:
    """The response-decoding harness accepts arbitrary bytes without violating its invariants."""
    fuzz_json_object(data)


@given(st.binary(max_size=512))
def test_citations_to_gfm_accepts_arbitrary_bytes(data: bytes) -> None:
    """The citation-rendering harness accepts arbitrary bytes without violating its invariants."""
    fuzz_citations_to_gfm(data)
