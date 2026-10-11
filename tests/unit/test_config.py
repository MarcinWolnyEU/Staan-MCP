"""Tests for Settings, AuthConfig, HttpConfig and is_loopback."""
# Explicit `== ()` assertions are deliberate: they pin the exact empty-tuple default.
# pylint: disable=use-implicit-booleaness-not-comparison

from __future__ import annotations

import dataclasses
from typing import get_args

import pytest
from hypothesis import given
from hypothesis import strategies as st

from staan_mcp.config import (
    DEFAULT_BASE_URL,
    AuthConfig,
    HttpConfig,
    Settings,
    Transport,
    is_loopback,
)
from staan_mcp.errors import ConfigurationError, StaanMCPError

SECRET_KEY = "sk_test_do_not_leak"
SECRET_CLIENT = "client-secret-do-not-leak"

JWT_ENV = {
    "STAAN_MCP_AUTH_ISSUER_URL": "https://auth.example.com",
    "STAAN_MCP_AUTH_RESOURCE_URL": "https://mcp.example.com/mcp",
    "STAAN_MCP_AUTH_JWKS_URL": "https://auth.example.com/jwks.json",
}
INTROSPECTION_ENV = {
    "STAAN_MCP_AUTH_ISSUER_URL": "https://auth.example.com",
    "STAAN_MCP_AUTH_RESOURCE_URL": "https://mcp.example.com/mcp",
    "STAAN_MCP_AUTH_INTROSPECTION_URL": "https://auth.example.com/introspect",
    "STAAN_MCP_AUTH_CLIENT_ID": "mcp-server",
    "STAAN_MCP_AUTH_CLIENT_SECRET": SECRET_CLIENT,
}


# --------------------------------------------------------------------------- is_loopback
@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST",
        " localhost ",
        "127.0.0.1",
        "127.255.255.254",
        "::1",
        "[::1]",
        "0:0:0:0:0:0:0:1",
    ],
)
def test_is_loopback_true(host: str) -> None:
    assert is_loopback(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "",
        "0.0.0.0",  # noqa: S104 - checking that the wildcard address is NOT loopback
        "::",
        "128.0.0.1",
        "10.0.0.1",
        "192.168.1.5",
        "example.com",
        "localhost.example.com",
        "127.0.0.1.example.com",
        "2001:db8::1",
        "localhostX",
        "XLOCALHOST",
        "127.0.0.1X",
        "X[::1]X",
    ],
)
def test_is_loopback_false(host: str) -> None:
    assert is_loopback(host) is False


@given(st.integers(min_value=0, max_value=2**24 - 1))
def test_is_loopback_whole_127_slash_8(suffix: int) -> None:
    host = f"127.{suffix >> 16}.{(suffix >> 8) & 255}.{suffix & 255}"
    assert is_loopback(host)


# --------------------------------------------------------------------------- Settings
def test_settings_defaults() -> None:
    settings = Settings(api_key=None)
    assert settings.api_key is None
    assert settings.base_url == DEFAULT_BASE_URL == "https://api.staan.ai/v2"
    assert settings.timeout == 30.0
    assert settings.max_retries == 2


def test_settings_is_frozen_and_slotted() -> None:
    settings = Settings(api_key="k")
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.timeout = 1.0  # type: ignore[misc]
    assert not hasattr(settings, "__dict__")


def test_settings_repr_hides_the_key() -> None:
    settings = Settings(api_key=SECRET_KEY)
    assert SECRET_KEY not in repr(settings)
    assert SECRET_KEY not in str(settings)
    assert "api_key" not in repr(settings)
    assert "base_url=" in repr(settings)


def test_from_env_empty_environment() -> None:
    assert Settings.from_env({}) == Settings(api_key=None)


def test_from_env_reads_process_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAAN_API_KEY", "from-process-env")
    monkeypatch.setenv("STAAN_TIMEOUT", "12")
    settings = Settings.from_env()
    assert settings.api_key == "from-process-env"
    assert settings.timeout == 12.0


def test_from_env_explicit_mapping_ignores_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STAAN_API_KEY", "from-process-env")
    assert Settings.from_env({}).api_key is None


