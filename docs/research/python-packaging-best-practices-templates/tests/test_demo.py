import pytest
from hypothesis import given
from hypothesis import strategies as st
from inline_snapshot import snapshot

from demo_pkg import clamp, double, main

pytestmark = pytest.mark.anyio


@given(st.integers())
def test_clamp_bounds(n: int) -> None:
    assert 1 <= clamp(n) <= 50


def test_clamp_values() -> None:
    assert [clamp(0), clamp(60), clamp(7)] == snapshot([1, 50, 7])


async def test_double() -> None:
    assert await double(2) == 4


def test_main(capsys: pytest.CaptureFixture[str]) -> None:
    main()
    assert capsys.readouterr().out == "50\n"
