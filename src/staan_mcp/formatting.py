"""Shape Staan responses for LLM consumption.

Two independent, pure pieces live here:

* Search: :func:`simplify_result` flattens one upstream web result into a small
  :class:`SimpleSearchResult`, and :func:`interleave_results` merges several per-market result
  lists into one ranked, de-duplicated list.
* Answer: :func:`citations_to_gfm` turns the upstream ``citations`` (insertion positions plus
  source ids) into GitHub-flavoured Markdown footnotes.

Upstream payloads are untrusted JSON, so every helper checks types instead of assuming them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from itertools import zip_longest
from typing import TYPE_CHECKING, Any, Final, NamedTuple, NotRequired, TypedDict
from unicodedata import category
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

__all__ = [
    "SNIPPET_SEPARATOR",
    "SimpleSearchResult",
    "citations_to_gfm",
    "interleave_results",
    "simplify_result",
    "truncate_text",
]

SNIPPET_SEPARATOR: Final = " (...) "
"""Joins the main snippet and the ``extra_snippets`` chunks into one string."""

_MAX_SOURCE_URL_LENGTH: Final = 2048
_MAX_ID_LENGTH: Final = 18
_MAX_ID_BITS: Final = 64
_URL_SCHEMES: Final = frozenset({"http", "https"})
_FENCE: Final = re.compile(r"(`{3,}|~{3,})(.*)")
_MIN_TABLE_ROWS: Final = 2


class SimpleSearchResult(TypedDict):
    """A web search result reduced to what a model needs."""

    title: str
    url: str
    snippet: str
    full_content: NotRequired[str]


# --------------------------------------------------------------------------------------
# Search results
# --------------------------------------------------------------------------------------


def _text_or_empty(value: object) -> str:
    """Return ``value`` if it is a string, otherwise ``""``."""
    return value if isinstance(value, str) else ""


def _chunk_text(item: object) -> str | None:
    """Extract the text of one ``extra_snippets`` entry (``{"chunk": str}`` or a plain str)."""
    if isinstance(item, str):
        return item
    if isinstance(item, Mapping):
        chunk = item.get("chunk")
        if isinstance(chunk, str):
            return chunk
    return None


def _chunks(extra_snippets: object) -> list[str]:
    """Collect the usable chunk texts from an ``extra_snippets`` value, skipping junk."""
    if not isinstance(extra_snippets, list | tuple):
        return []
    return [text for item in extra_snippets if (text := _chunk_text(item)) is not None]


def _full_content_text(full_content: object) -> str | None:
    """Return the non-empty page text of a ``full_content`` value, or ``None``."""
    text = full_content.get("text") if isinstance(full_content, Mapping) else full_content
    if isinstance(text, str) and text:
        return text
    return None


def truncate_text(text: str, limit: int) -> str:
    """Cut ``text`` to about ``limit`` characters, at a paragraph boundary where possible.

    The cut prefers the last blank line, then the last line break or space, and never throws
    away more than half of the allowed text to reach one; otherwise it is a hard cut. A visible
    marker that states how much was kept is appended, so the reader (a model) knows the text is
    incomplete.

    Args:
        text: The text to shorten.
        limit: The most characters of ``text`` to keep; ``0`` or less means no limit.

    Returns:
        ``text`` unchanged if it fits, otherwise the kept part plus the marker.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    head = text[:limit]
    floor = limit // 2
    cut = head.rfind("\n\n")
    if cut < floor:
        cut = max(head.rfind("\n"), head.rfind(" "))
    if cut < floor:
        cut = limit
    kept = head[:cut].rstrip()
    return f"{kept}\n\n[... truncated: {len(kept)} of {len(text)} characters shown ...]"


