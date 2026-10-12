"""Every kind of line on the wire, sent to ``python -m staan_mcp`` and to ``stdio_server.py``.

Each row of ``WIRE_ROWS`` is one line (valid JSON-RPC or not), sent to a fresh pair of real
processes, with what each of them is expected to write back. Most rows are answered alike by both
(the handshake rules, ``prompts/get``, ``resources/read``, unknown methods, every valid kind of
``id``, ...). The rows that are not are the deliberate differences that the docstring of
``test_stdio_parity.py`` lists: the SDK drops a line it cannot read as a JSON-RPC message, or
validates the optional members of ``params`` more deeply, and the standalone server answers or
tolerates it. A difference only passes here if both sides give exactly the answer written down.
"""
# pylint: disable=redefined-outer-name

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from rpc_process import INITIALIZE, RpcProcess, clean_environment, initialize
from staan_fakes import API_KEY

from staan_mcp import stdio_server
from staan_mcp.contract import INSTRUCTIONS, TOOL_LIST

STANDALONE = [sys.executable, str(Path(stdio_server.__file__))]
PACKAGE = [sys.executable, "-m", "staan_mcp"]


@dataclass(frozen=True)
class Wire:
    """One line sent to both real processes, and what each is expected to write back.

    ``standalone`` is only given for a deliberate difference. Without it the two must answer
    alike (and as ``package`` says, when that is given too).
    """

    label: str
    line: bytes
    package: list[dict[str, Any]] | None = None
    standalone: list[dict[str, Any]] | None = None


def line(message: object) -> bytes:
    """``message`` as the JSON text of one line."""
    return json.dumps(message).encode()


def answer(result: object, request_id: object = 3) -> dict[str, Any]:
    """A success response."""
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def failure(code: int, message: str, data: object = "", request_id: object = 3) -> dict[str, Any]:
    """An error response, with the ``data`` member both servers send."""
    error = {"code": code, "message": message, "data": data}
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def not_found(method: str) -> dict[str, Any]:
    """The answer to a method nobody serves."""
    return failure(-32601, "Method not found", method)


def unknown_prompt(name: str) -> dict[str, Any]:
    """The answer to ``prompts/get`` for a prompt that does not exist: no ``data`` member."""
    return {"jsonrpc": "2.0", "id": 3, "error": {"code": 0, "message": f"Unknown prompt: {name}"}}


def unknown_resource(uri: str) -> dict[str, Any]:
    """The answer to ``resources/read`` for a resource that does not exist."""
    return failure(-32602, f"Unknown resource: {uri}", {"uri": uri})


def initialized(version: str = "2025-06-18") -> dict[str, Any]:
    """The answer to an acceptable ``initialize``."""
    capabilities = {
        "prompts": {"listChanged": False},
        "resources": {"listChanged": False, "subscribe": False},
        "tools": {"listChanged": False},
    }
    return answer(
        {
            "protocolVersion": version,
            "capabilities": capabilities,
            "serverInfo": {"name": "staan", "version": stdio_server.VERSION},
            "instructions": INSTRUCTIONS,
        }
    )


def method_call(method: str, params: object = None, request_id: object = 3) -> bytes:
    """A request line; ``params`` is left out when ``None``."""
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return line(message)


def stdio_message(**members: Any) -> bytes:
    """A JSON-RPC 2.0 message line with exactly ``members`` besides ``jsonrpc``."""
    return line({"jsonrpc": "2.0", **members})


NO_ANSWER: list[dict[str, Any]] = []
PONG = answer({})
INVALID_PARAMS = failure(-32602, "Invalid request parameters")
PARSE_ERROR = failure(-32700, "Parse error: the message is not valid JSON.", request_id=None)
BATCH = failure(-32600, "Batch requests are not supported.", request_id=None)
NOT_AN_OBJECT = failure(-32600, "Invalid request: expected a JSON object.", request_id=None)
BAD_ID = failure(-32600, "Invalid request: 'id' must be a string or an integer.", request_id=None)
NO_METHOD = failure(-32600, "Invalid request: 'method' must be a string.")
UNKNOWN_TOOL = answer(
    {"content": [{"type": "text", "text": "Unknown tool: nope"}], "isError": True}
)
GOOD_INITIALIZE: dict[str, Any] = INITIALIZE["params"]