def test_from_env_all_values() -> None:
    settings = Settings.from_env(
        {
            "STAAN_API_KEY": SECRET_KEY,
            "STAAN_BASE_URL": "https://staging.example.com/v2",
            "STAAN_TIMEOUT": "7.5",
            "STAAN_MAX_RETRIES": "5",
        }
    )
    assert settings == Settings(
        api_key=SECRET_KEY,
        base_url="https://staging.example.com/v2",
        timeout=7.5,
        max_retries=5,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(f"  {SECRET_KEY}\n", SECRET_KEY), ("", None), ("   ", None), ("\n\t", None)],
)
def test_from_env_api_key_is_stripped_and_blank_means_none(raw: str, expected: str | None) -> None:
    assert Settings.from_env({"STAAN_API_KEY": raw}).api_key == expected


@pytest.mark.parametrize("raw", ["", "  "])
def test_from_env_blank_optional_values_use_defaults(raw: str) -> None:
    settings = Settings.from_env(
        {"STAAN_BASE_URL": raw, "STAAN_TIMEOUT": raw, "STAAN_MAX_RETRIES": raw}
    )
    assert settings == Settings(api_key=None)


def test_from_env_base_url_trailing_slash_is_removed() -> None:
    assert Settings.from_env({"STAAN_BASE_URL": "https://x.example.com/v2/"}).base_url == (
        "https://x.example.com/v2"
    )


def test_from_env_base_url_value_is_stripped() -> None:
    assert Settings.from_env({"STAAN_BASE_URL": "  https://x.example.com  "}).base_url == (
        "https://x.example.com"
    )


def test_from_env_base_url_only_loses_slashes_from_its_end() -> None:
    assert Settings.from_env({"STAAN_BASE_URL": "https://x.example.com/vX//"}).base_url == (
        "https://x.example.com/vX"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080/v2",
        "http://127.0.0.1:9000",
        "http://[::1]:9000/v2",
        "https://api.example.com",
    ],
)
def test_from_env_base_url_accepted(url: str) -> None:
    assert Settings.from_env({"STAAN_BASE_URL": url}).base_url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://api.staan.ai/v2",
        "http://example.com",
        "ftp://localhost/v2",
        "api.staan.ai/v2",
        "https://",
        "//api.staan.ai",
        "http://[::1",
    ],
)
def test_from_env_base_url_rejected(url: str) -> None:
    with pytest.raises(ConfigurationError, match="STAAN_BASE_URL"):
        Settings.from_env({"STAAN_BASE_URL": url})


def test_from_env_base_url_rejection_mentions_https() -> None:
    with pytest.raises(ConfigurationError, match="must use https unless the host is loopback"):
        Settings.from_env({"STAAN_BASE_URL": "http://example.com"})


@pytest.mark.parametrize(
    ("raw", "expected"), [("1", 1.0), ("0.5", 0.5), ("45", 45.0), ("1e1", 10.0)]
)
def test_from_env_timeout_valid(raw: str, expected: float) -> None:
    assert Settings.from_env({"STAAN_TIMEOUT": raw}).timeout == expected


@pytest.mark.parametrize("raw", ["abc", "0", "-1", "-0.5", "nan", "inf", "-inf", "1,5"])
def test_from_env_timeout_invalid(raw: str) -> None:
    with pytest.raises(ConfigurationError, match="STAAN_TIMEOUT"):
        Settings.from_env({"STAAN_TIMEOUT": raw})


@pytest.mark.parametrize(("raw", "expected"), [("0", 0), ("1", 1), ("10", 10), (" 3 ", 3)])
def test_from_env_max_retries_valid(raw: str, expected: int) -> None:
    assert Settings.from_env({"STAAN_MAX_RETRIES": raw}).max_retries == expected


@pytest.mark.parametrize("raw", ["-1", "11", "100", "two", "2.5", "1e1"])
def test_from_env_max_retries_invalid(raw: str) -> None:
    with pytest.raises(ConfigurationError, match="STAAN_MAX_RETRIES"):
        Settings.from_env({"STAAN_MAX_RETRIES": raw})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20", 20.0),
        ("5", 5.0),
        ("0.5", 0.5),
        ("2.5", 2.5),
        (" 100 ", 100.0),
        ("1e1", 10.0),
        ("0", 0.0),
    ],
)
def test_from_env_rate_limit_valid(raw: str, expected: float) -> None:
    assert Settings.from_env({"STAAN_RATE_LIMIT": raw}).rate_limit == expected


