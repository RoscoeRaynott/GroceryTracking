"""Insights: what you ate and spent over a period (only finished things count), by nutrient, ingredient and group.

📊 Overview: money, finished, waste, alcohol, and each key nutrient as an average per day vs its daily value.
🧪 Nutrients: one nutrient week by week, and which groups it came from.
🧾 Ingredients: how often each ingredient appeared in what you finished (lists have no amounts); watch list in red.
🗂 Groups: each group's spend, waste, how long it lasts (kept vs eating), best place to buy, nutrients, products.
"""

import datetime as dt

import altair as alt
import pandas as pd
import streamlit as st

from grocery import analytics, consumption, ingredients, productinfo, records, sheets
from grocery.views import product_ui
from grocery.views.common import back_home

DOT = {"green": "🟢", "orange": "🟠", "red": "🔴"}
EATEN, WASTED, LINE = "#2E8B5A", "#E8A33D", "#4A5059"
RED, GREEN, ORANGE, GRAY = "#E74C3C", "#2ECC71", "#F39C12", "#9AA0A6"
PERIODS = ["7 days", "30 days", "3 months", "Year"]


def _name(key):
    return key.replace("_", " ")


def _unit(key):
    return productinfo.NUTRIENT_UNITS.get(key, "")


def render():
    back_home()
    st.title("Insights")
    if not st.session_state.get("ingredients_tried"):
        # Once per visit: tidy the ingredient names of new products (one cheap AI call for all of them).
        st.session_state["ingredients_tried"] = True
        if any(p.get("ingredients") and ingredients.parsed(p) is None for p in records.products()):
            with st.spinner("Tidying ingredient names…"):
                ingredients.tidy_new(sheets.get_settings()["model_labels"])
    span = st.segmented_control("Period", PERIODS, default="30 days", key="ins_period",
                                label_visibility="collapsed") or "30 days"
    p0, p1, days = consumption.period(span)
    recs = consumption.eaten()
    st.caption(f"{p0:%b %d} – {p1:%b %d}. Only finished things count: what you ate of each is spread evenly from "
               "when you started it to when you finished it.")
    tabs = st.tabs(["📊 Overview", "🧪 Nutrients", "🧾 Ingredients", "🗂 Groups"], key="ins_tabs")
    with tabs[0]:
        _overview(recs, p0, p1, days)
    with tabs[1]:
        _nutrients(recs, p0, p1, days)
    with tabs[2]:
        _ingredients(recs, p0, p1)
    with tabs[3]:
        _groups(recs, p0, p1, days)


def _in_period(recs, p0, p1):
    return [r for r in recs if consumption.share_in(r, p0, p1) > 0]


def _coverage(recs, p0, p1):
    """📏 How much of what you ate is counted, and what's missing (so the numbers aren't read as complete)."""
    food = [r for r in _in_period(recs, p0, p1) if r["kind"] == "Food"]
    if not food:
        return
    counted = [r for r in food if r["counted"]]
    pct = round(100 * len(counted) / len(food))
    color = "green" if pct >= 80 else "orange" if pct >= 40 else "red"
    st.markdown(f":{color}-badge[📏 {len(counted)} of {len(food)} finished foods counted ({pct}%)]")
    missing = [r for r in food if not r["counted"]]
    if missing:
        with st.expander("Not counted yet"):
            st.markdown(" ".join(f":gray-badge[{r['product']}: {(r.get('problem') or 'weight') if r['has_nutrition'] else 'nutrition'}?]"
                                 for r in missing))
            st.caption("Open them in Pantry → 📊 Nutrition (or ⚠️ Needs info) to fill these in.")