# Members of requests that the tests below send.
UNKNOWN_PROMPT_PARAMS: list[tuple[str, dict[str, Any]]] = [
    ("with a name", {"name": "x"}),
    ("with an empty name", {"name": ""}),
    ("with arguments", {"name": "x", "arguments": {"a": "b"}}),
    ("with null arguments", {"name": "x", "arguments": None}),
    ("with a name that is not ASCII", {"name": "café \u2603"}),
    ("with members it does not know", {"name": "x", "_meta": {"progressToken": 1}, "z": 1}),
]
BAD_IDS: list[tuple[str, Any]] = [
    ("null", None),
    ("1.5", 1.5),
    ("2.0", 2.0),
    ("true", True),
    ("an array", [1]),
    ("an object", {}),
]
# What an ``initialize`` may get wrong inside, which the package rejects and the standalone
# server does not look at.
STANDALONE_ONLY_INITIALIZE: list[tuple[str, dict[str, Any]]] = [
    ("with capabilities wrong inside", {"capabilities": {"roots": {"listChanged": "nope"}}}),
    (
        "with a clientInfo title that is a number",
        {"clientInfo": {"name": "t", "version": "1", "title": 5}},
    ),
    ("with _meta that is a number", {"_meta": 5}),
]

# Lines both servers answer in the same words (or both ignore), after the handshake.
SAME_AFTER_HANDSHAKE = [
    Wire("an empty object", b"{}", NO_ANSWER),
    Wire("a blank line", b"", NO_ANSWER),
    Wire("a line of spaces", b"   ", NO_ANSWER),
    Wire("a response from the client", stdio_message(id=3, result={}), NO_ANSWER),
    Wire("an error from the client", stdio_message(id=3, error={"code": 1}), NO_ANSWER),
    Wire("ping as a notification", stdio_message(method="ping"), NO_ANSWER),
    Wire("tools/list as a notification", stdio_message(method="tools/list"), NO_ANSWER),
    Wire("an unknown notification", stdio_message(method="notifications/bogus"), NO_ANSWER),
    Wire(
        "cancelling a request nobody made",
        stdio_message(method="notifications/cancelled", params={"requestId": 99}),
        NO_ANSWER,
    ),
    Wire("a string id", method_call("ping", request_id="abc"), [answer({}, "abc")]),
    Wire("id 0", method_call("ping", request_id=0), [answer({}, 0)]),
    Wire("a negative id", method_call("ping", request_id=-1), [answer({}, -1)]),
    Wire("an empty string id", method_call("ping", request_id=""), [answer({}, "")]),
    Wire("an id beyond 64 bits", method_call("ping", request_id=10**30), [answer({}, 10**30)]),
    Wire(
        "duplicate keys, the last one wins",
        b'{"jsonrpc":"2.0","id":3,"id":4,"method":"ping"}',
        [answer({}, 4)],
    ),
    Wire("a carriage return before the newline", method_call("ping") + b"\r", [PONG]),
    Wire("ping with an object as params", method_call("ping", {"a": 1}), [PONG]),
    Wire(
        "NaN inside params",
        b'{"jsonrpc":"2.0","id":3,"method":"ping","params":{"x":NaN}}',
        [PONG],
    ),
    Wire(
        "bytes inside a string that are not UTF-8",
        b'{"jsonrpc":"2.0","id":3,"method":"pi\xffng"}',
        [not_found("pi\ufffdng")],
    ),
    Wire("an unknown method", method_call("bogus/method"), [not_found("bogus/method")]),
    Wire("an empty method", method_call(""), [not_found("")]),
    Wire("server/discover", method_call("server/discover"), [not_found("server/discover")]),
    Wire("tasks/get", method_call("tasks/get", {"taskId": "x"}), [not_found("tasks/get")]),
    Wire(
        "resources/subscribe",
        method_call("resources/subscribe", {"uri": "file:///x"}),
        [not_found("resources/subscribe")],
    ),
    Wire(
        "logging/setLevel",
        method_call("logging/setLevel", {"level": "debug"}),
        [not_found("logging/setLevel")],
    ),
    Wire(
        "completion/complete",
        method_call(
            "completion/complete",
            {"ref": {"type": "ref/prompt", "name": "x"}, "argument": {"name": "a", "value": "b"}},
        ),
        [not_found("completion/complete")],
    ),
    Wire(
        "prompts/list with a cursor",
        method_call("prompts/list", {"cursor": "abc"}),
        [answer({"prompts": []})],
    ),
    Wire(
        "resources/list with a null cursor",
        method_call("resources/list", {"cursor": None}),
        [answer({"resources": []})],
    ),
    Wire(
        "resources/templates/list",
        method_call("resources/templates/list"),
        [answer({"resourceTemplates": []})],
    ),
    Wire(
        "tools/list with a cursor",
        method_call("tools/list", {"cursor": "abc"}),
        [answer({"tools": TOOL_LIST})],
    ),
    Wire("tools/call without params", method_call("tools/call"), [INVALID_PARAMS]),
    Wire(
        "tools/call with a name that is not text",
        method_call("tools/call", {"name": 5}),
        [INVALID_PARAMS],
    ),
    Wire(
        "tools/call with arguments that are not an object",
        method_call("tools/call", {"name": "nope", "arguments": []}),
        [INVALID_PARAMS],
    ),
    Wire(
        "tools/call for a tool that does not exist",
        method_call(
            "tools/call", {"name": "nope", "arguments": None, "_meta": {"progressToken": 1}}
        ),
        [UNKNOWN_TOOL],
    ),
    Wire("a second initialize", method_call("initialize", GOOD_INITIALIZE), [initialized()]),
    Wire(
        "initialize with an empty protocol version",
        method_call("initialize", {**GOOD_INITIALIZE, "protocolVersion": ""}),
        [initialized("2025-11-25")],
    ),
    Wire(
        "initialize with more than it needs",
        method_call(
            "initialize",
            {
                **GOOD_INITIALIZE,
                "clientInfo": {"name": "t", "version": "1", "title": "T", "websiteUrl": "x"},
                "_meta": {"progressToken": 1},
                "extra": 1,
            },
        ),
        [initialized()],
    ),
    *[
        Wire(
            f"prompts/get {why}",
            method_call("prompts/get", params),
            [unknown_prompt(str(params["name"]))],
        )
        for why, params in UNKNOWN_PROMPT_PARAMS
    ],
    *[
        Wire(f"prompts/get {why}", method_call("prompts/get", params), [INVALID_PARAMS])
        for why, params in [
            ("with an empty object", {}),
            ("with a null name", {"name": None}),
            ("with a name that is a number", {"name": 5}),
            ("with a name that is a boolean", {"name": True}),
            ("with arguments that are a number", {"name": "x", "arguments": 5}),
            ("with an argument that is a number", {"name": "x", "arguments": {"a": 1}}),
            ("with an argument that is null", {"name": "x", "arguments": {"a": None}}),
        ]
    ],
    *[
        Wire(f"{method} without params", method_call(method), [INVALID_PARAMS])
        for method in ("prompts/get", "resources/read")
    ],
    *[
        Wire(
            f"{method} with null params",
            b'{"jsonrpc":"2.0","id":3,"method":"%s","params":null}' % method.encode(),
            [INVALID_PARAMS],
        )
        for method in ("prompts/get", "resources/read")
    ],
    *[
        Wire(
            f"resources/read of {uri!r}",
            method_call("resources/read", {"uri": uri, "bogus": 1}),
            [unknown_resource(uri)],
        )
        for uri in ("file:///x", "x", "", "   ", "HTTP://EXAMPLE.com", "café://\u2603")
    ],
    *[
        Wire(f"resources/read {why}", method_call("resources/read", params), [INVALID_PARAMS])
        for why, params in [
            ("with an empty object", {}),
            ("with a null uri", {"uri": None}),
            ("with a uri that is a number", {"uri": 5}),
        ]
    ],
]

