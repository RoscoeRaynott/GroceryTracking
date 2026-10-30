"""Changes to batches (finished, wasted, expiry, deleted) and undoing them.

A batch's wasted share is never stored on the batch: it is the sum of its
"wasted" events that haven't been undone. Each change gets a change_id so a
whole voice command can be undone at once; undo marks the events as undone
and restores the batch fields saved in "previous".
"""

import json

from grocery import records, sheets, timeutil

LABELS = {"wasted": "wasted", "finished": "finished", "expiry": "expiry set", "deleted": "deleted"}


def all_events():
    return sheets.read_tab("events")


def wasted_share(batch_id, events=None):
    events = all_events() if events is None else events
    total = sum(_num(e.get("wasted_fraction")) for e in events
                if e.get("batch_id") == batch_id and e.get("type") == "wasted" and e.get("undone") != "yes")
    return min(total, 1.0)


def apply(actions, said, source):
    """actions: [{"type": waste|finish|expiry|delete, "batch": batch row, ...}]. Returns change_id."""
    if not actions:
        return None
    now = timeutil.now_str()
    events = all_events()
    taken = {e.get("event_id") for e in events} | {e.get("change_id") for e in events}
    change_id = records.readable_id(f"change-{timeutil.now():%Y-%m-%d-%H%M}", taken)
    batch_changes, new_events, previous = {}, [], {}

    def event(batch, kind, **fields):
        bid = batch["batch_id"]
        if bid not in previous:
            previous[bid] = {k: batch.get(k, "") for k in ("status", "closed_on", "expiry_date")}
            prev = json.dumps(previous[bid])
        else:
            prev = ""
        new_events.append(dict({
            "event_id": records.readable_id(f"{bid}-{kind}", taken), "change_id": change_id, "batch_id": bid,
            "item": batch.get("item", ""), "type": kind, "recorded_at": now, "source": source,
            "said": said, "previous": prev, "undone": False,
        }, **fields))

    for a in actions:
        b = a["batch"]
        bid = b["batch_id"]
        day = a.get("happened_on") or timeutil.today_str()
        wasted = wasted_share(bid, events) + sum(
            _num(e.get("wasted_fraction")) for e in new_events if e["batch_id"] == bid and e["type"] == "wasted")
        if a["type"] == "waste":
            fraction = max(0.0, min(_num(a.get("fraction")), 1.0 - wasted))
            event(b, "wasted", wasted_fraction=round(fraction, 4), happened_on=day)
            if a.get("batch_done", True) or wasted + fraction >= 0.999:
                # The wasted part went bad and the rest was eaten: the batch is finished.
                event(b, "finished", wasted_fraction=0, happened_on=day)
                batch_changes.setdefault(bid, {}).update(status="closed", closed_on=day)
        elif a["type"] == "finish":
            if a.get("rest_wasted") and wasted < 0.999:
                event(b, "wasted", wasted_fraction=round(1.0 - wasted, 4), happened_on=day)
            event(b, "finished", wasted_fraction=0, happened_on=day)
            batch_changes.setdefault(bid, {}).update(status="closed", closed_on=day)
        elif a["type"] == "expiry":
            event(b, "expiry", expiry_date=a["expiry_date"], happened_on=timeutil.today_str())
            batch_changes.setdefault(bid, {}).update(expiry_date=a["expiry_date"])
        elif a["type"] == "delete":
            event(b, "deleted", happened_on=timeutil.today_str())
            batch_changes.setdefault(bid, {}).update(status="deleted")

    sheets.append_rows("events", new_events)
    sheets.update_rows("batches", "batch_id", batch_changes)
    return change_id


def undo(change_id):
    events = [e for e in all_events() if e.get("change_id") == change_id and e.get("undone") != "yes"]
    restore = {}
    for e in events:
        if e.get("previous"):
            try:
                restore[e["batch_id"]] = json.loads(e["previous"])
            except ValueError:
                pass
    sheets.update_rows("events", "event_id", {e["event_id"]: {"undone": "yes"} for e in events})
    sheets.update_rows("batches", "batch_id", restore)


def recent(limit=20):
    """Latest changes, newest first: [{"change_id", "when", "lines": [...], "undone": bool}]."""
    groups = {}
    for e in all_events():
        cid = e.get("change_id") or e.get("event_id")
        g = groups.setdefault(cid, {"change_id": e.get("change_id"), "when": e.get("recorded_at", ""),
                                    "lines": [], "undone": True, "said": e.get("said", "")})
        g["lines"].append(describe(e))
        g["undone"] = g["undone"] and e.get("undone") == "yes"
    return sorted(groups.values(), key=lambda g: g["when"], reverse=True)[:limit]


def describe(e):
    kind = e.get("type")
    if kind == "wasted":
        return f"{e['item']}: {fraction_text(_num(e.get('wasted_fraction')))} wasted ({e.get('happened_on')})"
    if kind == "finished":
        return f"{e['item']}: finished {e.get('happened_on')}"
    if kind == "expiry":
        return f"{e['item']}: expires {e.get('expiry_date')}"
    if kind == "deleted":
        return f"{e['item']}: batch deleted"
    return f"{e.get('item')}: {kind}"


def fraction_text(f):
    for value, text in [(1, "all"), (0.5, "½"), (1 / 3, "⅓"), (2 / 3, "⅔"), (0.25, "¼"), (0.75, "¾")]:
        if abs(f - value) < 0.01:
            return text
    return f"{f:.0%}"


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