def simplify_result(result: Mapping[str, Any], *, max_content_chars: int = 0) -> SimpleSearchResult:
    """Reduce one upstream web result to a :class:`SimpleSearchResult`.

    The ``snippet`` is the upstream snippet followed by every ``extra_snippets`` chunk, joined
    with :data:`SNIPPET_SEPARATOR`; blank parts are dropped. ``full_content`` is included only
    when the upstream page text is a non-empty string, shortened by :func:`truncate_text` when
    ``max_content_chars`` is set.

    Args:
        result: One item of ``web.results`` from a search response.
        max_content_chars: Longest ``full_content`` to keep; ``0`` means no limit.

    Returns:
        The simplified result. Missing or non-string ``title``, ``url`` and ``snippet`` become
        ``""``.
    """
    parts = [_text_or_empty(result.get("snippet")), *_chunks(result.get("extra_snippets"))]
    simple = SimpleSearchResult(
        title=_text_or_empty(result.get("title")),
        url=_text_or_empty(result.get("url")),
        snippet=SNIPPET_SEPARATOR.join(part for part in parts if part.strip()),
    )
    if (text := _full_content_text(result.get("full_content"))) is not None:
        simple["full_content"] = truncate_text(text, max_content_chars)
    return simple


def _by_rank(
    per_market: Sequence[Sequence[Mapping[str, Any]]],
) -> Iterator[Mapping[str, Any]]:
    """Yield results rank by rank: rank 0 of every market, then rank 1, and so on.

    Markets with fewer results are padded with ``None``, which (like any non-mapping) is skipped.
    """
    for rank in zip_longest(*per_market):
        for item in rank:
            if isinstance(item, Mapping):
                yield item


def interleave_results(
    per_market: Sequence[Sequence[Mapping[str, Any]]],
) -> list[Mapping[str, Any]]:
    """Merge per-market result lists round-robin by rank, dropping duplicate URLs.

    Args:
        per_market: One list of upstream results per market, in market order.

    Returns:
        Results ordered rank 0 of market 1, rank 0 of market 2, ..., rank 1 of market 1, and so
        on. The first occurrence of a URL wins; items without a string ``url`` are dropped.
    """
    seen: set[str] = set()
    merged: list[Mapping[str, Any]] = []
    for item in _by_rank(per_market):
        url = item.get("url")
        if isinstance(url, str) and url not in seen:
            seen.add(url)
            merged.append(item)
    return merged


# --------------------------------------------------------------------------------------
# Answer citations -> GitHub-flavoured Markdown footnotes
# --------------------------------------------------------------------------------------


class _Placed(NamedTuple):
    """A citation with a validated insertion index and the URL of its source."""

    offset: int
    url: str


def _is_safe_url(url: str) -> bool:
    """Tell whether ``url`` can sit in a ``[^n]: url`` definition without altering the Markdown.

    It must be short, an absolute http(s) URL with a host, and free of whitespace and control
    characters (a line break would let upstream text inject Markdown after the definitions).
    """
    if len(url) > _MAX_SOURCE_URL_LENGTH:
        return False
    if any(char.isspace() or category(char) == "Cc" for char in url):
        return False
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError:
        return False
    return parts.scheme.lower() in _URL_SCHEMES and bool(parts.hostname)


def _source_url(source: object) -> str | None:
    """Return the ``url`` of a source if it is present and safe to print, otherwise ``None``."""
    if isinstance(source, Mapping):
        url = source.get("url")
        if isinstance(url, str) and _is_safe_url(url := url.strip()):
            return url
    return None


def _id_key(source_id: object) -> str | None:
    """Return the string form of a source id (a str or an int of sane size), else ``None``."""
    if isinstance(source_id, str):
        return source_id
    if isinstance(source_id, int) and source_id.bit_length() <= _MAX_ID_BITS:
        return str(source_id)
    return None


def _source_urls(sources: Iterable[object]) -> dict[str, str]:
    """Map ``str(source id)`` to its URL; the first usable source wins for a repeated id."""
    urls: dict[str, str] = {}
    for source in sources:
        url = _source_url(source)
        key = _id_key(source.get("id")) if isinstance(source, Mapping) else None
        if url is not None and key is not None:
            urls.setdefault(key, url)
    return urls


