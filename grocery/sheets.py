"""Google Sheets storage, set up the same way as Bookshelf Scanner.

Google allows about 60 reads a minute, so the app keeps a copy of each tab in
memory: a tab is read once, every write updates that copy as well as the
Sheet, and the copy is re-read from Google after 10 minutes (so edits made by
hand in the Sheet show up within 10 minutes). If Google still says "too many
requests", the call waits and tries again instead of failing.
"""

import threading
import time

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
# Worksheet objects by title, so writes don't spend a read looking the tab up.
_WS = {}
# In-memory copy of each tab: title -> {"values": rows incl. header, "loaded": time}.
_MIRROR = {}
_LOCK = threading.RLock()
MIRROR_SECONDS = 600
_RETRY_WAITS = (5, 15, 30)


def _retry(call, *args, **kwargs):
    """Run a Sheets API call, waiting and retrying when Google says "too many requests"."""
    for wait in _RETRY_WAITS + (None,):
        try:
            return call(*args, **kwargs)
        except Exception as ex:
            if wait is None or getattr(ex, "code", None) not in (429, 500, 502, 503):
                raise
            time.sleep(wait)


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
    with _LOCK:
        _WS.clear()
        _MIRROR.clear()
    existing = {ws.title: ws for ws in _retry(sh.worksheets)}
    for title, headers in TABS.items():
        ws = existing.get(title)
        if ws is None:
            ws = _retry(sh.add_worksheet, title=title, rows=1000, cols=len(headers))
            _retry(ws.update, [headers], "A1")
            values = [list(headers)]
        else:
            values = _retry(ws.get_all_values)
            current = values[0] if values else []
            if current != headers:
                if len(values) <= 1:
                    # No data yet: replace the header row outright.
                    _resize(ws, len(headers))
                    _retry(ws.batch_clear, ["1:1"])
                    _retry(ws.update, [headers], "A1")
                    values = [list(headers)]
                else:
                    # Data exists: never move columns, only add missing ones at the end.
                    missing = [h for h in headers if h not in current]
                    if missing:
                        _resize(ws, len(current) + len(missing))
                        _retry(ws.update, [missing], rowcol_to_a1(1, len(current) + 1))
                        values[0] = current + missing
        _HEADERS[title] = list(values[0])
        if title == "settings":
            have = {row[0] for row in values[1:] if row}
            missing_rows = [[k, v] for k, v in DEFAULT_SETTINGS.items() if k not in have]
            if missing_rows:
                _retry(ws.append_rows, missing_rows, value_input_option="RAW")
                values += missing_rows
        with _LOCK:
            _WS[title] = ws
            # Opening already read every tab, so those values start the in-memory copy.
            _MIRROR[title] = {"values": values, "loaded": time.time()}


def _resize(ws, cols):
    if ws.col_count < cols:
        _retry(ws.resize, cols=cols)


def _sheet():
    sh, err = connect()
    if err:
        raise RuntimeError(err)
    return sh


def _ws(title):
    sh = _sheet()
    with _LOCK:
        if title not in _WS:
            _WS[title] = _retry(sh.worksheet, title)
        return _WS[title]


def _values(title, fresh=False):
    """The tab's rows (header first) from the in-memory copy, read from Google when old or missing.

    Callers hold _LOCK and may change the returned list in place to keep the copy in step.
    """
    entry = _MIRROR.get(title)
    if fresh or entry is None or time.time() - entry["loaded"] > MIRROR_SECONDS:
        entry = {"values": _retry(_ws(title).get_all_values), "loaded": time.time()}
        _MIRROR[title] = entry
    return entry["values"]


def read_tab(title):
    """All rows of a tab as a list of dicts (strings)."""
    _, err = connect()
    if err:
        return []
    with _LOCK:
        values = _values(title)
        if not values:
            return []
        header = values[0]
        return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in values[1:]]


def _forget():
    """Drop the in-memory copy so the next read comes fresh from Google."""
    with _LOCK:
        _MIRROR.clear()