def _overview(recs, p0, p1, days):
    ended = [r for r in recs if p0 <= r["end"] <= p1]
    finished_cost = sum(r["price"] or 0 for r in ended)
    wasted = sum(r["wasted_cost"] for r in ended)
    drinks = sum(r["pure_alcohol_floz"] * consumption.share_in(r, p0, p1) for r in recs) / 0.6
    waste_pct = 100 * wasted / finished_cost if finished_cost else 0
    st.markdown(f"### :blue-badge[💲 \\${consumption.spent(p0, p1):,.0f} spent] :green-badge[✅ {len(ended)} finished] "
                + (f":orange-badge[🗑 \\${wasted:,.0f} wasted · {waste_pct:.0f}%]" if wasted >= 0.5 else ":green-badge[🗑 nothing wasted]")
                + (f" :violet-badge[🍷 {drinks:.1f} drinks]" if drinks >= 0.05 else ""))
    st.caption("Spent: bought in this period (all kinds) · finished and wasted: finished in this period · "
               "drinks: US standard drinks (0.6 fl oz pure alcohol)")
    _coverage(recs, p0, p1)
    food = [r for r in recs if r["kind"] == "Food" and r["counted"]]
    got = consumption.totals(food, p0, p1)
    before = consumption.totals(food, p0 - dt.timedelta(days=days), p0 - dt.timedelta(days=1))
    badges = []
    for key, icon in (("calories", "🔥"), ("total_sugars", "🍬")):  # no daily value, but worth watching over time
        if key in got:
            now, then = got[key] / days, before.get(key, 0.0) / days
            change = (f" · {'▲' if now > then else '▼'} {abs(100 * (now - then) / then):.0f}% vs the {days} days before"
                      if then else "")
            badges.append(f":gray-badge[{icon} {now:,.0f} {_unit(key)}/day {_name(key)}{change}]")
    if badges:
        st.markdown(" ".join(badges))
    rows = []
    for key in consumption.LIMIT + consumption.GET:
        dv = productinfo.DAILY_VALUES.get(key)
        if not dv or key not in got:
            continue
        per_day = got[key] / days
        pct = 100 * per_day / dv
        limit = key in consumption.LIMIT
        color = (RED if pct > 100 else ORANGE if pct > 75 else GREEN) if limit else (GREEN if pct >= 100 else ORANGE if pct >= 50 else RED)
        rows.append({"nutrient": f"{'⬇' if limit else '⬆'} {_name(key)}", "pct": round(pct),
                     "label": f"{per_day:,.0f} {_unit(key)}/day · {pct:.0f}%", "color": color})
    st.subheader("Per day, vs daily value", anchor=False)
    if not rows:
        st.info("No finished food with nutrition in this period yet. It fills in as you finish things.")
        return
    df = pd.DataFrame(rows)
    base = alt.Chart(df).encode(y=alt.Y("nutrient:N", sort=None, title=None, axis=alt.Axis(labelLimit=160)))
    bars = base.mark_bar(cornerRadiusEnd=4).encode(x=alt.X("pct:Q", title=None, axis=None),
                                                   color=alt.Color("color:N", scale=None))
    rule = alt.Chart(pd.DataFrame({"x": [100]})).mark_rule(color=GRAY, strokeDash=[4, 3]).encode(x="x:Q")
    text = base.mark_text(align="left", dx=4, color=GRAY).encode(x="pct:Q", text="label:N")
    st.altair_chart((bars + rule + text).properties(height=30 * len(df)), width="stretch")
    st.caption("⬇ less is better (red over 100%) · ⬆ enough is good (green from 100%) · dashed line = daily value "
               "(US label, 2,000 kcal). From groceries you finished, so meals out aren't included.")