def _resolve_url(
    source_id: object,
    urls_by_id: Mapping[str, str],
    sources: Sequence[object],
) -> str | None:
    """Find the URL a citation refers to.

    Ids are matched as strings. Only if no source carries a usable id at all, a decimal id ``n``
    with ``1 <= n <= len(sources)`` selects ``sources[n - 1]``, which covers APIs that number
    sources instead of naming them. When ids exist, an unknown one is dropped: citing the wrong
    page is worse than citing none.
    """
    key = _id_key(source_id)
    if key is None:
        return None
    if key in urls_by_id:
        return urls_by_id[key]
    if urls_by_id or len(key) > _MAX_ID_LENGTH:
        return None
    if key.isascii() and key.isdecimal() and 1 <= int(key) <= len(sources):
        return _source_url(sources[int(key) - 1])
    return None


def _index_for(answer: str, position: object) -> int | None:
    """Convert an upstream citation ``position`` to an insertion index into ``answer``.

    The unit is Python code points. If the API turns out to count UTF-16 code units or bytes, this
    is the only function that needs to change.

    Returns:
        The position clamped to ``[0, len(answer)]``, or ``None`` if it is not an integer
        (booleans do not count).
    """
    if isinstance(position, bool) or not isinstance(position, int):
        return None
    return max(0, min(position, len(answer)))


def _is_inside_word(answer: str, index: int) -> bool:
    """Tell whether ``index`` falls between two alphanumeric characters."""
    return 0 < index < len(answer) and answer[index - 1].isalnum() and answer[index].isalnum()


def _skip_back(answer: str, index: int) -> int:
    """Move ``index`` back over whitespace so a marker attaches to the preceding text."""
    while index > 0 and answer[index - 1].isspace():
        index -= 1
    return index


def _lines(answer: str) -> list[tuple[int, int, str]]:
    """Split ``answer`` into ``(start, end, text)`` lines, where ``end`` excludes the newline."""
    lines: list[tuple[int, int, str]] = []
    start = 0
    for text in answer.split("\n"):
        lines.append((start, start + len(text), text))
        start += len(text) + 1
    return lines


def _closes_fence(text: str, opened: tuple[str, int, int]) -> bool:
    """Tell whether ``text`` closes the fence ``opened``: same character, as long, no info."""
    found = _FENCE.fullmatch(text.strip())
    return (
        found is not None
        and found[1][0] == opened[0]
        and len(found[1]) >= opened[1]
        and not found[2].strip()
    )


def _fence_spans(lines: Sequence[tuple[int, int, str]], length: int) -> list[tuple[int, int]]:
    """Find fenced code blocks (backticks or tildes) as inclusive ``(first, last)`` index spans.

    An unclosed fence runs to the end of the text (``length``).
    """
    spans: list[tuple[int, int]] = []
    opened: tuple[str, int, int] | None = None  # (fence character, run length, start index)
    for start, end, text in lines:
        if opened is None:
            if found := _FENCE.fullmatch(text.strip()):
                opened = (found[1][0], len(found[1]), start)
        elif _closes_fence(text, opened):
            spans.append((opened[2], end))
            opened = None
    if opened is not None:
        spans.append((opened[2], length))
    return spans


def _table_spans(lines: Sequence[tuple[int, int, str]]) -> list[tuple[int, int]]:
    """Find GFM table blocks: runs of at least two lines that start with a pipe."""
    spans: list[tuple[int, int]] = []
    run: list[tuple[int, int]] = []
    for start, end, text in [*lines, (0, 0, "")]:  # the sentinel line flushes the last run
        if text.lstrip().startswith("|"):
            run.append((start, end))
            continue
        if len(run) >= _MIN_TABLE_ROWS:
            spans.append((run[0][0], run[-1][1]))
        run = []
    return spans


def _block_spans(answer: str) -> list[tuple[int, int]]:
    """Find the Markdown blocks a marker must never be placed in or against.

    Returns:
        Fenced code blocks and tables as inclusive ``(first, last)`` index spans, latest start
        first.
    """
    lines = _lines(answer)
    spans = [*_fence_spans(lines, len(answer)), *_table_spans(lines)]
    return sorted(spans, reverse=True)


def _snap_index(answer: str, index: int) -> int:
    """Move an insertion index so a marker never splits a word or follows a space."""
    if _is_inside_word(answer, index):
        while index < len(answer) and answer[index].isalnum():
            index += 1
    return _skip_back(answer, index)