# Lines the SDK drops without a word (or answers with its own validation error) and the
# standalone server answers or tolerates on purpose; each row gives the two expected answers.
DIFFERENT_AFTER_HANDSHAKE = [
    Wire("text that is not JSON", b"not json", NO_ANSWER, [PARSE_ERROR]),
    Wire("an unfinished object", b"{", NO_ANSWER, [PARSE_ERROR]),
    Wire("bytes that are not UTF-8", b"\xff\xfe", NO_ANSWER, [PARSE_ERROR]),
    Wire("a byte order mark", b"\xef\xbb\xbf" + method_call("ping"), NO_ANSWER, [PARSE_ERROR]),
    Wire("nesting beyond the parser", b"[" * 100_000, NO_ANSWER, [PARSE_ERROR]),
    Wire("an empty batch", b"[]", NO_ANSWER, [BATCH]),
    Wire(
        "a batch of one request",
        line([{"jsonrpc": "2.0", "id": 3, "method": "ping"}]),
        NO_ANSWER,
        [BATCH],
    ),
    *[
        Wire(f"{text.decode()} as a message", text, NO_ANSWER, [NOT_AN_OBJECT])
        for text in (b"5", b'"x"', b"null", b"true")
    ],
    *[
        Wire(f"an id that is {why}", stdio_message(id=value, method="ping"), NO_ANSWER, [BAD_ID])
        for why, value in BAD_IDS
    ],
    Wire("a request without a method", stdio_message(id=3), NO_ANSWER, [NO_METHOD]),
    Wire("a method that is a number", stdio_message(id=3, method=5), NO_ANSWER, [NO_METHOD]),
    Wire("a null method", stdio_message(id=3, method=None), NO_ANSWER, [NO_METHOD]),
    Wire("no jsonrpc member", line({"id": 3, "method": "ping"}), NO_ANSWER, [PONG]),
    Wire("jsonrpc 1.0", line({"jsonrpc": "1.0", "id": 3, "method": "ping"}), NO_ANSWER, [PONG]),
    Wire(
        "jsonrpc 2 as a number", line({"jsonrpc": 2, "id": 3, "method": "ping"}), NO_ANSWER, [PONG]
    ),
    Wire("ping with a string as params", method_call("ping", "x"), NO_ANSWER, [PONG]),
    Wire("ping with an array as params", method_call("ping", [1]), NO_ANSWER, [PONG]),
    Wire(
        "tools/list with a string as params",
        method_call("tools/list", "x"),
        NO_ANSWER,
        [answer({"tools": TOOL_LIST})],
    ),
    Wire(
        "tools/call with an array as params",
        method_call("tools/call", []),
        NO_ANSWER,
        [INVALID_PARAMS],
    ),
    Wire(
        "ping with _meta that is a number",
        method_call("ping", {"_meta": 5}),
        [INVALID_PARAMS],
        [PONG],
    ),
    Wire(
        "tools/list with a cursor that is a number",
        method_call("tools/list", {"cursor": 5}),
        [INVALID_PARAMS],
        [answer({"tools": TOOL_LIST})],
    ),
    Wire(
        "prompts/list with a cursor that is a number",
        method_call("prompts/list", {"cursor": 5}),
        [INVALID_PARAMS],
        [answer({"prompts": []})],
    ),
    Wire(
        "tools/call with _meta that is a number",
        method_call("tools/call", {"name": "nope", "_meta": 5}),
        [INVALID_PARAMS],
        [UNKNOWN_TOOL],
    ),
    Wire(
        "resources/subscribe without params",
        method_call("resources/subscribe"),
        [INVALID_PARAMS],
        [not_found("resources/subscribe")],
    ),
    Wire(
        "logging/setLevel with a bad level",
        method_call("logging/setLevel", {"level": "nope"}),
        [INVALID_PARAMS],
        [not_found("logging/setLevel")],
    ),
    Wire(
        "completion/complete without a reference",
        method_call("completion/complete", {}),
        [INVALID_PARAMS],
        [not_found("completion/complete")],
    ),
]