def test_from_env_rate_limit_defaults_to_twenty_a_second() -> None:
    assert Settings.from_env({}).rate_limit == 20.0
    assert Settings.from_env({"STAAN_RATE_LIMIT": "  "}).rate_limit == 20.0


NEGATIVE = (
    "STAAN_RATE_LIMIT must be a finite number of requests per second, 0 (no limit) or greater"
)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("fast", "STAAN_RATE_LIMIT must be a number of requests per second, got 'fast'."),
        ("1,5", "STAAN_RATE_LIMIT must be a number of requests per second, got '1,5'."),
        ("-1", f"{NEGATIVE}, got '-1'."),
        ("nan", f"{NEGATIVE}, got 'nan'."),
        ("inf", f"{NEGATIVE}, got 'inf'."),
    ],
)
def test_from_env_rate_limit_invalid(raw: str, message: str) -> None:
    with pytest.raises(ConfigurationError) as info:
        Settings.from_env({"STAAN_RATE_LIMIT": raw})
    assert str(info.value) == message


def test_invalid_settings_errors_never_contain_the_api_key() -> None:
    with pytest.raises(ConfigurationError) as info:
        Settings.from_env({"STAAN_API_KEY": SECRET_KEY, "STAAN_TIMEOUT": "nope"})
    assert SECRET_KEY not in str(info.value)


def test_configuration_error_is_a_staan_mcp_error() -> None:
    with pytest.raises(StaanMCPError):
        Settings.from_env({"STAAN_TIMEOUT": "0"})


# --------------------------------------------------------------------------- AuthConfig
def test_auth_from_env_none_when_nothing_is_set() -> None:
    assert AuthConfig.from_env({}) is None


def test_auth_from_env_ignores_unrelated_and_blank_variables() -> None:
    env = {"STAAN_API_KEY": SECRET_KEY, "STAAN_MCP_AUTH_ISSUER_URL": "  ", "PATH": "/bin"}
    assert AuthConfig.from_env(env) is None


def test_auth_from_env_reads_process_environment_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in JWT_ENV.items():
        monkeypatch.setenv(name, value)
    config = AuthConfig.from_env()
    assert config is not None
    assert config.jwks_url == "https://auth.example.com/jwks.json"


def test_auth_from_env_jwt_defaults() -> None:
    assert AuthConfig.from_env(JWT_ENV) == AuthConfig(
        issuer_url="https://auth.example.com",
        resource_url="https://mcp.example.com/mcp",
        jwks_url="https://auth.example.com/jwks.json",
    )


def test_auth_config_defaults() -> None:
    config = AuthConfig(issuer_url="https://i", resource_url="https://r")
    assert config.required_scopes == ()
    assert config.jwks_url is None
    assert config.introspection_url is None
    assert config.client_id is None
    assert config.client_secret is None
    assert config.algorithms == ("RS256", "ES256")


def test_auth_from_env_introspection() -> None:
    config = AuthConfig.from_env(INTROSPECTION_ENV)
    assert config == AuthConfig(
        issuer_url="https://auth.example.com",
        resource_url="https://mcp.example.com/mcp",
        introspection_url="https://auth.example.com/introspect",
        client_id="mcp-server",
        client_secret=SECRET_CLIENT,
    )


def test_auth_repr_hides_the_client_secret() -> None:
    config = AuthConfig.from_env(INTROSPECTION_ENV)
    assert config is not None
    assert SECRET_CLIENT not in repr(config)
    assert "client_secret" not in repr(config)
    assert "client_id='mcp-server'" in repr(config)