def _nutrients(recs, p0, p1, days):
    food = [r for r in recs if r["kind"] == "Food" and r["counted"]]
    keys = sorted({k for r in food for k in r["nutrients"]}, key=lambda k: list(productinfo.NUTRIENT_UNITS).index(k)
                  if k in productinfo.NUTRIENT_UNITS else 99)
    if not keys:
        st.info("No finished food with nutrition yet.")
        return
    key = st.selectbox("Nutrient", keys, index=keys.index("sodium") if "sodium" in keys else 0,
                       format_func=_name, key="ins_nutrient")
    weeks, start = [], p0
    while start <= p1:
        end = min(start + dt.timedelta(days=6), p1)
        total = consumption.totals(food, start, end).get(key, 0.0)
        weeks.append({"week": start.strftime("%b %d"), "per_day": total / ((end - start).days + 1)})
        start = end + dt.timedelta(days=1)
    dv = productinfo.DAILY_VALUES.get(key)
    chart = alt.Chart(pd.DataFrame(weeks)).mark_bar(color="#FF4B4B", cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X("week:N", sort=None, title=None), y=alt.Y("per_day:Q", title=f"{_unit(key)} per day"),
        tooltip=[alt.Tooltip("week:N"), alt.Tooltip("per_day:Q", format=",.0f")])
    if dv:
        chart = chart + alt.Chart(pd.DataFrame({"y": [dv]})).mark_rule(color=GRAY, strokeDash=[4, 3]).encode(y="y:Q")
    st.altair_chart(chart.properties(height=220), width="stretch")
    st.caption("Average per day, week by week" + (f" · dashed line = daily value ({dv:g} {_unit(key)})" if dv else ""))
    by = {}
    for r in food:
        s = consumption.share_in(r, p0, p1)
        if s and key in r["nutrients"]:
            by[r["group"]] = by.get(r["group"], 0.0) + r["nutrients"][key] * s
    total = sum(by.values())
    if total:
        st.subheader(f"Where your {_name(key)} came from", anchor=False)
        top = sorted(by.items(), key=lambda x: -x[1])[:10]
        df = pd.DataFrame([{"group": g, "pct": 100 * v / total, "label": f"{100 * v / total:.0f}%"} for g, v in top])
        base = alt.Chart(df).encode(y=alt.Y("group:N", sort="-x", title=None, axis=alt.Axis(labelLimit=160)))
        st.altair_chart((base.mark_bar(color="#FF4B4B", cornerRadiusEnd=4).encode(x=alt.X("pct:Q", axis=None, title=None))
                         + base.mark_text(align="left", dx=4, color=GRAY).encode(x="pct:Q", text="label:N"))
                        .properties(height=30 * len(df)), width="stretch")
    _coverage(recs, p0, p1)


def _ingredients(recs, p0, p1):
    food = [r for r in _in_period(recs, p0, p1) if r["kind"] == "Food" and r["ingredients"]]
    watch = ingredients.watch_list()
    with st.expander("✏️ Watch list"):
        text = st.text_input("Ingredients to watch (comma separated)", value=", ".join(watch), key="ins_watch")
        if st.button("Save watch list", key="ins_watch_save"):
            records.sheets.save_settings({"watch_list": text})
            sheets.update_rows("products", "product_id", {p["product_id"]: {"ingredients_norm": ""}
                                                          for p in records.products() if p.get("ingredients_norm")})
            st.session_state.pop("ingredients_tried", None)  # re-tidy against the new list
            st.rerun()
    if not food:
        st.info("No finished food with an ingredients list in this period yet.")
        return
    seen = {}  # one count per finished product, not per pack
    for r in food:
        seen.setdefault(r["product_id"] or r["product"], r)
    done = [(r, ingredients.parsed({"ingredients_norm": r["ingredients_norm"]})
             or ingredients.rough({"ingredients": r["ingredients"]})) for r in seen.values()]
    rough = sum(1 for _, d in done if d.get("rough"))
    n = len(seen)
    st.caption(f"{n} different finished foods with ingredient lists" +
               (f" · {rough} not tidied yet (matched by simple word search for now)" if rough else "") +
               ". Lists have no amounts, so this shows how often each ingredient appeared.")
    st.subheader("⚠️ Watch list", anchor=False)
    hits = {w: [r["product"] for r, d in done if w in d.get("watch", [])] for w in watch}
    st.markdown(" ".join((f":red-badge[{w} · {len(v)}]" if v else f":green-badge[{w} · 0]") for w, v in hits.items()))
    for w, v in hits.items():
        if v:
            st.caption(f"**{w}**: " + ", ".join(sorted(set(v))))
    counts = {}
    for r, d in done:
        for name in set(d.get("items", [])):
            counts[name] = counts.get(name, 0) + 1
    if counts:
        st.subheader("Most common ingredients", anchor=False)
        def on_watch(name, watched):  # "red 40" counts as an artificial color, "cane sugar" as added sugar
            return any(a in name or name == a for w in watched
                       for a in ingredients.ALIASES.get(w.lower(), ()) + (w.lower(),))
        flagged = {name for r, d in done for name in d.get("items", []) if on_watch(name, d.get("watch", []))}
        top = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:25]
        df = pd.DataFrame([{"ingredient": k, "pct": 100 * v / len(done), "label": f"{v} of {len(done)}",
                            "color": RED if k in flagged else "#5B8DEF"} for k, v in top])
        base = alt.Chart(df).encode(y=alt.Y("ingredient:N", sort=None, title=None, axis=alt.Axis(labelLimit=160)))
        st.altair_chart((base.mark_bar(cornerRadiusEnd=4).encode(x=alt.X("pct:Q", axis=None, title=None),
                                                                color=alt.Color("color:N", scale=None))
                         + base.mark_text(align="left", dx=4, color=GRAY).encode(x="pct:Q", text="label:N"))
                        .properties(height=26 * len(df)), width="stretch")
        st.caption("Bar = share of your finished foods that contain it · red = on your watch list")


