"""Google Sheets storage, set up the same way as Bookshelf Scanner.

Reads are cached so opening the app costs one read per tab; every write
clears the cache so the next read sees fresh data.
"""

import streamlit as st

from grocery.schema import DEFAULT_SETTINGS, TABS

try:
    import gspread
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
        elif not ws.row_values(1):
            ws.update([headers], "A1")
        if title == "settings":
            have = {row[0] for row in ws.get_all_values()[1:] if row}
            missing = [[k, v] for k, v in DEFAULT_SETTINGS.items() if k not in have]
            if missing:
                ws.append_rows(missing, value_input_option="RAW")


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
    sh, err = connect()
    if err:
        raise RuntimeError(err)
    headers = TABS[title]
    values = [[str(r.get(h, "")) for h in headers] for r in rows]
    sh.worksheet(title).append_rows(values, value_input_option="RAW")
    read_tab.clear()


def get_settings():
    values = dict(DEFAULT_SETTINGS)
    values.update({r["key"]: r["value"] for r in read_tab("settings") if r.get("key")})
    return values


def save_settings(changes):
    """Write changed settings back to their rows in one batch."""
    sh, err = connect()
    if err:
        raise RuntimeError(err)
    ws = sh.worksheet("settings")
    keys = [row[0] if row else "" for row in ws.get_all_values()]
    updates, new_rows = [], []
    for key, value in changes.items():
        if key in keys:
            updates.append({"range": f"B{keys.index(key) + 1}", "values": [[str(value)]]})
        else:
            new_rows.append([key, str(value)])
    if updates:
        ws.batch_update(updates, value_input_option="RAW")
    if new_rows:
        ws.append_rows(new_rows, value_input_option="RAW")
    read_tab.clear()
