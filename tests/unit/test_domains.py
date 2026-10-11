"""Tests for domain normalisation."""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from staan_mcp.domains import (
    MAX_DOMAINS,
    InvalidDomainError,
    normalize_domain,
    normalize_domains,
)

_LABEL = r"[a-z0-9-]+"
_ALNUM = "abcdefghijklmnopqrstuvwxyz0123456789"

# Strategies stay cheap on purpose. Hypothesis fails a test whose input generation is slow
# (HealthCheck.too_slow: fewer than 10 valid inputs after a second of drawing) and a CPU that is
# shared with other work is enough to trip it. ``st.from_regex`` and many small draws per element
# cost about ten times more than one ``st.text`` draw, so a label is one drawn string whose two
# ends are checked: the same language as ``[a-z0-9]([a-z0-9-]{0,10}[a-z0-9])?``.
label = st.text(_ALNUM + "-", min_size=1, max_size=12).filter(
    lambda text: not text.startswith("-") and not text.endswith("-")
)
# "www." is stripped by design, so generated domains never start with it.
bare_domains = (
    st.lists(label, min_size=2, max_size=4)
    .map(".".join)
    .filter(lambda domain: not domain.startswith("www."))
)
schemes = st.sampled_from(["", "http://", "https://", "//", "HTTPS://", "ftp://", "git+ssh://"])
userinfos = st.sampled_from(["", "user@", "user:secret@", "a@b@"])
ports = st.sampled_from(["", ":", ":80", ":443", ":8080"])
tails = st.sampled_from(["", "/", "/a/b", "?q=1", "#frag", "/p?q=1#f", "."])
paddings = st.sampled_from(["", " ", "\t", "\n  "])


@st.composite
def decorated_domains(draw: st.DrawFn) -> tuple[str, str]:
    """Return ``(decorated input, expected bare domain)``."""
    domain = draw(bare_domains)
    tail = draw(tails)
    # A trailing dot is only meaningful directly after the host.
    host = domain + ("." if tail == "." else "")
    www = draw(st.sampled_from(["", "www."]))
    pad = draw(paddings)
    rest = "" if tail == "." else tail
    text = f"{pad}{draw(schemes)}{draw(userinfos)}{www}{host}{draw(ports)}{rest}{pad}"
    return text, domain


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.Example.com:443/a?b", "example.com"),
        ("www.wikipedia.org", "wikipedia.org"),
        ("en.wikipedia.org", "en.wikipedia.org"),
        ("example.com", "example.com"),
        ("EXAMPLE.COM", "example.com"),
        ("  example.com  ", "example.com"),
        ("http://example.com", "example.com"),
        ("HTTP://Example.com", "example.com"),
        ("ftp://example.com", "example.com"),
        ("git+ssh://example.com/repo", "example.com"),
        ("//example.com/path", "example.com"),
        ("example.com/path/to/page", "example.com"),
        ("example.com?query=1", "example.com"),
        ("example.com#fragment", "example.com"),
        ("example.com/a?b#c", "example.com"),
        ("https://user@example.com", "example.com"),
        ("https://user:pass@example.com:8443/x", "example.com"),
        ("user:pass@example.com", "example.com"),
        ("example.com:8080", "example.com"),
        ("example.com:", "example.com"),
        ("example.com.", "example.com"),
        ("https://example.com./x", "example.com"),
        ("www.example.com.", "example.com"),
        ("WWW.Example.COM", "example.com"),
        ("www.www.example.com", "www.example.com"),
        ("wwwexample.com", "wwwexample.com"),
        ("www2.example.com", "www2.example.com"),
        ("sub-domain.example.co.uk", "sub-domain.example.co.uk"),
        ("a-b.c-d.org", "a-b.c-d.org"),
        ("1.2.3.4", "1.2.3.4"),
        ("xn--bcher-kva.de", "xn--bcher-kva.de"),
        ("https://example.com/user@host", "example.com"),
        ("https://example.com?next=//evil.com", "example.com"),
    ],
)
def test_normalize_domain_examples(raw: str, expected: str) -> None:
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://bücher.de/katalog", "xn--bcher-kva.de"),
        ("BÜCHER.de", "xn--bcher-kva.de"),
        ("www.bücher.de", "xn--bcher-kva.de"),
        ("münchen.example.com", "xn--mnchen-3ya.example.com"),
        ("пример.рф", "xn--e1afmkfd.xn--p1ai"),
        ("bücher。de", "xn--bcher-kva.de"),
    ],
)
def test_internationalised_names_become_punycode(raw: str, expected: str) -> None:
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "localhost",
        "com",
        "www.com",
        "https://",
        "https://localhost:8000/x",
        "/path/only",
        "?query",
        "#frag",
        "example..com",
        ".example.com",
        "example.com..",
        "-example.com",
        "example-.com",
        "example.-com",
        "example.com-",
        "exa_mple.com",
        "exa mple.com",
        "example\n.com",
        "a.b\n.com",
        "http://http://example.com",
        "https://https://www.example.com",
    ],
)
def test_invalid_domains_are_rejected(raw: str) -> None:
    with pytest.raises(InvalidDomainError):
        normalize_domain(raw)


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("localhost", "it needs at least one dot"),
        ("example..com", "it has an empty label"),
        (".example.com", "it has an empty label"),
        ("exa_mple.com", "labels may only contain letters, digits and hyphens"),
        ("exa mple.com", "labels may only contain letters, digits and hyphens"),
        ("-a.example.com", "a label must not start or end with a hyphen"),
        ("a-.example.com", "a label must not start or end with a hyphen"),
        ("a.example-.com", "a label must not start or end with a hyphen"),
    ],
)
def test_invalid_domain_message_names_the_value_and_the_reason(raw: str, reason: str) -> None:
    with pytest.raises(InvalidDomainError) as excinfo:
        normalize_domain(raw)

    assert str(excinfo.value) == (
        f"Invalid domain {raw!r}: {reason}. Expected a bare domain such as 'example.com'."
    )