def _groups(recs, p0, p1, days):
    items = [i for i in records.items() if i.get("tracked") != "no"]
    if not items:
        st.info("No items yet.")
        return
    stats = {}
    for b in records.live_batches():
        day = analytics._date(b.get("purchased_on"))
        if day and p0 <= day <= p1:
            stats.setdefault(b.get("item_id"), {"spent": 0.0})["spent"] += records._float(b.get("price_paid"))
    items.sort(key=lambda i: (-stats.get(i["item_id"], {}).get("spent", 0.0), i["name"].lower()))
    pick = st.selectbox("Group", items, key="ins_group", format_func=lambda i: f"{i.get('emoji', '')} {i['name']}"
                        + (f"  · ${stats[i['item_id']]['spent']:,.0f}" if i["item_id"] in stats else ""))
    pick = next((i for i in items if i["item_id"] == pick["item_id"]), pick)
    prods = [p for p in records.products() if p.get("item_id") == pick["item_id"]]
    with st.container(horizontal=True, vertical_alignment="center"):
        product_ui.photo({"photo_url": records.group_picture(pick)}, pick.get("emoji", ""), width=64)
        st.markdown(f"### {pick['name']}")
    choice = "All products"
    if len(prods) > 1:
        choice = st.segmented_control("Product", ["All products"] + [p["product_id"] for p in prods], default="All products",
                                      key=f"ins_prod_{pick['item_id']}",
                                      format_func=lambda x: x if x == "All products" else records.short_name(
                                          next(p for p in prods if p["product_id"] == x), pick)) or "All products"
    mine = [r for r in recs if r["item_id"] == pick["item_id"] and (choice == "All products" or r["product_id"] == choice)]
    ended = [r for r in mine if p0 <= r["end"] <= p1]
    spent_here = sum(records._float(b.get("price_paid")) for b in records.live_batches()
                     if b.get("item_id") == pick["item_id"] and (choice == "All products" or b.get("product_id") == choice)
                     and (analytics._date(b.get("purchased_on")) or dt.date.min) >= p0)
    finished_cost = sum(r["price"] or 0 for r in ended)
    wasted = sum(r["wasted_cost"] for r in ended)
    st.markdown(f":blue-badge[💲 \\${spent_here:,.0f} spent] :green-badge[✅ {len(ended)} finished] "
                + (f":orange-badge[🗑 {100 * wasted / finished_cost:.0f}% wasted]" if finished_cost and wasted >= 0.5
                   else ":green-badge[🗑 nothing wasted]"))
    cfg = analytics.settings()
    done = analytics.finished_batches(pick["item_id"])
    if choice != "All products":
        done = [b for b in done if b.get("product_id") == choice]
    summary = {"finished": done, "per_unit": analytics.usual_days_per_unit(done, cfg["window"]),
               "running": analytics.running_average(done, cfg["window"]),
               "eat_per_unit": analytics.usual_eating_days_per_unit(done, cfg["window"]),
               "any_started": any(b["started"] for b in done)}
    _lifespan(summary, pick)
    _best_place(analytics.best_place(done, cfg["best_min"]), pick, cfg)
    got = consumption.totals([r for r in mine if r["counted"]], p0, p1)
    if got:
        st.subheader("Nutrients from it, this period", anchor=False)
        st.markdown(" ".join(f":gray-badge[{_name(k)} {v:,.0f} {_unit(k)}]" for k, v in got.items()
                             if k in productinfo.NUTRIENT_UNITS))


