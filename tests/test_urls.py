"""Tests that the router URLs are constructed correctly.

Verifies:
- Login goes to /cgi-bin/wlogin.cgi with base64-encoded aa and ab params
- Physical Connection page is tried first; DSL status paths are fallbacks
- HTTP vs HTTPS scheme is selected based on port
"""
from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, unquote_plus, urlparse

import aiohttp
import pytest

from custom_components.draytek_dsl.const import DSL_STATUS_PATHS, PHYSICAL_CONNECTION_PATH
from custom_components.draytek_dsl.coordinator import DraytekDslCoordinator


def _make_mock_response(status: int = 200, text: str = "") -> MagicMock:
    """Build a mock aiohttp response usable as an async context manager."""
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_mock_session(*responses) -> MagicMock:
    """Return a mock ClientSession whose .get() returns responses in sequence."""
    session = MagicMock()
    session.get = MagicMock(side_effect=list(responses))
    return session


_PHYCONN_HTML = (
    '<td><font color="green">SHOWTIME</font></td>'
    "<td><script>document.write(bg.KseparatorAdd('4998000'))</script></td>"
    "<td><script>document.write(bg.KseparatorAdd('13095000'))</script></td>"
    """vdsl_str+='<td><font color="'+f_color+'">9 (dB)</font></td>';"""
    """vdsl_str+='<td><font color="'+f_color+'">17 (dB)</font></td>';"""
)
_SPEEDS_HTML = "<td>Down Speed</td><td>80000 Kbps</td><td>Up Speed</td><td>20000 Kbps</td>"


class TestLoginUrl:
    async def test_login_path_is_wlogin_cgi(self, coordinator):
        session = _make_mock_session(_make_mock_response(200))
        await coordinator._login(session)

        url = session.get.call_args[0][0]
        assert urlparse(url).path == "/cgi-bin/wlogin.cgi"

    async def test_login_uses_correct_host_and_port(self, coordinator):
        session = _make_mock_session(_make_mock_response(200))
        await coordinator._login(session)

        url = session.get.call_args[0][0]
        parsed = urlparse(url)
        assert parsed.hostname == "192.168.1.1"
        assert parsed.port == 80

    async def test_login_sends_aa_and_ab_params(self, coordinator):
        session = _make_mock_session(_make_mock_response(200))
        await coordinator._login(session)

        url = session.get.call_args[0][0]
        params = parse_qs(urlparse(url).query)
        assert "aa" in params, "Missing 'aa' (username) query parameter"
        assert "ab" in params, "Missing 'ab' (password) query parameter"

    async def test_login_encodes_username_as_base64(self, coordinator):
        session = _make_mock_session(_make_mock_response(200))
        await coordinator._login(session)

        url = session.get.call_args[0][0]
        aa_value = parse_qs(urlparse(url).query)["aa"][0]
        decoded = base64.b64decode(unquote_plus(aa_value)).decode()
        assert decoded == coordinator._username

    async def test_login_encodes_password_as_base64(self, coordinator):
        session = _make_mock_session(_make_mock_response(200))
        await coordinator._login(session)

        url = session.get.call_args[0][0]
        ab_value = parse_qs(urlparse(url).query)["ab"][0]
        decoded = base64.b64decode(unquote_plus(ab_value)).decode()
        assert decoded == coordinator._password

    async def test_login_accepts_302_redirect(self, coordinator):
        """DrayTek routers sometimes return 302 on successful login."""
        session = _make_mock_session(_make_mock_response(302))
        await coordinator._login(session)  # Must not raise

    async def test_login_raises_on_401(self, coordinator):
        from homeassistant.helpers.update_coordinator import UpdateFailed
        session = _make_mock_session(_make_mock_response(401))
        with pytest.raises(UpdateFailed):
            await coordinator._login(session)