INVALID_INITIALIZE: list[tuple[str, object]] = [
    ("without params", None),
    ("with params that are an empty object", {}),
    *[
        (f"without {name}", {k: v for k, v in GOOD_INITIALIZE.items() if k != name})
        for name in GOOD_INITIALIZE
    ],
    ("with a null protocol version", {**GOOD_INITIALIZE, "protocolVersion": None}),
    ("with a protocol version that is a number", {**GOOD_INITIALIZE, "protocolVersion": 5}),
    ("with a protocol version that is a boolean", {**GOOD_INITIALIZE, "protocolVersion": True}),
    ("with a protocol version that is a list", {**GOOD_INITIALIZE, "protocolVersion": ["x"]}),
    ("with null capabilities", {**GOOD_INITIALIZE, "capabilities": None}),
    ("with capabilities that are a list", {**GOOD_INITIALIZE, "capabilities": []}),
    ("with a clientInfo that is a list", {**GOOD_INITIALIZE, "clientInfo": []}),
    ("with an empty clientInfo", {**GOOD_INITIALIZE, "clientInfo": {}}),
    ("with a clientInfo without a version", {**GOOD_INITIALIZE, "clientInfo": {"name": "t"}}),
    (
        "with a clientInfo name that is a number",
        {**GOOD_INITIALIZE, "clientInfo": {"name": 5, "version": "1"}},
    ),
    (
        "with a clientInfo version that is a number",
        {**GOOD_INITIALIZE, "clientInfo": {"name": "t", "version": 1}},
    ),
]

