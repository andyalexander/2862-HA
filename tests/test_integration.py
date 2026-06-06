"""Integration tests against a real DrayTek router.

These tests are skipped automatically unless a .env file (or environment
variables) provides real router credentials:

    DRAYTEK_HOST       Router IP or hostname (required)
    DRAYTEK_PORT       Web interface port (default: 80)
    DRAYTEK_USERNAME   Admin username (default: admin)
    DRAYTEK_PASSWORD   Admin password (required)

Copy .env.example to .env and fill in your values. The .env file is
git-ignored and will never be committed.
"""
from __future__ import annotations

import os
from pathlib import Path

import aiohttp
import pytest

# Load .env if present (silently skip if python-dotenv is not installed)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

_HOST = os.getenv("DRAYTEK_HOST")
_PORT = int(os.getenv("DRAYTEK_PORT", "80"))
_USERNAME = os.getenv("DRAYTEK_USERNAME", "admin")
_PASSWORD = os.getenv("DRAYTEK_PASSWORD")

_router_available = pytest.mark.skipif(
    not _HOST or not _PASSWORD,
    reason="Set DRAYTEK_HOST and DRAYTEK_PASSWORD in .env to run integration tests",
)


def _make_session() -> aiohttp.ClientSession:
    """Return a session that stores cookies from IP-address hosts."""
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=False),
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    )


@_router_available
async def test_router_is_reachable():
    """Basic connectivity check — the router must respond to HTTP."""
    async with _make_session() as session:
        async with session.get(
            f"http://{_HOST}:{_PORT}/",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            assert resp.status in (200, 302, 401), (
                f"Unexpected status {resp.status} — is {_HOST}:{_PORT} the right address?"
            )


@_router_available
async def test_login_succeeds():
    """Login must return HTTP 200 or 302 (redirect to main page)."""
    from custom_components.draytek_dsl.coordinator import _encode_credential

    url = (
        f"http://{_HOST}:{_PORT}/cgi-bin/wlogin.cgi"
        f"?aa={_encode_credential(_USERNAME)}"
        f"&ab={_encode_credential(_PASSWORD)}"
    )
    async with _make_session() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            assert resp.status in (200, 302), (
                f"Login failed with HTTP {resp.status}. "
                "Check DRAYTEK_USERNAME and DRAYTEK_PASSWORD in .env."
            )


@_router_available
async def test_dsl_status_page_is_reachable():
    """After login, at least one DSL status path must return a parseable page.

    This test passes whether or not the DSL line is currently synced — it only
    verifies that authentication works and the correct status page is served.
    """
    from custom_components.draytek_dsl.const import DSL_STATUS_PATHS
    from custom_components.draytek_dsl.coordinator import _encode_credential

    base_url = f"http://{_HOST}:{_PORT}"

    async with _make_session() as session:
        login_url = (
            f"{base_url}/cgi-bin/wlogin.cgi"
            f"?aa={_encode_credential(_USERNAME)}"
            f"&ab={_encode_credential(_PASSWORD)}"
        )
        async with session.get(login_url, timeout=aiohttp.ClientTimeout(total=10)):
            pass

        found_path = None
        for path in DSL_STATUS_PATHS:
            url = f"{base_url}{path}"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        continue
                    html = await resp.text()
                    # Confirm the page is the DSL status page, not the login page
                    if "Actual Rate" in html and "Vigor Login" not in html:
                        found_path = path
                        break
            except aiohttp.ClientError:
                continue

    assert found_path is not None, (
        f"No DSL status page found on any of these paths: {DSL_STATUS_PATHS}\n"
        "Check that the router credentials are correct and the firmware version "
        "is supported."
    )


@_router_available
async def test_dsl_speeds_are_plausible():
    """When the DSL line is synced, speeds must be in a sensible range (100 kbps – 300 Mbps)."""
    from custom_components.draytek_dsl.const import DSL_STATUS_PATHS
    from custom_components.draytek_dsl.coordinator import _encode_credential, _parse_speeds

    base_url = f"http://{_HOST}:{_PORT}"

    async with _make_session() as session:
        login_url = (
            f"{base_url}/cgi-bin/wlogin.cgi"
            f"?aa={_encode_credential(_USERNAME)}"
            f"&ab={_encode_credential(_PASSWORD)}"
        )
        async with session.get(login_url, timeout=aiohttp.ClientTimeout(total=10)):
            pass

        for path in DSL_STATUS_PATHS:
            url = f"{base_url}{path}"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        continue
                    html = await resp.text()
                    data = _parse_speeds(html)
                    if data["download_kbps"] is not None:
                        assert 100 <= data["download_kbps"] <= 300_000, (
                            f"Download speed {data['download_kbps']} kbps is outside "
                            "expected VDSL2 range (100 – 300,000 kbps)"
                        )
                        assert 100 <= data["upload_kbps"] <= 300_000, (
                            f"Upload speed {data['upload_kbps']} kbps is outside "
                            "expected VDSL2 range (100 – 300,000 kbps)"
                        )
                        return
            except aiohttp.ClientError:
                continue

    pytest.skip("No DSL speed data found — line may not be synced")