def test_auth_config_is_frozen_and_slotted() -> None:
    config = AuthConfig(issuer_url="https://i", resource_url="https://r")
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.issuer_url = "https://other"  # type: ignore[misc]
    assert not hasattr(config, "__dict__")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mcp:search mcp:answer", ("mcp:search", "mcp:answer")),
        ("mcp:search,mcp:answer", ("mcp:search", "mcp:answer")),
        (" a , b  c,, d ", ("a", "b", "c", "d")),
        ("single", ("single",)),
        ("a\tb\nc", ("a", "b", "c")),
    ],
)
def test_auth_scopes_split_on_spaces_and_commas(raw: str, expected: tuple[str, ...]) -> None:
    config = AuthConfig.from_env({**JWT_ENV, "STAAN_MCP_AUTH_SCOPES": raw})
    assert config is not None
    assert config.required_scopes == expected


def test_auth_blank_scopes_mean_no_scopes() -> None:
    config = AuthConfig.from_env({**JWT_ENV, "STAAN_MCP_AUTH_SCOPES": " , "})
    assert config is not None
    assert config.required_scopes == ()


def test_auth_algorithms_override() -> None:
    config = AuthConfig.from_env({**JWT_ENV, "STAAN_MCP_AUTH_ALGORITHMS": "PS256, ES384"})
    assert config is not None
    assert config.algorithms == ("PS256", "ES384")


@pytest.mark.parametrize("raw", ["none", "NONE", "RS256 none", "RS256,None", "nOnE"])
def test_auth_algorithms_never_allow_none(raw: str) -> None:
    with pytest.raises(ConfigurationError, match="'none' algorithm"):
        AuthConfig.from_env({**JWT_ENV, "STAAN_MCP_AUTH_ALGORITHMS": raw})


def test_auth_algorithms_alone_count_as_partial_config() -> None:
    with pytest.raises(ConfigurationError, match="STAAN_MCP_AUTH_ISSUER_URL"):
        AuthConfig.from_env({"STAAN_MCP_AUTH_ALGORITHMS": "RS256"})


@pytest.mark.parametrize("missing", ["STAAN_MCP_AUTH_ISSUER_URL", "STAAN_MCP_AUTH_RESOURCE_URL"])
def test_auth_issuer_and_resource_are_required(missing: str) -> None:
    env = {k: v for k, v in JWT_ENV.items() if k != missing}
    with pytest.raises(ConfigurationError, match=missing):
        AuthConfig.from_env(env)


def test_auth_blank_required_value_counts_as_missing() -> None:
    with pytest.raises(ConfigurationError, match="STAAN_MCP_AUTH_ISSUER_URL"):
        AuthConfig.from_env({**JWT_ENV, "STAAN_MCP_AUTH_ISSUER_URL": "   "})


def test_auth_needs_a_token_source() -> None:
    env = {k: v for k, v in JWT_ENV.items() if k != "STAAN_MCP_AUTH_JWKS_URL"}
    with pytest.raises(ConfigurationError, match="exactly one of"):
        AuthConfig.from_env(env)


def test_auth_rejects_both_token_sources() -> None:
    env = {**INTROSPECTION_ENV, "STAAN_MCP_AUTH_JWKS_URL": "https://auth.example.com/jwks"}
    with pytest.raises(ConfigurationError, match="exactly one of"):
        AuthConfig.from_env(env)


@pytest.mark.parametrize("missing", ["STAAN_MCP_AUTH_CLIENT_ID", "STAAN_MCP_AUTH_CLIENT_SECRET"])
def test_auth_introspection_needs_client_credentials(missing: str) -> None:
    env = {k: v for k, v in INTROSPECTION_ENV.items() if k != missing}
    with pytest.raises(ConfigurationError, match="requires both"):
        AuthConfig.from_env(env)


def test_auth_jwt_mode_does_not_need_client_credentials() -> None:
    config = AuthConfig.from_env(JWT_ENV)
    assert config is not None
    assert config.client_id is None
    assert config.client_secret is None


def test_auth_jwt_mode_keeps_optional_client_credentials() -> None:
    env = {
        **JWT_ENV,
        "STAAN_MCP_AUTH_CLIENT_ID": " cid ",
        "STAAN_MCP_AUTH_CLIENT_SECRET": " sec ",
    }
    config = AuthConfig.from_env(env)
    assert config is not None
    assert (config.client_id, config.client_secret) == ("cid", "sec")


