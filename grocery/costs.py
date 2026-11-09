"""What each AI call costs, from the token and search counts Gemini reports.

Prices are editable in Settings (ai_prices, price_per_1000_searches), since they change and the defaults come
from third-party price lists, not Google's own page. Thinking tokens are billed as output.
"""

import threading

import streamlit as st

from grocery import sheets

SPEND = "ai_spend_pending"  # this session's AI calls since the last cost pop-up
_LOCK = threading.Lock()

# Friendly names for the AI tasks the app runs.
TASKS = {
    "product_web_lookup": "🔎 Web lookup", "lookup_product": "🔎 Web lookup (in parts)",
    "lookup_nutrition": "🔎 Web lookup (in parts)", "lookup_ingredient_items": "🔎 Web lookup (in parts)",
    "picture_search": "🖼 Picture search", "label_read": "📷 Label / front photo",
    "receipt_read": "🧾 Receipt photo", "receipt_items": "🧾 Receipt → items",
    "group_suggest": "🗂 Group suggestions", "voice": "🎤 Voice", "voice_refine": "🎤 Voice fix",
    "waste_text": "🗑 Went bad", "nickname": "🏷 Nickname", "ingredients_said": "🎤 Ingredients said",
}


def task_name(task):
    if str(task).startswith("lab_"):
        return "🧪 Lookup lab"
    if str(task).startswith("trial_"):
        return "🧪 Thinking trial"
    return TASKS.get(task, task)


def prices():
    """{model: (dollars per 1M input tokens, per 1M output tokens)}, and dollars per Google search."""
    s = sheets.get_settings()
    table = {}
    for part in str(s.get("ai_prices", "")).split(";"):
        if "=" in part and "/" in part:
            model, rates = part.split("=", 1)
            try:
                i, o = (float(x) for x in rates.split("/", 1))
                table[model.strip()] = (i, o)
            except ValueError:
                pass
    try:
        per_search = float(s.get("price_per_1000_searches") or 14) / 1000
    except ValueError:
        per_search = 0.014
    return table, per_search


def cost(model, usage, searches=0):
    """Dollars for one call. A model with no price uses the dearest known one, so costs are never understated."""
    table, per_search = prices()
    rate = table.get(model) or max(table.values(), default=(0.75, 3.75), key=lambda r: r[1])
    tokens_in = (usage.get("promptTokenCount") or 0) + (usage.get("toolUsePromptTokenCount") or 0)
    tokens_out = (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0)
    return tokens_in / 1e6 * rate[0] + tokens_out / 1e6 * rate[1] + searches * per_search


def estimate(task, model):
    """(dollars, searches) for a call that failed before Google reported its usage: the typical cost of this kind
    of call from your log (median of the last 20 that worked), else a cautious guess. Google may bill such calls."""
    rows = [r for r in sheets.read_tab("ai_log") if r.get("task") == task and r.get("cost_usd") not in ("", None)
            and str(r.get("ok")).lower() in ("true", "yes", "1")][-20:]
    if rows:
        def mid(values):
            values = sorted(values)
            return values[len(values) // 2]
        f = lambda v: float(v or 0)
        return mid([f(r["cost_usd"]) for r in rows]), int(mid([f(r.get("searches")) for r in rows]))
    searched = task in ("product_web_lookup", "picture_search") or str(task).startswith(("lookup_", "lab_"))
    usage = {"promptTokenCount": 3000, "candidatesTokenCount": 1000, "thoughtsTokenCount": 3000}
    return cost(model, usage, 2 if searched else 0), 2 if searched else 0


def note(task, dollars, searches, estimated=False):
    """Remember this call for the next cost pop-up (works from the parallel lookups too)."""
    try:
        with _LOCK:
            st.session_state.setdefault(SPEND, []).append((task_name(task) + (" ≈" if estimated else ""), dollars, searches))
    except Exception:
        pass  # no session (e.g. a test or a background job): nothing to show


def toast_pending():
    """After an action: one small pop-up saying what its AI calls cost."""
    try:
        with _LOCK:
            spent = st.session_state.pop(SPEND, [])
    except Exception:
        return
    if not spent:
        return
    total = sum(d for _, d, _ in spent)
    rough = any(name.endswith(" ≈") for name, _, _ in spent)
    searches = sum(s for _, _, s in spent)
    by = {}
    for name, d, _ in spent:
        by[name] = by.get(name, 0) + d
    parts = " · ".join(f"{name} ${d:.3f}" for name, d in sorted(by.items(), key=lambda x: -x[1])[:3])
    st.toast(f"💸 AI cost {'≈' if rough else ''}${total:.3f}" + (f" ({searches} Google search{'es' if searches != 1 else ''})" if searches else "")
             + f"  \n{parts}" + ("  \n≈ estimated: a call failed before Google said what it used" if rough else ""),
             icon="💸", duration=15)  # long enough to read on a phone (✕ closes it sooner)
