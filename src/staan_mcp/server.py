"""The MCP server: tool definitions for Staan web search and Answer.

:func:`create_server` builds an :class:`~mcp.server.MCPServer` with two read-only tools,
``web_search`` and ``answer``. One :class:`~staan_mcp.client.StaanClient` lives for the whole
server lifetime (opened by the lifespan, closed on shutdown) and the tools reach it through the
request context. Everything a tool reports back to the model is either a JSON document or a
:class:`~mcp.server.mcpserver.exceptions.ToolError` with a safe message.
"""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver import Context  # noqa: TC002  (resolved at runtime by the SDK)
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from staan_mcp import __version__
from staan_mcp.auth import IntrospectionTokenVerifier, build_auth_settings, build_token_verifier
from staan_mcp.client import StaanClient
from staan_mcp.contract import (
    ANSWER_DESCRIPTION,
    ANSWER_FIELD_PARAM,
    ANSWER_QUERY_PARAM,
    ANSWER_SIMPLE_PARAM,
    ANSWER_TITLE,
    CONTENT_PARAM,
    EXCLUDE_PARAM,
    FILTER_PARAM,
    FULL_CONTENT_PARAM,
    INCLUDE_PARAM,
    INSTRUCTIONS,
    LANGUAGE_PARAM,
    MARKET_PARAM,
    MAX_ANSWER_QUERY_LENGTH,
    MAX_ANSWER_SOURCES,
    MAX_SEARCH_QUERY_LENGTH,
    MAX_SOURCE_CONTENT_LENGTH,
    MAX_SOURCE_TITLE_LENGTH,
    MODE_PARAM,
    OFFSET_PARAM,
    SEARCH_QUERY_PARAM,
    SEARCH_SIMPLE_PARAM,
    SERVER_NAME,
    SOURCES_MODE_PARAM,
    SOURCES_PARAM,
    TITLE_PARAM,
    URL_PARAM,
    WEB_SEARCH_DESCRIPTION,
    WEB_SEARCH_TITLE,
)
from staan_mcp.domains import InvalidDomainError, normalize_domain, normalize_domain_filters
from staan_mcp.errors import StaanAPIError, StaanMCPError, is_fatal
from staan_mcp.markets import DEFAULT_MARKET, InvalidMarketError, expand_markets
from staan_mcp.payloads import Outcome, answer_payload, raise_for_failures, search_payload
from staan_mcp.sources import check_source_url, require_url_or_content

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
    from typing import Self

    from staan_mcp.config import AuthConfig, Settings

__all__ = ["AnswerSource", "create_server"]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _AppState:
    """What the lifespan hands to every tool call.

    Attributes:
        client: The shared Staan client.
        max_content_chars: Longest ``full_content`` text kept per result; ``0`` means no limit.
    """

    client: StaanClient
    max_content_chars: int = 0


class AnswerSource(BaseModel):
    """A source: needs a url, content, or both."""

    url: Annotated[str | None, Field(description=URL_PARAM)] = None
    title: Annotated[
        str | None, Field(max_length=MAX_SOURCE_TITLE_LENGTH, description=TITLE_PARAM)
    ] = None
    content: Annotated[
        str | None,
        Field(max_length=MAX_SOURCE_CONTENT_LENGTH, description=CONTENT_PARAM),
    ] = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        """Accept only a public-looking absolute http(s) URL; blank means no URL."""
        return None if value is None else check_source_url(value)

    @model_validator(mode="after")
    def _require_url_or_content(self) -> Self:
        """Reject a source that carries neither a URL nor any content."""
        require_url_or_content(self.url, self.content)
        return self


class AnswerOutput(BaseModel):
    """The answer; with simple_response=false, the whole upstream response."""

    model_config = ConfigDict(extra="allow")

    answer: Annotated[str, Field(description=ANSWER_FIELD_PARAM)]


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