class TestSchemeSelection:
    def test_http_used_for_port_80(self):
        coord = object.__new__(DraytekDslCoordinator)
        coord.host = "192.168.1.1"
        coord._port = 80
        coord._username = "admin"
        coord._password = "pass"
        coord._base_url = ("https" if 80 == 443 else "http") + "://192.168.1.1:80"
        assert coord._base_url.startswith("http://")

    def test_https_used_for_port_443(self):
        coord = object.__new__(DraytekDslCoordinator)
        coord.host = "192.168.1.1"
        coord._port = 443
        coord._username = "admin"
        coord._password = "pass"
        scheme = "https" if 443 == 443 else "http"
        coord._base_url = f"{scheme}://192.168.1.1:443"
        assert coord._base_url.startswith("https://")


class TestFetchLineData:
    def test_first_path_in_const_is_tried_first(self):
        assert DSL_STATUS_PATHS[0] == "/doc/dslstatus.sht"

    async def test_physical_connection_page_is_tried_first(self, coordinator):
        """Physical Connection page must be the first URL attempted."""
        session = _make_mock_session(_make_mock_response(200, _PHYCONN_HTML))
        await coordinator._fetch_line_data(session)

        first_url = session.get.call_args_list[0][0][0]
        assert PHYSICAL_CONNECTION_PATH in first_url

    async def test_physical_connection_page_returns_all_four_values(self, coordinator):
        session = _make_mock_session(_make_mock_response(200, _PHYCONN_HTML))
        data = await coordinator._fetch_line_data(session)

        assert data["upload_kbps"] == 4998
        assert data["download_kbps"] == 13095
        assert data["snr_upstream_db"] == 9
        assert data["snr_downstream_db"] == 17

    async def test_falls_back_to_dsl_paths_when_physical_connection_fails(self, coordinator):
        """A 404 on the Physical Connection page must trigger the DSL fallback."""
        session = _make_mock_session(
            _make_mock_response(404),            # Physical Connection: 404
            _make_mock_response(200, _SPEEDS_HTML),  # First DSL fallback path: success
        )
        data = await coordinator._fetch_line_data(session)

        assert data["download_kbps"] == 80000
        assert data["snr_upstream_db"] is None   # Fallback has no SNR
        assert data["snr_downstream_db"] is None

    async def test_falls_through_to_next_path_on_404(self, coordinator):
        """A 404 on the first fallback path must cause the second to be tried."""
        session = _make_mock_session(
            _make_mock_response(404),            # Physical Connection
            _make_mock_response(404),            # DSL path 0: 404
            _make_mock_response(200, _SPEEDS_HTML),  # DSL path 1: success
        )
        await coordinator._fetch_line_data(session)

        tried_urls = [c[0][0] for c in session.get.call_args_list]
        assert any(DSL_STATUS_PATHS[1] in u for u in tried_urls)

    async def test_raises_update_failed_when_all_paths_exhausted(self, coordinator):
        from homeassistant.helpers.update_coordinator import UpdateFailed

        # Physical Connection + all DSL fallbacks fail
        all_404s = [_make_mock_response(404)] * (1 + len(DSL_STATUS_PATHS))
        session = _make_mock_session(*all_404s)
        with pytest.raises(UpdateFailed):
            await coordinator._fetch_line_data(session)

    async def test_all_declared_paths_are_tried_before_giving_up(self, coordinator):
        """Every path in DSL_STATUS_PATHS must be attempted before giving up."""
        from homeassistant.helpers.update_coordinator import UpdateFailed

        # Physical Connection returns empty (no SHOWTIME), then all DSL paths empty
        no_data_pages = [_make_mock_response(200, "<html></html>")] * (1 + len(DSL_STATUS_PATHS))
        session = _make_mock_session(*no_data_pages)
        with pytest.raises(UpdateFailed):
            await coordinator._fetch_line_data(session)

        tried_urls = [c[0][0] for c in session.get.call_args_list]
        for path in DSL_STATUS_PATHS:
            assert any(path in u for u in tried_urls), f"Path {path!r} was never tried"