def test_overlong_internationalised_label_is_reported_as_invalid_idn() -> None:
    with pytest.raises(InvalidDomainError, match="internationalised domain name"):
        normalize_domain("ä" * 70 + ".com")


def test_internationalised_name_with_an_empty_label_is_rejected() -> None:
    with pytest.raises(InvalidDomainError, match="internationalised domain name"):
        normalize_domain("bücher..de")


def test_character_that_idna_prohibits_is_rejected() -> None:
    value = "a‎b.com"  # contains an invisible left-to-right mark

    with pytest.raises(InvalidDomainError) as excinfo:
        normalize_domain(value)

    assert str(excinfo.value) == (
        f"Invalid domain {value!r}: it is not a valid internationalised domain name. "
        "Expected a bare domain such as 'example.com'."
    )


def test_invalid_domain_error_is_a_value_error() -> None:
    assert issubclass(InvalidDomainError, ValueError)


def test_max_domains_is_ten() -> None:
    assert MAX_DOMAINS == 10


def test_normalize_domains_normalises_each_item() -> None:
    assert normalize_domains(["https://www.A.com/x", "b.org:80", "EN.wikipedia.org"]) == [
        "a.com",
        "b.org",
        "en.wikipedia.org",
    ]


def test_normalize_domains_dedupes_after_normalisation_keeping_first_seen_order() -> None:
    values = ["b.com", "https://www.a.com", "A.COM", "www.b.com/path", "c.com", "a.com"]

    assert normalize_domains(values) == ["b.com", "a.com", "c.com"]


def test_normalize_domains_of_nothing_is_empty() -> None:
    assert normalize_domains([]) == []  # pylint: disable=use-implicit-booleaness-not-comparison


def test_normalize_domains_accepts_exactly_max_domains() -> None:
    values = [f"site{i}.com" for i in range(MAX_DOMAINS)]

    assert normalize_domains(values) == values


def test_normalize_domains_rejects_more_than_max_domains() -> None:
    values = [f"site{i}.com" for i in range(MAX_DOMAINS + 1)]

    with pytest.raises(InvalidDomainError) as excinfo:
        normalize_domains(values)

    assert str(excinfo.value) == f"At most {MAX_DOMAINS} distinct domains are allowed, got 11."


def test_normalize_domains_counts_distinct_domains_not_inputs() -> None:
    values = [f"site{i}.com" for i in range(MAX_DOMAINS)] * 2

    assert normalize_domains(values) == values[:MAX_DOMAINS]


def test_normalize_domains_raises_for_any_invalid_item() -> None:
    with pytest.raises(InvalidDomainError, match="localhost"):
        normalize_domains(["a.com", "localhost"])


def test_normalize_domains_accepts_a_tuple() -> None:
    assert normalize_domains(("a.com", "b.com")) == ["a.com", "b.com"]


@given(decorated_domains())
def test_decorations_are_stripped_to_the_bare_domain(case: tuple[str, str]) -> None:
    text, expected = case

    assert normalize_domain(text) == expected


@given(decorated_domains())
def test_normalize_domain_is_idempotent(case: tuple[str, str]) -> None:
    once = normalize_domain(case[0])

    # Only one leading "www." is removed per pass, so "www.www.x.com" needs two passes.
    if not once.startswith("www."):
        assert normalize_domain(once) == once


@given(st.text(max_size=60))
def test_arbitrary_text_is_rejected_or_yields_a_valid_bare_domain(text: str) -> None:
    try:
        result = normalize_domain(text)
    except InvalidDomainError:
        return

    assert result.isascii()
    assert result == result.lower()
    assert "." in result
    assert all(re.fullmatch(_LABEL, part) for part in result.split("."))
    assert not any(part.startswith("-") or part.endswith("-") for part in result.split("."))
    if not result.startswith("www."):
        assert normalize_domain(result) == result


@given(st.lists(bare_domains, max_size=MAX_DOMAINS))
def test_normalize_domains_keeps_unique_domains_in_order(domains: list[str]) -> None:
    decorated = [f"https://www.{d.upper()}/x" for d in domains]

    assert normalize_domains(decorated) == list(dict.fromkeys(domains))


# Only the number of distinct domains matters to this property, so each domain is one drawn label
# under a fixed suffix (a label of "www" is stripped to "example.com", which is still distinct).
distinct_domains = st.lists(
    st.text(_ALNUM, min_size=1, max_size=12),
    min_size=MAX_DOMAINS + 1,
    max_size=MAX_DOMAINS + 5,
    unique=True,
).map(lambda labels: [f"{name}.example.com" for name in labels])


@given(distinct_domains)
def test_more_than_max_distinct_domains_always_raises(domains: list[str]) -> None:
    with pytest.raises(InvalidDomainError, match="At most 10 distinct domains"):
        normalize_domains(domains)