@contextmanager
def _tool_errors() -> Iterator[None]:
    """Turn the package's own errors into model-visible :class:`ToolError` messages.

    ``StaanMCPError`` (including a missing API key) and the market and domain validation errors
    carry safe, user-facing text. Any other exception propagates; the SDK reports it generically.
    """
    try:
        yield
    except (StaanMCPError, InvalidMarketError, InvalidDomainError) as exc:
        raise ToolError(str(exc)) from exc


def _json_result(payload: object) -> CallToolResult:
    """Wrap ``payload`` as a single compact-JSON text block with no structured content."""
    text = json.dumps(payload, ensure_ascii=False)
    return CallToolResult(content=[TextContent(type="text", text=text)])


def _structured_result(payload: dict[str, Any]) -> CallToolResult:
    """Return ``payload`` as structured content plus the same compact JSON as text."""
    text = json.dumps(payload, ensure_ascii=False)
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=payload)


def _annotations(*, idempotent: bool) -> ToolAnnotations:
    """Build the hints shared by both tools: read-only, open-world, never destructive."""
    return ToolAnnotations(
        read_only_hint=True,
        open_world_hint=True,
        destructive_hint=False,
        idempotent_hint=idempotent,
    )


def _state(ctx: Context[_AppState]) -> _AppState:
    """Return what the lifespan shares with every call: the client and the output limits."""
    return ctx.request_context.lifespan_context


async def _fetch_markets(
    search: Callable[[str], Awaitable[dict[str, Any]]], markets: Sequence[str]
) -> list[Outcome]:
    """Run ``search`` for every market concurrently, keeping market order.

    A key or configuration error cancels the markets still running, since they would all fail
    the same way. Other :class:`StaanMCPError` values are returned in place of a response.

    Args:
        search: Coroutine function taking a market and returning the raw response.
        markets: The concrete markets.

    Returns:
        One outcome per market: the raw response, or the error that ended it.
    """
    outcomes: list[Outcome] = [StaanAPIError("The search did not complete.")] * len(markets)

    async def run(index: int, market: str, scope: anyio.CancelScope) -> None:
        try:
            outcomes[index] = await search(market)
        except StaanMCPError as exc:
            outcomes[index] = exc
            if is_fatal(exc):
                scope.cancel()

    async with anyio.create_task_group() as group:
        for index, market in enumerate(markets):
            group.start_soon(run, index, market, group.cancel_scope)
    return outcomes


def _raise_for_failures(markets: Sequence[str], outcomes: Sequence[Outcome]) -> None:
    """Raise if the search failed fatally or in every market; log a partial failure.

    Raises:
        StaanMCPError: See :func:`staan_mcp.payloads.raise_for_failures`.
    """
    failed = raise_for_failures(markets, outcomes)
    if failed:
        _logger.warning(
            "web_search: %d of %d markets failed: %s",
            len(failed),
            len(markets),
            ", ".join(failed),
        )


# --------------------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------------------


async def web_search(  # noqa: PLR0913  (the tool schema is the contract)
    query: Annotated[
        str,
        Field(min_length=1, max_length=MAX_SEARCH_QUERY_LENGTH, description=SEARCH_QUERY_PARAM),
    ],
    *,
    market: Annotated[str | list[str], Field(description=MARKET_PARAM)] = DEFAULT_MARKET,
    full_content: Annotated[
        Literal["none", "markdown", "html"], Field(description=FULL_CONTENT_PARAM)
    ] = "none",
    offset: Annotated[Literal[0, 10, 20, 30], Field(description=OFFSET_PARAM)] = 0,
    include_domains: Annotated[list[str] | None, Field(description=INCLUDE_PARAM)] = None,
    exclude_domains: Annotated[list[str] | None, Field(description=EXCLUDE_PARAM)] = None,
    simple_response: Annotated[bool, Field(description=SEARCH_SIMPLE_PARAM)] = True,
    ctx: Context[_AppState],
) -> CallToolResult:
    """Search the web with Staan; see the tool description registered in ``create_server``."""
    with _tool_errors():
        markets = expand_markets(market)
        include, exclude = normalize_domain_filters(include_domains, exclude_domains)
        state = _state(ctx)

        async def search(single_market: str) -> dict[str, Any]:
            return await state.client.search_web(
                query=query,
                market=single_market,
                offset=offset,
                full_content=None if full_content == "none" else full_content,
                include_domains=include,
                exclude_domains=exclude,
            )

        outcomes = await _fetch_markets(search, markets)
        _raise_for_failures(markets, outcomes)
        payload = search_payload(
            markets,
            outcomes,
            simple_response=simple_response,
            max_content_chars=state.max_content_chars,
        )
        return _json_result(payload)