@pytest.mark.parametrize(
    "name",
    [
        "STAAN_MCP_AUTH_ISSUER_URL",
        "STAAN_MCP_AUTH_RESOURCE_URL",
        "STAAN_MCP_AUTH_JWKS_URL",
    ],
)
def test_auth_urls_must_be_https(name: str) -> None:
    with pytest.raises(ConfigurationError, match=name):
        AuthConfig.from_env({**JWT_ENV, name: "http://auth.example.com/x"})


def test_auth_introspection_url_must_be_https() -> None:
    env = {**INTROSPECTION_ENV, "STAAN_MCP_AUTH_INTROSPECTION_URL": "http://auth.example.com/i"}
    with pytest.raises(ConfigurationError, match="STAAN_MCP_AUTH_INTROSPECTION_URL"):
        AuthConfig.from_env(env)


def test_auth_loopback_http_is_allowed() -> None:
    env = {
        "STAAN_MCP_AUTH_ISSUER_URL": "http://localhost:9000",
        "STAAN_MCP_AUTH_RESOURCE_URL": "http://127.0.0.1:8000/mcp",
        "STAAN_MCP_AUTH_JWKS_URL": "http://[::1]:9000/jwks",
    }
    config = AuthConfig.from_env(env)
    assert config is not None
    assert config.resource_url == "http://127.0.0.1:8000/mcp"


def test_auth_error_messages_never_contain_secrets() -> None:
    env = {**INTROSPECTION_ENV, "STAAN_MCP_AUTH_INTROSPECTION_URL": "http://auth.example.com/i"}
    with pytest.raises(ConfigurationError) as info:
        AuthConfig.from_env(env)
    assert SECRET_CLIENT not in str(info.value)


# --------------------------------------------------------------------------- HttpConfig
def test_http_config_defaults() -> None:
    config = HttpConfig()
    assert config.host == "127.0.0.1"
    assert config.port == 8000
    assert config.path == "/mcp"
    assert config.allowed_hosts == ()
    assert config.allowed_origins == ()
    assert config.insecure_no_auth is False


def test_http_config_is_frozen_and_slotted() -> None:
    config = HttpConfig(port=9000, allowed_hosts=("a.example.com",))
    assert config.port == 9000
    assert config.allowed_hosts == ("a.example.com",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.port = 1  # type: ignore[misc]
    assert not hasattr(config, "__dict__")


# --------------------------------------------------------------------------- exact messages
def _settings_message(env: dict[str, str]) -> str:
    """Return the ConfigurationError text raised by ``Settings.from_env(env)``."""
    with pytest.raises(ConfigurationError) as info:
        Settings.from_env(env)
    return str(info.value)


def _auth_message(env: dict[str, str]) -> str:
    """Return the ConfigurationError text raised by ``AuthConfig.from_env(env)``."""
    with pytest.raises(ConfigurationError) as info:
        AuthConfig.from_env(env)
    return str(info.value)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (
            {"STAAN_BASE_URL": "api.staan.ai/v2"},
            "STAAN_BASE_URL must be an absolute http(s) URL.",
        ),
        (
            {"STAAN_BASE_URL": "http://example.com"},
            "STAAN_BASE_URL must use https unless the host is loopback.",
        ),
        ({"STAAN_BASE_URL": "http://[::1"}, "STAAN_BASE_URL is not a valid URL."),
        (
            {"STAAN_TIMEOUT": "soon"},
            "STAAN_TIMEOUT must be a number of seconds, got 'soon'.",
        ),
        (
            {"STAAN_TIMEOUT": "0"},
            "STAAN_TIMEOUT must be a finite number greater than 0, got '0'.",
        ),
        (
            {"STAAN_TIMEOUT": "inf"},
            "STAAN_TIMEOUT must be a finite number greater than 0, got 'inf'.",
        ),
        (
            {"STAAN_MAX_RETRIES": "many"},
            "STAAN_MAX_RETRIES must be an integer, got 'many'.",
        ),
        (
            {"STAAN_MAX_RETRIES": "11"},
            "STAAN_MAX_RETRIES must be between 0 and 10, got 11.",
        ),
        (
            {"STAAN_MAX_RETRIES": "-1"},
            "STAAN_MAX_RETRIES must be between 0 and 10, got -1.",
        ),
    ],
)
def test_settings_error_messages(env: dict[str, str], expected: str) -> None:
    assert _settings_message(env) == expected


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (
            {"STAAN_MCP_AUTH_RESOURCE_URL": "https://mcp.example.com/mcp"},
            "STAAN_MCP_AUTH_ISSUER_URL is required when any STAAN_MCP_AUTH_* variable is set.",
        ),
        (
            {"STAAN_MCP_AUTH_ISSUER_URL": "https://auth.example.com"},
            "STAAN_MCP_AUTH_RESOURCE_URL is required when any STAAN_MCP_AUTH_* variable is set.",
        ),
        (
            {k: v for k, v in JWT_ENV.items() if k != "STAAN_MCP_AUTH_JWKS_URL"},
            (
                "Set exactly one of STAAN_MCP_AUTH_JWKS_URL (JWT verification) or "
                "STAAN_MCP_AUTH_INTROSPECTION_URL (token introspection)."
            ),
        ),
        (
            {k: v for k, v in INTROSPECTION_ENV.items() if k != "STAAN_MCP_AUTH_CLIENT_ID"},
            (
                "STAAN_MCP_AUTH_INTROSPECTION_URL requires both STAAN_MCP_AUTH_CLIENT_ID and "
                "STAAN_MCP_AUTH_CLIENT_SECRET."
            ),
        ),
        (
            {**JWT_ENV, "STAAN_MCP_AUTH_ALGORITHMS": "RS256,none"},
            "STAAN_MCP_AUTH_ALGORITHMS must not allow the 'none' algorithm.",
        ),
        (
            {**JWT_ENV, "STAAN_MCP_AUTH_JWKS_URL": "http://auth.example.com/jwks"},
            "STAAN_MCP_AUTH_JWKS_URL must use https unless the host is loopback.",
        ),
    ],
)
def test_auth_error_messages(env: dict[str, str], expected: str) -> None:
    assert _auth_message(env) == expected