def _best_place(best, item, cfg):
    st.subheader("Best place to buy", anchor=False)
    unit = item.get("unit", "unit")
    if best["ready"]:
        w = best["winner"]
        with st.container(border=True):
            st.markdown(f"### :green[{w['store']}]")
            st.markdown(f"{w['product']}  \n**${w['per_eaten_unit']:.2f}** per {unit} eaten")
    else:
        st.markdown(f":red-badge[not enough data] :gray[needs {cfg['best_min']}+ finished batches "
                    f"from 2 stores or products]")
    if best["options"]:
        with st.expander("Compare", expanded=not best["ready"]):
            for o in best["options"]:
                badge = "green" if o["enough"] else "red"
                price = f"${o['per_eaten_unit']:.2f}/{unit} eaten" if o["per_eaten_unit"] else "all wasted"
                st.markdown(f":{badge}-badge[{o['batches']}×] **{o['store']}** · {o['product']}  \n"
                            f"{price} · :orange[{o['wasted']:.0%} wasted] · :gray[shelf ${o['shelf_per_unit']:.2f}/{unit}]")


def _lifespan(summary, item):
    st.subheader("How long it lasts", anchor=False)
    done, unit = summary["finished"], item.get("unit", "unit")
    if summary["per_unit"]:
        per = "piece" if unit == "each" else unit
        c1, c2 = st.columns(2)
        c1.metric(f"🗓 Kept, per {per}", f"{summary['per_unit']:.1f} days", help="From the day it was bought to the day it was finished")
        if summary.get("any_started") and summary.get("eat_per_unit"):
            c2.metric(f"🍽 Eating time, per {per}", f"{summary['eat_per_unit']:.1f} days",
                      help="From the day you started it to the day it was finished (no start date = the bought date)")
    if not done:
        st.markdown(":red-badge[no finished batches yet]")
        return
    rows = []
    for n, (b, avg) in enumerate(zip(done, summary["running"]), start=1):
        label = f"{n}. {b['purchased_on'][5:]} {b.get('store', '')[:8]}" + (f" {b['pack_label']}" if b.get("pack_label") else "")
        rows.append({"batch": label, "n": n, "part": "Eaten", "days": b["days"] * b["eaten_share"]})
        rows.append({"batch": label, "n": n, "part": "Wasted", "days": b["days"] * b["wasted"]})
    bars = pd.DataFrame(rows)
    line = pd.DataFrame([{"batch": f"{n}. {b['purchased_on'][5:]} {b.get('store', '')[:8]}"
                                   + (f" {b['pack_label']}" if b.get("pack_label") else ""),
                          "usual": avg * b["units_num"]}
                         for n, (b, avg) in enumerate(zip(done, summary["running"]), start=1) if avg])
    order = list(dict.fromkeys(bars["batch"]))
    x = alt.X("batch:N", sort=order, title=None, axis=alt.Axis(labelAngle=-40))
    chart = alt.Chart(bars).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=x, y=alt.Y("sum(days):Q", title="days"),
        color=alt.Color("part:N", scale=alt.Scale(domain=["Eaten", "Wasted"], range=[EATEN, WASTED]),
                        legend=alt.Legend(orient="top", title=None)),
        order=alt.Order("part:N", sort="descending"),
        tooltip=["batch", "part", alt.Tooltip("days:Q", format=".0f")],
    )
    if not line.empty:
        chart = chart + alt.Chart(line).mark_line(color=LINE, strokeDash=[4, 3], point=True).encode(
            x=x, y="usual:Q", tooltip=[alt.Tooltip("usual:Q", title="usual for this size", format=".0f")])
    st.altair_chart(chart.properties(height=260), width="stretch")
    st.caption("Bars: days each batch lasted (orange = wasted share). Dashed line: usual lifespan for that pack size.")
