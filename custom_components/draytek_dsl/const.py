DOMAIN = "draytek_dsl"
DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 60

# Physical Connection page — provides speeds + SNR for VDSL2 (Vigor 2862 and similar).
# Preferred over the DSL Status fallback paths below.
PHYSICAL_CONNECTION_PATH = "/cgi-bin/v2x00.cgi?sFormAuthStr=&fid=168&option=1"

# Fallback paths for routers where the physical connection CGI is unavailable.
# DrayTek uses .sht (server-side HTML template) files for status pages.
DSL_STATUS_PATHS = [
    "/doc/dslstatus.sht",
    "/doc/DSLStatus.sht",
    "/doc/dslstatusinfo.sht",
    "/doc/vdslstatus.sht",
    "/doc/adslstatus.sht",
]