# Lines to a server that has had no handshake yet. Both open for a valid ``initialize``, so the
# ones that only the standalone server accepts come last.
SAME_BEFORE_HANDSHAKE = [
    Wire("ping before the handshake", method_call("ping"), [PONG]),
    Wire("an unknown method before it", method_call("bogus/x"), [not_found("bogus/x")]),
    *[
        Wire(f"{method} is refused", method_call(method, params), [INVALID_PARAMS])
        for method, params in [
            ("tools/list", None),
            ("tools/call", {"name": "nope"}),
            ("prompts/list", None),
            ("prompts/get", {"name": "x"}),
            ("resources/list", None),
            ("resources/templates/list", None),
            ("resources/read", {"uri": "file:///x"}),
        ]
    ],
    *[
        Wire(f"initialize {why} is refused", method_call("initialize", params), [INVALID_PARAMS])
        for why, params in INVALID_INITIALIZE
    ],
    Wire("tools/list after those refusals", method_call("tools/list"), [INVALID_PARAMS]),
]
DIFFERENT_BEFORE_HANDSHAKE = [
    Wire(
        "initialize with params that are a list",
        method_call("initialize", []),
        NO_ANSWER,
        [INVALID_PARAMS],
    ),
    *[
        Wire(
            f"initialize {why}",
            method_call("initialize", {**GOOD_INITIALIZE, **changed}),
            [INVALID_PARAMS],
            [initialized()],
        )
        for why, changed in STANDALONE_ONLY_INITIALIZE
    ],
]
WIRE_ROWS = [
    *SAME_AFTER_HANDSHAKE,
    *DIFFERENT_AFTER_HANDSHAKE,
    *SAME_BEFORE_HANDSHAKE,
    *DIFFERENT_BEFORE_HANDSHAKE,
]


def run_wire_session(
    command: list[str], rows: list[Wire], *, handshake: bool
) -> list[list[dict[str, Any]]]:
    """Send each row to a fresh process, after the handshake or not; the answers, row by row."""
    environment = clean_environment({"STAAN_API_KEY": API_KEY})
    with RpcProcess(command, environment) as process:
        if handshake:
            initialize(process)
        # A tool call answers from a worker thread or task: give it time to say so.
        return [
            process.answers_to(row.line, grace=0.3 if b"tools/call" in row.line else 0.05)
            for row in rows
        ]


@pytest.fixture(scope="module")
def wire_answers() -> dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """The ``(package, standalone)`` answers to every row of ``WIRE_ROWS``, by label."""
    sessions = [
        (SAME_AFTER_HANDSHAKE + DIFFERENT_AFTER_HANDSHAKE, True),
        (SAME_BEFORE_HANDSHAKE + DIFFERENT_BEFORE_HANDSHAKE, False),
    ]
    with ThreadPoolExecutor() as pool:
        runs = [
            [
                pool.submit(run_wire_session, command, rows, handshake=handshake)
                for command in (PACKAGE, STANDALONE)
            ]
            for rows, handshake in sessions
        ]
        results = [[future.result() for future in futures] for futures in runs]
    answers = {}
    for (rows, _), (package, standalone) in zip(sessions, results, strict=True):
        for row, expected, actual in zip(rows, package, standalone, strict=True):
            answers[row.label] = (expected, actual)
    return answers


def test_every_wire_row_has_its_own_label() -> None:
    labels = [row.label for row in WIRE_ROWS]

    assert len(labels) == len(set(labels))


@pytest.mark.parametrize("row", WIRE_ROWS, ids=[row.label for row in WIRE_ROWS])
def test_the_two_processes_answer_every_kind_of_line_as_documented(
    wire_answers: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]], row: Wire
) -> None:
    package, standalone = wire_answers[row.label]

    if row.standalone is None:  # not a deliberate difference: both say the same, word for word
        assert standalone == package
        if row.package is not None:
            assert package == row.package
    else:
        assert package == row.package
        assert standalone == row.standalone
        assert standalone != package