# Kept so callers (and tests) that change the Sheet directly can force a fresh read.
read_tab.clear = _forget


def append_rows(title, rows):
    """Append rows (dicts keyed by header) in one call."""
    if not rows:
        return
    headers = _HEADERS.get(title) or TABS[title]
    values = [[_cell(r.get(h, "")) for h in headers] for r in rows]
    with _LOCK:
        _retry(_ws(title).append_rows, values, value_input_option="RAW")
        if title in _MIRROR:
            _MIRROR[title]["values"].extend(values)


def update_rows(title, id_col, changes):
    """changes = {row id: {column: value}}; writes all cells in one batch."""
    if not changes:
        return
    with _LOCK:
        values = _values(title)
        headers = values[0]
        id_index = headers.index(id_col)
        updates = []
        for r, row in enumerate(values[1:], start=2):
            row_id = row[id_index] if id_index < len(row) else ""
            for col, value in changes.get(row_id, {}).items():
                c = headers.index(col)
                updates.append({"range": rowcol_to_a1(r, c + 1), "values": [[_cell(value)]]})
                row.extend([""] * (c + 1 - len(row)))
                row[c] = _cell(value)
        if updates:
            try:
                _retry(_ws(title).batch_update, updates, value_input_option="RAW")
            except Exception:
                _MIRROR.pop(title, None)
                raise


def delete_rows_where(title, **match):
    """Delete every row whose columns equal all the given values."""
    with _LOCK:
        # Deleting goes by row number, so read fresh to be sure the rows are where we think.
        values = _values(title, fresh=True)
        if not values:
            return
        cols = {values[0].index(c): str(v) for c, v in match.items()}
        doomed = [
            r for r, row in enumerate(values[1:], start=2)
            if all(i < len(row) and row[i] == v for i, v in cols.items())
        ]
        ws = _ws(title)
        try:
            for r in reversed(doomed):
                _retry(ws.delete_rows, r)
                del values[r - 1]
        except Exception:
            _MIRROR.pop(title, None)
            raise


def remove_rows(title, should_remove):
    """Remove every data row for which should_remove(row dict) is true, in two API calls.

    The kept rows are written over the top first and only then is the leftover
    tail cleared, so a failure part-way never loses kept data.
    """
    with _LOCK:
        # The whole tab is rewritten, so read fresh rather than trust the copy.
        values = _values(title, fresh=True)
        if len(values) <= 1:
            return 0
        header = values[0]
        keep = [header] + [r for r in values[1:] if not should_remove(dict(zip(header, r + [""] * len(header))))]
        removed = len(values) - len(keep)
        if removed:
            ws = _ws(title)
            width = max(len(header), max(len(r) for r in values))
            padded = [r + [""] * (width - len(r)) for r in keep]
            try:
                _retry(ws.update, padded, "A1")
                _retry(ws.batch_clear, [f"A{len(keep) + 1}:{rowcol_to_a1(len(values), width)}"])
            except Exception:
                _MIRROR.pop(title, None)
                raise
            _MIRROR[title] = {"values": padded, "loaded": time.time()}
        return removed


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
    with _LOCK:
        values = _values("settings")
        keys = [row[0] if row else "" for row in values]
        updates, new_rows = [], []
        for key, value in changes.items():
            if key in keys:
                row = values[keys.index(key)]
                updates.append({"range": f"B{keys.index(key) + 1}", "values": [[_cell(value)]]})
                row.extend([""] * (2 - len(row)))
                row[1] = _cell(value)
            else:
                new_rows.append([key, _cell(value)])
        ws = _ws("settings")
        try:
            if updates:
                _retry(ws.batch_update, updates, value_input_option="RAW")
            if new_rows:
                _retry(ws.append_rows, new_rows, value_input_option="RAW")
                values.extend(new_rows)
        except Exception:
            _MIRROR.pop("settings", None)
            raise
