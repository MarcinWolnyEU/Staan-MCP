"""The Hermes plugin: what it registers, and what its handlers do against a fake Staan."""
# pylint: disable=missing-function-docstring,protected-access,redefined-outer-name,unused-argument

from __future__ import annotations

import copy
import json
import sys
import types
from typing import TYPE_CHECKING, Any

import pytest
from hermes_helpers import RecordingContext
from local_staan import Reply, json_reply
from stdio_helpers import API_KEY, OK_SEARCH

from hermes import tools
from staan_mcp.config import Settings
from staan_mcp.contract import TOOL_LIST

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from local_staan import LocalStaan

ENV_NAMES = tools._ENV_NAMES


@pytest.fixture
def hermes_tools() -> dict[str, dict[str, Any]]:
    context = RecordingContext()
    tools.register(context)
    return context.registered


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def configured(local_staan: LocalStaan, monkeypatch: pytest.MonkeyPatch) -> LocalStaan:
    monkeypatch.setenv("STAAN_API_KEY", API_KEY)
    monkeypatch.setenv("STAAN_BASE_URL", local_staan.base_url)
    monkeypatch.setenv("STAAN_MAX_RETRIES", "0")
    return local_staan


def call(registered: dict[str, dict[str, Any]], name: str, args: dict[str, Any]) -> Any:
    """Call a registered handler the way Hermes does (with context keywords) and decode it."""
    return json.loads(registered[name]["handler"](args, task_id="t1", session_id="s1"))


def walk(node: Any) -> Iterator[Any]:
    """Every JSON value inside ``node``."""
    yield node
    children = node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()
    for child in children:
        yield from walk(child)


def test_both_tools_are_registered_under_names_that_do_not_take_hermes_web_search(
    hermes_tools: dict[str, dict[str, Any]],
) -> None:
    assert set(hermes_tools) == {"staan_web_search", "staan_answer"}
    assert {fields["toolset"] for fields in hermes_tools.values()} == {"staan"}
    for name, fields in hermes_tools.items():
        assert fields["schema"]["name"] == name
        assert fields["requires_env"] == ["STAAN_API_KEY"]


def test_the_answer_description_points_at_the_search_tool_by_its_hermes_name(
    hermes_tools: dict[str, dict[str, Any]],
) -> None:
    description = hermes_tools["staan_answer"]["schema"]["description"]

    assert "staan_web_search" in description
    assert " web_search" not in description


def test_the_schemas_hold_no_null_branch_title_text_or_reference(
    hermes_tools: dict[str, dict[str, Any]],
) -> None:
    for fields in hermes_tools.values():
        nodes = list(walk(fields["schema"]["parameters"]))
        dicts = [node for node in nodes if isinstance(node, dict)]

        assert {"type": "null"} not in nodes
        assert not any("$ref" in node or "$defs" in node for node in dicts)
        assert not any(isinstance(node.get("title"), str) for node in dicts)
        assert not any(node.get("default") is None for node in dicts if "default" in node)


def test_optional_arguments_stay_optional_and_keep_their_shape(
    hermes_tools: dict[str, dict[str, Any]],
) -> None:
    search = hermes_tools["staan_web_search"]["schema"]["parameters"]
    answer = hermes_tools["staan_answer"]["schema"]["parameters"]

    assert search["required"] == ["query"]
    assert search["properties"]["include_domains"]["type"] == "array"
    assert [branch["type"] for branch in search["properties"]["market"]["anyOf"]] == [
        "string",
        "array",
    ]
    assert search["properties"]["market"]["default"] == "en-gb"
    assert answer["required"] == ["query"]


def test_a_source_of_the_answer_tool_keeps_its_title_property(
    hermes_tools: dict[str, dict[str, Any]],
) -> None:
    item = hermes_tools["staan_answer"]["schema"]["parameters"]["properties"]["sources"]["items"]

    assert set(item["properties"]) == {"url", "title", "content"}
    assert item["properties"]["title"]["type"] == "string"


def test_registering_leaves_the_servers_own_tool_definitions_alone() -> None:
    before = copy.deepcopy(TOOL_LIST)

    tools.register(RecordingContext())

    assert before == TOOL_LIST


