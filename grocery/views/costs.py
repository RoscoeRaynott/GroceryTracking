"""💸 AI costs: what the app's AI calls cost, by task, from the ai_log tab."""

import datetime as dt

import altair as alt
import pandas as pd
import streamlit as st

from grocery import costs, sheets, timeutil
from grocery.views.common import back_home


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _rows():
    out = []
    for r in sheets.read_tab("ai_log"):
        try:
            at = dt.datetime.fromisoformat(str(r.get("at", ""))[:19].replace(" ", "T"))
        except ValueError:
            continue
        usage = {"promptTokenCount": _f(r.get("input_tokens")), "candidatesTokenCount": _f(r.get("output_tokens")),
                 "thoughtsTokenCount": _f(r.get("thought_tokens")), "toolUsePromptTokenCount": _f(r.get("tool_tokens"))}
        logged = r.get("cost_usd") not in ("", None) and "estimated" not in str(r.get("error", ""))
        out.append({"at": at, "task": costs.task_name(r.get("task", "")), "model": r.get("model", ""),
                    "blocked": "RECITATION" in str(r.get("error", "")),
                    "searches": int(_f(r.get("searches"))),
                    "thinking": int(usage["thoughtsTokenCount"]),
                    "cost": _f(r["cost_usd"]) if r.get("cost_usd") not in ("", None) else costs.cost(r.get("model", ""), usage),
                    "full": logged})  # older rows lack thinking and searches, so their cost is a low estimate
    return out


def render():
    back_home()
    st.title("💸 AI costs")
    rows = _rows()
    if not rows:
        st.info("No AI calls logged yet.")
        return
    now = timeutil.now().replace(tzinfo=None)
    span = st.segmented_control("Period", ["Today", "7 days", "30 days", "All"], default="Today",
                                key="cost_span", label_visibility="collapsed") or "Today"
    start = {"Today": now.replace(hour=0, minute=0, second=0, microsecond=0), "7 days": now - dt.timedelta(days=7),
             "30 days": now - dt.timedelta(days=30), "All": dt.datetime.min}[span]
    shown = [r for r in rows if r["at"] >= start]
    if not shown:
        st.info("No AI calls in this period.")
        return
    df = pd.DataFrame(shown)
    total, searches = df["cost"].sum(), int(df["searches"].sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Spent", f"${total:,.2f}")
    c2.metric("AI calls", f"{len(df)}")
    c3.metric("Google searches", f"{searches}")
    c4.metric("🚧 Blocked", f"{int(df['blocked'].sum())}", help="Lookups Google blocked for copying label text; "
              "each can be fetched in 3 parts from its panel (≈4× a lookup)")
    if not df["full"].all():
        st.caption("⚠️ Some costs are estimates: calls logged before this page existed (they miss thinking and searches), "
                   "and calls that failed or timed out before Google reported what they used.")

    by = (df.groupby("task").agg(cost=("cost", "sum"), calls=("cost", "size"), searches=("searches", "sum"))
          .reset_index().sort_values("cost", ascending=False))
    by["per_call"] = by["cost"] / by["calls"]
    by["label"] = by.apply(lambda r: f"${r['cost']:.2f} · {int(r['calls'])}×", axis=1)
    st.subheader("Where it goes", anchor=False)
    base = alt.Chart(by).encode(y=alt.Y("task:N", sort="-x", title=None, axis=alt.Axis(labelLimit=180)))
    bars = base.mark_bar(cornerRadiusEnd=4, color="#FF4B4B").encode(
        x=alt.X("cost:Q", title=None, axis=None),
        tooltip=[alt.Tooltip("task:N"), alt.Tooltip("cost:Q", format="$.3f"), alt.Tooltip("calls:Q"),
                 alt.Tooltip("per_call:Q", title="per call", format="$.4f"), alt.Tooltip("searches:Q")])
    text = base.mark_text(align="left", dx=4, color="#9AA0A6").encode(x="cost:Q", text="label:N")
    st.altair_chart((bars + text).properties(height=34 * len(by)), width="stretch")
    st.caption("Each bar: total cost of that kind of AI task · how many times it ran.")

    st.subheader("Cost per use", anchor=False)
    st.markdown(" ".join(f":gray-badge[{r['task']}: ${r['per_call']:.3f} each"
                         + (f", {r['searches'] / r['calls']:.1f} searches" if r["searches"] else "") + "]"
                         for _, r in by.iterrows()))

    if span != "Today":
        daily = df.assign(day=df["at"].dt.date).groupby("day")["cost"].sum().reset_index()
        st.subheader("By day", anchor=False)
        st.altair_chart(alt.Chart(daily).mark_bar(color="#FF4B4B", cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
            x=alt.X("day:T", title=None), y=alt.Y("cost:Q", title="$"),
            tooltip=[alt.Tooltip("day:T"), alt.Tooltip("cost:Q", format="$.2f")]).properties(height=180), width="stretch")

    with st.expander("Last 30 calls"):
        last = df.sort_values("at", ascending=False).head(30)
        st.dataframe(last.assign(cost=last["cost"].map(lambda c: f"${c:.4f}"), at=last["at"].dt.strftime("%b %d %H:%M"))
                     [["at", "task", "cost", "searches", "thinking", "model"]], hide_index=True, width="stretch")
    table, per_search = costs.prices()
    st.caption("Prices used (edit in Settings): " + " · ".join(f"{m} ${i}/${o} per 1M tokens in/out" for m, (i, o) in table.items())
               + f" · ${per_search * 1000:g} per 1,000 Google searches (Google gives some free each month, not subtracted here).")