def test_configuration_errors_chain_the_parse_failure() -> None:
    with pytest.raises(ConfigurationError) as info:
        Settings.from_env({"STAAN_TIMEOUT": "soon"})
    assert isinstance(info.value.__cause__, ValueError)


def test_transport_type_lists_the_three_transports() -> None:
    assert get_args(Transport) == ("stdio", "http", "stateless")


# --------------------------------------------------------------------------- URL credentials
USERINFO_SECRET = "Sup3rS3cret"


@pytest.mark.parametrize(
    ("variable", "url"),
    [
        ("STAAN_BASE_URL", f"https://user:{USERINFO_SECRET}@api.example.com/v2"),
        ("STAAN_BASE_URL", f"http://user:{USERINFO_SECRET}@api.example.com/v2"),
        ("STAAN_BASE_URL", f"https://{USERINFO_SECRET}@api.example.com/v2"),
        ("STAAN_BASE_URL", f"http://user:{USERINFO_SECRET}@localhost:8080/v2"),
    ],
)
def test_settings_url_rejects_credentials_without_echoing_them(variable: str, url: str) -> None:
    message = _settings_message({variable: url})

    assert message == f"{variable} must not contain credentials (user:password@)."
    assert USERINFO_SECRET not in message


@pytest.mark.parametrize(
    "variable",
    [
        "STAAN_MCP_AUTH_ISSUER_URL",
        "STAAN_MCP_AUTH_RESOURCE_URL",
        "STAAN_MCP_AUTH_JWKS_URL",
    ],
)
@pytest.mark.parametrize("scheme", ["https", "http"])
def test_auth_urls_reject_credentials_without_echoing_them(variable: str, scheme: str) -> None:
    env = {**JWT_ENV, variable: f"{scheme}://svc:{USERINFO_SECRET}@auth.example.com/x"}

    message = _auth_message(env)

    assert variable in message
    assert USERINFO_SECRET not in message
    assert "svc" not in message


@pytest.mark.parametrize(
    "url",
    ["http://example.com:abc/v2", "https://api.staan.ai:abc/v2", "https://api.staan.ai:99999"],
)
def test_settings_url_rejects_a_bad_port_at_startup(url: str) -> None:
    assert _settings_message({"STAAN_BASE_URL": url}) == "STAAN_BASE_URL is not a valid URL."