def _clear_blocks(answer: str, index: int, blocks: Sequence[tuple[int, int]]) -> int | None:
    """Move an insertion index out of fenced code blocks and tables.

    A marker inside a code fence, or at the end of a fence or a table row, would stop the block
    from rendering (and swallow the footnote definitions), so it moves to the end of the text
    before the block.

    Returns:
        The adjusted index, or ``None`` if a block has no text before it to attach to.
    """
    for first, last in blocks:  # latest start first, so one pass also clears nested blocks
        if first <= index <= last:
            index = _skip_back(answer, first)
            if index == 0:
                return None
    return index


def _locate(
    answer: str,
    citation: object,
    urls_by_id: Mapping[str, str],
    sources: Sequence[object],
    blocks: Sequence[tuple[int, int]],
) -> _Placed | None:
    """Validate one citation, returning where it goes and what it cites, or ``None``."""
    if not isinstance(citation, Mapping):
        return None
    index = _index_for(answer, citation.get("position"))
    url = _resolve_url(citation.get("source_id"), urls_by_id, sources)
    if index is None or url is None:
        return None
    snapped = _clear_blocks(answer, _snap_index(answer, index), blocks)
    return None if snapped is None else _Placed(snapped, url)


def _number_footnotes(
    placed: Iterable[_Placed],
) -> tuple[dict[int, list[int]], dict[str, int]]:
    """Number sources by first appearance and group the numbers by insertion index.

    ``placed`` must already be in text order. A repeated source reuses its number, and a number
    is listed once per index.

    Returns:
        ``(numbers_at_index, number_by_url)``.
    """
    number_by_url: dict[str, int] = {}
    numbers_at_index: dict[int, list[int]] = {}
    for index, url in placed:
        number = number_by_url.setdefault(url, len(number_by_url) + 1)
        numbers = numbers_at_index.setdefault(index, [])
        if number not in numbers:
            numbers.append(number)
    return numbers_at_index, number_by_url


def _insert_markers(answer: str, numbers_at_index: Mapping[int, Sequence[int]]) -> str:
    """Insert ``[^n]`` markers into ``answer`` at the given indices.

    Splitting the text at the sorted indices and joining once is equivalent to inserting from the
    highest index down: earlier offsets are never shifted.
    """
    pieces: list[str] = []
    start = 0
    for index, numbers in sorted(numbers_at_index.items()):
        pieces.append(answer[start:index])
        pieces.extend(f"[^{number}]" for number in numbers)
        start = index
    pieces.append(answer[start:])
    return "".join(pieces)


def citations_to_gfm(
    answer: str,
    citations: Sequence[Mapping[str, Any]],
    sources: Sequence[Mapping[str, Any]],
) -> str:
    """Render an answer's citations as GitHub-flavoured Markdown footnotes.

    Each citation's ``position`` is an insertion index into ``answer`` and its ``source_id``
    selects an entry of ``sources``. Footnotes are numbered ``[^1]``, ``[^2]``, ... in order of
    first appearance in the text, and a block of definitions (``[^n]: <url>``) for the cited
    sources is appended after a blank line.

    Citations whose position is not an integer or whose source cannot be resolved are ignored,
    and so are sources whose URL is not a short, whitespace-free http(s) URL. Valid positions are
    clamped to the text, moved to the end of a word they would split, moved back over whitespace
    so the marker attaches to the preceding text, and moved out of fenced code blocks and tables
    (to the end of the text before them) so the Markdown keeps rendering.

    Args:
        answer: The marker-free answer text.
        citations: Upstream ``citations``: items with ``position`` and ``source_id``.
        sources: Upstream ``sources``: items with ``id`` and ``url``.

    Returns:
        The answer with footnote markers and definitions, or ``answer`` unchanged if no citation
        is valid.
    """
    urls_by_id = _source_urls(sources)
    blocks = _block_spans(answer)
    located = (_locate(answer, citation, urls_by_id, sources, blocks) for citation in citations)
    placed = sorted((item for item in located if item is not None), key=lambda item: item.offset)
    if not placed:
        return answer
    numbers_at_index, number_by_url = _number_footnotes(placed)
    definitions = "\n".join(f"[^{number}]: {url}" for url, number in number_by_url.items())
    return f"{_insert_markers(answer, numbers_at_index)}\n\n{definitions}"