async def answer(  # noqa: PLR0913  (the tool schema is the contract)
    query: Annotated[
        str,
        Field(min_length=1, max_length=MAX_ANSWER_QUERY_LENGTH, description=ANSWER_QUERY_PARAM),
    ],
    *,
    filter: Annotated[str | None, Field(description=FILTER_PARAM)] = None,  # noqa: A002  # pylint: disable=redefined-builtin
    language: Annotated[Literal["en", "fr"], Field(description=LANGUAGE_PARAM)] = "en",
    mode: Annotated[Literal["short", "long"], Field(description=MODE_PARAM)] = "short",
    sources: Annotated[
        list[AnswerSource] | None,
        Field(max_length=MAX_ANSWER_SOURCES, description=SOURCES_PARAM),
    ] = None,
    sources_mode: Annotated[
        Literal["search", "context"], Field(description=SOURCES_MODE_PARAM)
    ] = "search",
    simple_response: Annotated[bool, Field(description=ANSWER_SIMPLE_PARAM)] = True,
    ctx: Context[_AppState],
) -> Annotated[CallToolResult, AnswerOutput]:
    """Ask Staan for a grounded answer; see the tool description registered in ``create_server``."""
    with _tool_errors():
        domain = normalize_domain(filter) if filter and filter.strip() else None
        response = await _state(ctx).client.answer(
            query=query,
            language=language,
            mode=mode,
            filter_domain=domain,
            sources=[s.model_dump(exclude_none=True) for s in sources] if sources else None,
            sources_mode=sources_mode,
        )
        return _structured_result(answer_payload(response, simple_response=simple_response))


# --------------------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------------------


def create_server(
    settings: Settings,
    *,
    auth: AuthConfig | None = None,
    client_factory: Callable[[Settings], StaanClient] = StaanClient,
) -> MCPServer[_AppState]:
    """Build the MCP server.

    Args:
        settings: Upstream API settings. A missing API key is allowed; the tools then report it.
        auth: Optional OAuth resource-server gate. It only takes effect on the HTTP transports.
        client_factory: Creates the shared :class:`StaanClient`; replaced in tests.

    Returns:
        A server with the ``web_search`` and ``answer`` tools registered.

    Raises:
        ConfigurationError: If ``auth`` is inconsistent (see :func:`build_token_verifier`).
    """
    verifier = None if auth is None else build_token_verifier(auth)

    @asynccontextmanager
    async def lifespan(_server: MCPServer[_AppState]) -> AsyncIterator[_AppState]:
        async with AsyncExitStack() as stack:
            if isinstance(verifier, IntrospectionTokenVerifier):
                stack.push_async_callback(verifier.aclose)
            client = await stack.enter_async_context(client_factory(settings))
            yield _AppState(client, settings.max_content_chars)

    options: dict[str, Any] = {}
    if auth is not None:
        options = {"token_verifier": verifier, "auth": build_auth_settings(auth)}
    server = MCPServer[_AppState](
        SERVER_NAME,
        instructions=INSTRUCTIONS,
        version=__version__,
        lifespan=lifespan,
        **options,
    )
    server.add_tool(
        web_search,
        title=WEB_SEARCH_TITLE,
        description=WEB_SEARCH_DESCRIPTION,
        annotations=_annotations(idempotent=True),
        structured_output=False,
    )
    server.add_tool(
        answer,
        title=ANSWER_TITLE,
        description=ANSWER_DESCRIPTION,
        annotations=_annotations(idempotent=False),
    )
    return server