def test_no_url_error_message_ever_repeats_the_url() -> None:
    for url in ("http://example.com/secret-path", "ftp://x.example/secret-path", "no-scheme"):
        assert "secret-path" not in _settings_message({"STAAN_BASE_URL": url})


# --------------------------------------------------------------------------- API key characters
@pytest.mark.parametrize(
    "key",
    [
        "sk-live_AbC123.xyz~+/=",
        "a" * 200,
        "!" + "z" * 10 + "~",
    ],
)
def test_from_env_api_key_visible_ascii_is_accepted(key: str) -> None:
    assert Settings.from_env({"STAAN_API_KEY": key}).api_key == key


@pytest.mark.parametrize(
    "key",
    [
        "sk-“quoted”",  # smart quotes pasted from a document
        "café-key",
        "has space",
        "tab\tinside",
        "line\nbreak",
        "nul\x00byte",
        "del\x7fchar",
        "emoji-\U0001f511",
    ],
)
def test_from_env_api_key_rejects_header_unsafe_characters_without_echoing_it(key: str) -> None:
    message = _settings_message({"STAAN_API_KEY": key})

    assert message.startswith("STAAN_API_KEY contains characters")
    assert key not in message
    assert "sk-" not in message
    assert "key-" not in message


def test_from_env_api_key_surrounding_whitespace_is_still_stripped() -> None:
    assert Settings.from_env({"STAAN_API_KEY": "  sk-abc \n"}).api_key == "sk-abc"


# --------------------------------------------------------------------------- deadline
def test_settings_defaults_for_deadline_and_content_limit() -> None:
    settings = Settings.from_env({})

    assert settings.deadline == 50.0
    assert settings.max_content_chars == 0


@pytest.mark.parametrize(("raw", "expected"), [("1", 1.0), ("12.5", 12.5), (" 90 ", 90.0)])
def test_from_env_deadline_valid(raw: str, expected: float) -> None:
    assert Settings.from_env({"STAAN_DEADLINE": raw}).deadline == expected


@pytest.mark.parametrize("raw", ["abc", "0", "-1", "nan", "inf"])
def test_from_env_deadline_invalid(raw: str) -> None:
    with pytest.raises(ConfigurationError, match="STAAN_DEADLINE"):
        Settings.from_env({"STAAN_DEADLINE": raw})


def test_deadline_error_messages_are_exact() -> None:
    assert _settings_message({"STAAN_DEADLINE": "soon"}) == (
        "STAAN_DEADLINE must be a number of seconds, got 'soon'."
    )
    assert _settings_message({"STAAN_DEADLINE": "0"}) == (
        "STAAN_DEADLINE must be a finite number greater than 0, got '0'."
    )


# --------------------------------------------------------------------------- content limit
@pytest.mark.parametrize(
    ("raw", "expected"), [("0", 0), ("200", 200), ("8000", 8000), (" 20000 ", 20000)]
)
def test_from_env_max_content_chars_valid(raw: str, expected: int) -> None:
    assert Settings.from_env({"STAAN_MCP_MAX_CONTENT_CHARS": raw}).max_content_chars == expected


@pytest.mark.parametrize("raw", ["-1", "1", "199", "lots", "1.5", "1e4"])
def test_from_env_max_content_chars_invalid(raw: str) -> None:
    with pytest.raises(ConfigurationError, match="STAAN_MCP_MAX_CONTENT_CHARS"):
        Settings.from_env({"STAAN_MCP_MAX_CONTENT_CHARS": raw})


def test_max_content_chars_error_messages_are_exact() -> None:
    assert _settings_message({"STAAN_MCP_MAX_CONTENT_CHARS": "lots"}) == (
        "STAAN_MCP_MAX_CONTENT_CHARS must be an integer, got 'lots'."
    )
    assert _settings_message({"STAAN_MCP_MAX_CONTENT_CHARS": "5"}) == (
        "STAAN_MCP_MAX_CONTENT_CHARS must be 0 (no limit) or at least 200, got 5."
    )
