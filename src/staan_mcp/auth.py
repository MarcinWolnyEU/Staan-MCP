"""Optional OAuth 2.1 resource-server gate for the HTTP transports.

The MCP SDK validates bearer tokens through a :class:`~mcp.server.auth.provider.TokenVerifier`.
This module supplies two of them, plus the factories that build them from
:class:`~staan_mcp.config.AuthConfig`:

* :class:`JWTTokenVerifier` checks signed JWT access tokens against a JWKS endpoint (needs the
  optional ``oauth`` extra, which installs PyJWT).
* :class:`IntrospectionTokenVerifier` asks an RFC 7662 introspection endpoint.

Both verifiers bind a token to this server through its audience, return ``None`` for every
rejected token (they never raise) and never log the token. The inbound token is only ever
checked here. It is never forwarded to the Staan API, as the MCP specification's
token-passthrough ban requires.
"""

from __future__ import annotations

import logging
import math
import re
import time
from typing import TYPE_CHECKING, Any, Final, Protocol
from urllib.parse import urlsplit

import anyio.to_thread
import httpx2
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from staan_mcp.errors import ConfigurationError

if TYPE_CHECKING:
    from mcp.server.auth.provider import TokenVerifier

    from staan_mcp.config import AuthConfig

__all__ = [
    "IntrospectionTokenVerifier",
    "JWTTokenVerifier",
    "OAuthDependencyError",
    "build_auth_settings",
    "build_token_verifier",
]

logger = logging.getLogger(__name__)

INTROSPECTION_TIMEOUT: Final = 10.0
"""Seconds to wait for the introspection endpoint before rejecting the token."""

JWKS_TIMEOUT: Final = 10.0
"""Seconds to wait for the JWKS endpoint (PyJWT's own default is 30)."""

JWKS_COOLDOWN: Final = 30.0
"""Seconds between JWKS refreshes forced by tokens with an unknown ``kid``."""

JWKS_LIFESPAN: Final = 300.0
"""Seconds a fetched JWKS stays cached."""

MAX_INTROSPECTED_TOKEN_LENGTH: Final = 4096
"""Longest bearer token forwarded to the introspection endpoint; longer ones are rejected."""

_TOKEN_CHARACTERS: Final = re.compile(r"[\x21-\x7e]+")

_REQUIRED_JWT_CLAIMS: Final = ["exp", "iss", "aud"]
_CLIENT_ID_CLAIMS: Final = ("client_id", "azp", "sub")
_UNKNOWN_CLIENT_ID: Final = "unknown"
_HTTP_OK: Final = 200


class OAuthDependencyError(ConfigurationError):
    """Raised when JWT verification is requested but PyJWT is not installed."""


class SigningKey(Protocol):  # pylint: disable=too-few-public-methods
    """The part of ``jwt.PyJWK`` the verifier uses."""

    @property
    def key(self) -> Any:  # noqa: ANN401  # PyJWT yields RSA, EC and other key types
        """The public key object (or secret) to verify the signature with."""
        ...


class JWKClient(Protocol):  # pylint: disable=too-few-public-methods
    """The part of ``jwt.PyJWKClient`` the verifier uses (it is synchronous)."""

    def get_signing_key_from_jwt(self, token: str) -> SigningKey:
        """Return the key that signed ``token``, or raise if there is none."""
        ...


# --------------------------------------------------------------------------------------
# Claim helpers shared by both verifiers
# --------------------------------------------------------------------------------------


def _scopes(claims: dict[str, Any]) -> list[str]:
    """Extract scopes from the ``scope`` string, falling back to the ``scp`` claim.

    Args:
        claims: Decoded token claims or an introspection response.

    Returns:
        The scopes, or an empty list if neither claim holds a usable value.
    """
    scope = claims.get("scope")
    if isinstance(scope, str):
        return scope.split()
    scp = claims.get("scp")
    if isinstance(scp, str):
        return scp.split()
    if isinstance(scp, list):
        return [item for item in scp if isinstance(item, str)]
    return []