def test_a_search_goes_to_staan_and_comes_back_as_json(
    configured: LocalStaan, hermes_tools: dict[str, dict[str, Any]]
) -> None:
    configured.search = json_reply(OK_SEARCH)

    result = call(hermes_tools, "staan_web_search", {"query": "python"})

    assert result == [{"title": "T", "url": "https://a.example", "snippet": "S"}]
    (request,) = configured.requests
    assert request.body["q"] == "python"


def test_an_answer_goes_to_staan_and_comes_back_as_json(
    configured: LocalStaan, hermes_tools: dict[str, dict[str, Any]]
) -> None:
    configured.answer = json_reply({"answer": "Forty-two", "sources": []})

    result = call(hermes_tools, "staan_answer", {"query": "meaning of life"})

    assert "Forty-two" in result["answer"]


def test_a_bad_argument_is_an_error_object_for_the_model(
    configured: LocalStaan, hermes_tools: dict[str, dict[str, Any]]
) -> None:
    result = call(hermes_tools, "staan_web_search", {"query": ""})

    assert set(result) == {"error"}
    assert "query" in result["error"]
    assert not configured.requests


def test_an_upstream_failure_is_an_error_object_for_the_model(
    configured: LocalStaan, hermes_tools: dict[str, dict[str, Any]]
) -> None:
    configured.search = Reply(status=403, body=b'{"message":"Forbidden"}')

    result = call(hermes_tools, "staan_web_search", {"query": "python"})

    assert set(result) == {"error"}
    assert "403" in result["error"]


def test_without_a_key_the_tools_are_hidden_and_a_call_says_what_is_missing(
    hermes_tools: dict[str, dict[str, Any]],
) -> None:
    result = call(hermes_tools, "staan_web_search", {"query": "python"})

    assert hermes_tools["staan_web_search"]["check_fn"]() is False
    assert "STAAN_API_KEY" in result["error"]


def test_a_key_makes_the_tools_available(
    configured: LocalStaan, hermes_tools: dict[str, dict[str, Any]]
) -> None:
    assert configured.base_url
    assert all(fields["check_fn"]() is True for fields in hermes_tools.values())


def test_a_bad_setting_hides_the_tools_and_is_named_in_the_error(
    configured: LocalStaan,  # noqa: ARG001  (sets a key, so the setting is the only fault)
    hermes_tools: dict[str, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STAAN_TIMEOUT", "soon")

    result = call(hermes_tools, "staan_web_search", {"query": "python"})

    assert hermes_tools["staan_web_search"]["check_fn"]() is False
    assert "STAAN_TIMEOUT" in result["error"]


@pytest.fixture
def hermes_secrets(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Hermes' profile-aware ``agent.secret_scope``, with the active profile's secrets."""
    secrets: dict[str, str] = {}
    scope = types.ModuleType("agent.secret_scope")
    scope.get_secret = secrets.get  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.secret_scope", scope)
    return secrets


def test_inside_hermes_the_profiles_key_is_used_and_the_process_environment_is_not(
    local_staan: LocalStaan,
    hermes_secrets: dict[str, str],
    hermes_tools: dict[str, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STAAN_API_KEY", "another-profiles-key")
    hermes_secrets.update({"STAAN_BASE_URL": local_staan.base_url, "STAAN_MAX_RETRIES": "0"})
    check = hermes_tools["staan_web_search"]["check_fn"]
    assert check() is False

    hermes_secrets["STAAN_API_KEY"] = "this-profiles-key-0123456789"
    call(hermes_tools, "staan_web_search", {"query": "python"})

    assert check() is True
    (request,) = local_staan.requests
    assert request.headers["Authorization"] == "Bearer this-profiles-key-0123456789"


def test_calls_made_with_the_same_settings_share_one_rate_limit() -> None:
    settings = Settings(api_key=API_KEY, rate_limit=7)

    first = tools._limiter(settings)

    assert tools._limiter(Settings(api_key=API_KEY, rate_limit=7)) is first
    assert tools._limiter(Settings(api_key="another-key-0123456789", rate_limit=7)) is not first
    assert tools._limiter(Settings(api_key=API_KEY, rate_limit=8)) is not first


def test_the_handler_takes_whatever_context_hermes_adds(
    configured: LocalStaan, hermes_tools: dict[str, dict[str, Any]]
) -> None:
    handler: Callable[..., str] = hermes_tools["staan_web_search"]["handler"]

    assert json.loads(handler({"query": "python"}, task_id="t", parent_agent=object())) == []
    assert len(configured.requests) == 1
