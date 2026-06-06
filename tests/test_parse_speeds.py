"""Unit tests for HTML parsing functions.

test_parse_physical_connection covers the primary Physical Connection CGI page.
test_parse_speeds covers the fallback DSL Status .sht pages.
"""
import pytest

from custom_components.draytek_dsl.coordinator import (
    _parse_physical_connection,
    _parse_speeds,
)


class TestParsePhysicalConnection:
    """Tests for the Physical Connection page parser (primary data source)."""

    def _vigor_2862_html(
        self,
        up_bps: int = 4998000,
        down_bps: int = 13095000,
        snr_up_db: int = 9,
        snr_down_db: int = 17,
        synced: bool = True,
    ) -> str:
        """Reproduce the server-generated HTML structure of the Vigor 2862."""
        state = "SHOWTIME" if synced else "TRAINING"
        return (
            f'<td><font color="green">{state}</font></td>'
            f"<td><font color=\"green\"><script>document.write(bg.KseparatorAdd('{up_bps}'))</script></font></td>"
            f"<td><font color=\"green\"><script>document.write(bg.KseparatorAdd('{down_bps}'))</script></font></td>"
            f'<td><font color="green">17</font></td>'
            f'<td><font color="green">24</font></td>'
            # VDSL JS block with server-injected SNR dB values
            f'vdsl_str+=\'<td><font color="\'+f_color+\'">{snr_up_db} (dB)</font></td>\';'
            f'vdsl_str+=\'<td><font color="\'+f_color+\'">{snr_down_db} (dB)</font></td>\';'
        )

    def test_extracts_upload_speed(self):
        data = _parse_physical_connection(self._vigor_2862_html())
        assert data["upload_kbps"] == 4998

    def test_extracts_download_speed(self):
        data = _parse_physical_connection(self._vigor_2862_html())
        assert data["download_kbps"] == 13095

    def test_extracts_snr_upstream(self):
        data = _parse_physical_connection(self._vigor_2862_html())
        assert data["snr_upstream_db"] == 9

    def test_extracts_snr_downstream(self):
        data = _parse_physical_connection(self._vigor_2862_html())
        assert data["snr_downstream_db"] == 17

    def test_different_snr_values(self):
        data = _parse_physical_connection(self._vigor_2862_html(snr_up_db=12, snr_down_db=25))
        assert data["snr_upstream_db"] == 12
        assert data["snr_downstream_db"] == 25

    def test_zero_speed_returns_none(self):
        """Zero bps means the line is not synced — should return None, not 0."""
        data = _parse_physical_connection(self._vigor_2862_html(up_bps=0, down_bps=0))
        assert data["upload_kbps"] is None
        assert data["download_kbps"] is None

    def test_no_showtime_returns_none_speeds(self):
        """Without SHOWTIME the speed regex cannot anchor — both speeds are None."""
        data = _parse_physical_connection(self._vigor_2862_html(synced=False))
        assert data["upload_kbps"] is None
        assert data["download_kbps"] is None

    def test_empty_html_returns_all_none(self):
        data = _parse_physical_connection("")
        assert data["download_kbps"] is None
        assert data["upload_kbps"] is None
        assert data["snr_upstream_db"] is None
        assert data["snr_downstream_db"] is None


class TestParseSpeedsFallback:
    """Tests for the fallback DSL Status .sht page parser (speeds only)."""

    def test_down_speed_kbps(self):
        assert _parse_speeds("<td>Down Speed</td><td>80000 Kbps</td>")["download_kbps"] == 80000

    def test_down_speed_lowercase_kbps(self):
        assert _parse_speeds("<td>Down Speed</td><td>76000 kbps</td>")["download_kbps"] == 76000

    def test_upstream_rate_kbps(self):
        assert _parse_speeds("Upstream Rate: 19000 Kbps")["upload_kbps"] == 19000

    def test_actual_rate_row_format(self):
        """Vigor dslstatus.sht: DS/US values in adjacent cells on one Actual Rate row."""
        html = (
            "<td>Actual Rate</td>"
            "<td><font color=green>38000</font></td>"
            "<td><font color=green>Kbps</font></td>"
            "<td><font color=blue>10000</font></td>"
            "<td><font color=blue>Kbps</font></td>"
        )
        assert _parse_speeds(html)["download_kbps"] == 38000
        assert _parse_speeds(html)["upload_kbps"] == 10000

    def test_realistic_vigor_2862_vdsl_page(self):
        """Mimics the DSL status page structure of the Vigor 2862."""
        html = """
        <html><body>
        <table border="1">
          <tr><td>Down Speed</td><td>80000 Kbps</td></tr>
          <tr><td>Up Speed</td><td>20000 Kbps</td></tr>
        </table>
        </body></html>
        """
        data = _parse_speeds(html)
        assert data["download_kbps"] == 80000
        assert data["upload_kbps"] == 20000

    def test_ds_us_label_format(self):
        html = "DS Actual Rate:  72448 Kbps\nUS Actual Rate:  19200 Kbps"
        data = _parse_speeds(html)
        assert data["download_kbps"] == 72448
        assert data["upload_kbps"] == 19200

    def test_empty_html(self):
        data = _parse_speeds("")
        assert data["download_kbps"] is None
        assert data["upload_kbps"] is None

    def test_snr_numbers_not_mistaken_for_speeds(self):
        html = """
        <tr><td>Down Speed</td><td>80000 Kbps</td></tr>
        <tr><td>SNR Margin Down</td><td>8</td></tr>
        <tr><td>Up Speed</td><td>20000 Kbps</td></tr>
        """
        data = _parse_speeds(html)
        assert data["download_kbps"] == 80000
        assert data["upload_kbps"] == 20000