def _client_id(claims: dict[str, Any]) -> str:
    """Pick the client identifier: ``client_id``, then ``azp``, then ``sub``.

    Args:
        claims: Decoded token claims or an introspection response.

    Returns:
        The first non-empty string found, or ``"unknown"``.
    """
    for name in _CLIENT_ID_CLAIMS:
        value = claims.get(name)
        if isinstance(value, str) and value:
            return value
    return _UNKNOWN_CLIENT_ID


def _expiry(value: object) -> int | None:
    """Convert an ``exp`` claim to whole epoch seconds.

    Args:
        value: The raw claim value.

    Returns:
        The expiry, or ``None`` if the value is not a finite number (booleans are not numbers,
        and JSON's ``Infinity`` and ``NaN`` are not finite).
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return int(value)


def _access_token(token: str, claims: dict[str, Any], config: AuthConfig) -> AccessToken:
    """Build the SDK's :class:`AccessToken` from verified claims.

    The ``sub`` claim and the issuer are passed on so the SDK can tell principals apart (it binds
    a stateful session to ``(client_id, issuer, subject)``). Without them two users of the same
    client would count as one principal.

    Args:
        token: The bearer token that was verified.
        claims: Its verified claims.
        config: The gate configuration; its resource URL is the token's resource and its issuer
            stands in for an ``iss`` the introspection response left out.

    Returns:
        The access token handed back to the SDK's bearer middleware.
    """
    subject = claims.get("sub")
    issuer = claims.get("iss")
    return AccessToken(
        token=token,
        client_id=_client_id(claims),
        scopes=_scopes(claims),
        expires_at=_expiry(claims.get("exp")),
        resource=config.resource_url,
        subject=subject if isinstance(subject, str) and subject else None,
        claims={"iss": issuer if isinstance(issuer, str) and issuer else config.issuer_url},
    )


def _equivalent_urls(url: str) -> frozenset[str]:
    """Return the spellings of ``url`` that name the same resource or issuer.

    The SDK advertises an origin-only URL with a trailing slash (``https://host/``) in the
    protected-resource metadata, so a client may send that spelling as the token audience while
    the operator configured ``https://host``. Both are accepted. A URL with a path is matched
    exactly.
    """
    canonical = str(AnyHttpUrl(url))
    spellings = {url, canonical}
    if urlsplit(canonical).path == "/":
        spellings.add(canonical.removesuffix("/"))
    return frozenset(spellings)


# --------------------------------------------------------------------------------------
# JWT / JWKS
# --------------------------------------------------------------------------------------


class JWTTokenVerifier:  # pylint: disable=too-few-public-methods
    """Verify signed JWT access tokens against a JWKS endpoint.

    The signature algorithm must be in ``config.algorithms`` (never ``none``), and the ``exp``,
    ``iss`` and ``aud`` claims are mandatory. ``aud`` must equal ``config.resource_url`` and
    ``iss`` must equal ``config.issuer_url`` (an origin-only URL may be spelled with or without
    its trailing slash). Unknown ``kid`` values cannot force more than one JWKS fetch per
    cooldown period.
    """

    def __init__(self, config: AuthConfig, *, jwk_client: JWKClient | None = None) -> None:
        """Create the verifier.

        Args:
            config: The gate configuration; ``jwks_url`` is required unless ``jwk_client`` is
                given.
            jwk_client: Object with ``get_signing_key_from_jwt(token)`` returning something with
                a ``key`` attribute. Defaults to a ``jwt.PyJWKClient`` for ``config.jwks_url``.

        Raises:
            OAuthDependencyError: If PyJWT is not installed.
            ConfigurationError: If there is neither a ``jwk_client`` nor a ``jwks_url``.
        """
        try:  # PyJWT is an optional extra, so import it on first use.
            import jwt  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        except ImportError as exc:
            msg = (
                "JWT verification needs PyJWT, which is not installed. "
                "Install it with: pip install 'staan-mcp[oauth]'"
            )
            raise OAuthDependencyError(msg) from exc
        if jwk_client is None:
            if config.jwks_url is None:
                msg = "JWT verification needs a JWKS URL (STAAN_MCP_AUTH_JWKS_URL)."
                raise ConfigurationError(msg)
            # The cooldown stops tokens with an unknown kid from forcing a JWKS fetch each time.
            jwk_client = jwt.PyJWKClient(
                config.jwks_url,
                timeout=JWKS_TIMEOUT,
                cooldown_duration=JWKS_COOLDOWN,
                lifespan=JWKS_LIFESPAN,
            )
        self._config = config
        self._audiences = sorted(_equivalent_urls(config.resource_url))
        self._issuers = sorted(_equivalent_urls(config.issuer_url))
        self._jwk_client = jwk_client
        self._decode = jwt.decode

    async def verify_token(self, token: str) -> AccessToken | None:
        """Validate a bearer token.

        Args:
            token: The raw bearer token from the ``Authorization`` header.

        Returns:
            The access token, or ``None`` if the token is invalid for any reason.
        """
        try:  # Never raise: the SDK treats an exception as a server error, not a rejection.
            claims = await self._claims(token)
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.debug("JWT rejected (%s)", type(exc).__name__)
            return None
        return _access_token(token, claims, self._config)

    async def _claims(self, token: str) -> dict[str, Any]:
        """Look up the signing key off the event loop and decode the token."""
        signing_key = await anyio.to_thread.run_sync(
            self._jwk_client.get_signing_key_from_jwt, token
        )
        claims: dict[str, Any] = self._decode(
            token,
            signing_key.key,
            algorithms=list(self._config.algorithms),
            audience=self._audiences,
            issuer=self._issuers,
            options={"require": _REQUIRED_JWT_CLAIMS},
        )
        return claims


# --------------------------------------------------------------------------------------
# RFC 7662 introspection
# --------------------------------------------------------------------------------------


def _audience_matches(claims: dict[str, Any], resources: frozenset[str]) -> bool:
    """Check that the ``aud`` claim (a string or a list) contains one of ``resources``."""
    audience = claims.get("aud")
    if isinstance(audience, str):
        return audience in resources
    return isinstance(audience, list) and any(
        isinstance(item, str) and item in resources for item in audience
    )


def _is_unexpired(claims: dict[str, Any]) -> bool:
    """Check ``exp`` is in the future; a missing ``exp`` is accepted, an unusable one is not."""
    if "exp" not in claims:
        return True
    expiry = _expiry(claims["exp"])
    return expiry is not None and expiry > time.time()


def _issuer_matches(claims: dict[str, Any], issuers: frozenset[str]) -> bool:
    """Check ``iss`` is one of ``issuers`` when the response carries one."""
    issuer = claims.get("iss", "")
    return "iss" not in claims or (isinstance(issuer, str) and issuer in issuers)


def _plausible_token(token: str) -> bool:
    """Tell whether ``token`` looks like a bearer token worth asking the authorization server about.

    Opaque tokens are visible ASCII without spaces and fit in a few KiB. Anything else is
    rejected locally, so anonymous callers cannot make this server send arbitrary bytes, one
    credentialed request each, to the operator's authorization server.
    """
    return (
        len(token) <= MAX_INTROSPECTED_TOKEN_LENGTH
        and _TOKEN_CHARACTERS.fullmatch(token) is not None
    )


class IntrospectionTokenVerifier:
    """Verify opaque access tokens with an RFC 7662 introspection endpoint.

    The endpoint is called with HTTP Basic client authentication. A token is accepted only if the
    response is ``active``, its ``aud`` contains ``config.resource_url``, it has not expired and
    its ``iss`` (when present) equals ``config.issuer_url``. Tokens that are not short visible
    ASCII are rejected without a request. One HTTP client (and connection pool) serves all
    requests; call :meth:`aclose` on shutdown.
    """

    def __init__(
        self, config: AuthConfig, *, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        """Create the verifier.

        Args:
            config: The gate configuration; ``introspection_url``, ``client_id`` and
                ``client_secret`` are required.
            transport: Optional HTTP transport, used by tests to mock the endpoint.

        Raises:
            ConfigurationError: If the introspection endpoint or client credentials are missing.
        """
        if (
            config.introspection_url is None
            or config.client_id is None
            or config.client_secret is None
        ):
            msg = "Token introspection needs an endpoint URL, a client id and a client secret."
            raise ConfigurationError(msg)
        self._config = config
        self._url = config.introspection_url
        self._audiences = _equivalent_urls(config.resource_url)
        self._issuers = _equivalent_urls(config.issuer_url)
        self._http = httpx2.AsyncClient(
            transport=transport,
            timeout=INTROSPECTION_TIMEOUT,
            auth=httpx2.BasicAuth(config.client_id, config.client_secret),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        """Close the shared HTTP client. Safe to call more than once."""
        await self._http.aclose()

    async def verify_token(self, token: str) -> AccessToken | None:
        """Validate a bearer token.

        Args:
            token: The raw bearer token from the ``Authorization`` header.

        Returns:
            The access token, or ``None`` if the token is invalid for any reason.
        """
        if not _plausible_token(token):
            logger.debug("Token rejected without introspection: unexpected length or characters")
            return None
        try:  # Never raise: the SDK treats an exception as a server error, not a rejection.
            claims = await self._introspect(token)
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.debug("Introspection failed (%s)", type(exc).__name__)
            return None
        if claims is None or not self._acceptable(claims):
            return None
        return _access_token(token, claims, self._config)

    async def _introspect(self, token: str) -> dict[str, Any] | None:
        """POST the token to the endpoint and return the JSON object, if there is one."""
        response = await self._http.post(
            self._url, data={"token": token}, headers={"Accept": "application/json"}
        )
        if response.status_code != _HTTP_OK:
            logger.debug("Introspection endpoint answered HTTP %s", response.status_code)
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None

    def _acceptable(self, claims: dict[str, Any]) -> bool:
        """Apply the activity, audience, expiry and issuer rules to an introspection response."""
        if claims.get("active") is not True:
            logger.debug("Introspection reports the token as inactive")
            return False
        checks = (
            _audience_matches(claims, self._audiences),
            _is_unexpired(claims),
            _issuer_matches(claims, self._issuers),
        )
        if not all(checks):
            logger.debug("Introspection response failed the audience, expiry or issuer check")
        return all(checks)


# --------------------------------------------------------------------------------------
# Factories
# --------------------------------------------------------------------------------------


def build_token_verifier(config: AuthConfig) -> TokenVerifier:
    """Choose the verifier that matches the configured token source.

    Args:
        config: The gate configuration.

    Returns:
        A :class:`JWTTokenVerifier` if ``jwks_url`` is set, otherwise an
        :class:`IntrospectionTokenVerifier`.

    Raises:
        ConfigurationError: If not exactly one of ``jwks_url`` and ``introspection_url`` is set.
        OAuthDependencyError: If JWT verification is chosen but PyJWT is not installed.
    """
    if (config.jwks_url is None) == (config.introspection_url is None):
        msg = "Set exactly one of the JWKS URL and the introspection URL."
        raise ConfigurationError(msg)
    if config.jwks_url is not None:
        return JWTTokenVerifier(config)
    return IntrospectionTokenVerifier(config)


def build_auth_settings(config: AuthConfig) -> AuthSettings:
    """Build the SDK's auth settings for the protected-resource metadata and middleware.

    Args:
        config: The gate configuration.

    Returns:
        Settings that make the SDK refuse any token whose resource is not ``resource_url``.
    """
    return AuthSettings(
        issuer_url=AnyHttpUrl(config.issuer_url),
        resource_server_url=AnyHttpUrl(config.resource_url),
        required_scopes=list(config.required_scopes),
        validate_token_resource=True,
    )
