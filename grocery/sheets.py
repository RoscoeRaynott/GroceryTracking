"""Google Sheets storage, set up the same way as Bookshelf Scanner.

Reads are cached so opening the app costs one read per tab; every write
clears the cache so the next read sees fresh data.
"""

import streamlit as st

from grocery.schema import DEFAULT_SETTINGS, TABS

try:
    import gspread
    from gspread.utils import rowcol_to_a1
    from google.oauth2.service_account import Credentials
    GSHEETS_AVAILABLE = True
except ImportError:
    gspread = None
    Credentials = None
    GSHEETS_AVAILABLE = False

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Header row of each tab as it actually is in the Sheet (may have extra columns).
_HEADERS = {}


def _service_account():
    try:
        sa = dict(st.secrets["gcp_service_account"])
    except Exception:
        return None
    if isinstance(sa.get("private_key"), str) and "\\n" in sa["private_key"]:
        sa["private_key"] = sa["private_key"].replace("\\n", "\n")
    return sa


def _spreadsheet_url():
    try:
        return str(st.secrets["gsheets"]["spreadsheet_url"]).strip()
    except Exception:
        return None


def service_account_email():
    sa = _service_account()
    return sa.get("client_email") if sa else None


def connect():
    """Return (spreadsheet, error message). Creates any missing tabs."""
    if not GSHEETS_AVAILABLE:
        return None, "The gspread / google-auth packages are not installed."
    sa = _service_account()
    url = _spreadsheet_url()
    if not sa:
        return None, "Missing [gcp_service_account] in Streamlit secrets."
    if not url:
        return None, "Missing [gsheets] spreadsheet_url in Streamlit secrets."
    try:
        return _open(url), None
    except Exception as ex:
        return None, f"{type(ex).__name__}: {ex}"


@st.cache_resource(show_spinner="Connecting to Google Sheets…")
def _open(url):
    # Only successful connections are cached; a failure raises and is retried next run.
    client = gspread.authorize(Credentials.from_service_account_info(_service_account(), scopes=SCOPES))
    sh = client.open_by_url(url)
    _ensure_tabs(sh)
    return sh


def _ensure_tabs(sh):
    existing = {ws.title: ws for ws in sh.worksheets()}
    for title, headers in TABS.items():
        ws = existing.get(title)
        if ws is None:
            ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers))
            ws.update([headers], "A1")
            _HEADERS[title] = list(headers)
            current = list(headers)
        else:
            values = ws.get_all_values()
            current = values[0] if values else []
            if current != headers:
                if len(values) <= 1:
                    # No data yet: replace the header row outright.
                    _resize(ws, len(headers))
                    ws.batch_clear(["1:1"])
                    ws.update([headers], "A1")
                    current = list(headers)
                else:
                    # Data exists: never move columns, only add missing ones at the end.
                    missing = [h for h in headers if h not in current]
                    if missing:
                        _resize(ws, len(current) + len(missing))
                        ws.update([missing], rowcol_to_a1(1, len(current) + 1))
                        current = current + missing
        _HEADERS[title] = current
        if title == "settings":
            have = {row[0] for row in ws.get_all_values()[1:] if row}
            missing_rows = [[k, v] for k, v in DEFAULT_SETTINGS.items() if k not in have]
            if missing_rows:
                ws.append_rows(missing_rows, value_input_option="RAW")


def _resize(ws, cols):
    if ws.col_count < cols:
        ws.resize(cols=cols)


def _sheet():
    sh, err = connect()
    if err:
        raise RuntimeError(err)
    return sh


@st.cache_data(ttl=600, show_spinner=False)
def read_tab(title):
    """All rows of a tab as a list of dicts (strings)."""
    sh, err = connect()
    if err:
        return []
    return sh.worksheet(title).get_all_records(default_blank="", numericise_ignore=["all"])


def append_rows(title, rows):
    """Append rows (dicts keyed by header) in one call, then refresh reads."""
    if not rows:
        return
    ws = _sheet().worksheet(title)
    headers = _HEADERS.get(title) or TABS[title]
    values = [[_cell(r.get(h, "")) for h in headers] for r in rows]
    ws.append_rows(values, value_input_option="RAW")
    read_tab.clear()


def update_rows(title, id_col, changes):
    """changes = {row id: {column: value}}; writes all cells in one batch."""
    if not changes:
        return
    ws = _sheet().worksheet(title)
    values = ws.get_all_values()
    headers = values[0]
    id_index = headers.index(id_col)
    updates = []
    for r, row in enumerate(values[1:], start=2):
        row_id = row[id_index] if id_index < len(row) else ""
        for col, value in changes.get(row_id, {}).items():
            updates.append({"range": rowcol_to_a1(r, headers.index(col) + 1), "values": [[_cell(value)]]})
    if updates:
        ws.batch_update(updates, value_input_option="RAW")
    read_tab.clear()


def delete_rows_where(title, **match):
    """Delete every row whose columns equal all the given values."""
    ws = _sheet().worksheet(title)
    values = ws.get_all_values()
    if not values:
        return
    cols = {values[0].index(c): str(v) for c, v in match.items()}
    doomed = [
        r for r, row in enumerate(values[1:], start=2)
        if all(i < len(row) and row[i] == v for i, v in cols.items())
    ]
    for r in reversed(doomed):
        ws.delete_rows(r)
    read_tab.clear()


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def get_settings():
    values = dict(DEFAULT_SETTINGS)
    values.update({r["key"]: r["value"] for r in read_tab("settings") if r.get("key") and r.get("value") != ""})
    return values


def save_settings(changes):
    """Write changed settings back to their rows in one batch."""
    ws = _sheet().worksheet("settings")
    keys = [row[0] if row else "" for row in ws.get_all_values()]
    updates, new_rows = [], []
    for key, value in changes.items():
        if key in keys:
            updates.append({"range": f"B{keys.index(key) + 1}", "values": [[_cell(value)]]})
        else:
            new_rows.append([key, _cell(value)])
    if updates:
        ws.batch_update(updates, value_input_option="RAW")
    if new_rows:
        ws.append_rows(new_rows, value_input_option="RAW")
    read_tab.clear()
