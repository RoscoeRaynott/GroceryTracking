"""Insights per item: best place to buy, usual lifespan, and a bar per finished batch."""

import altair as alt
import pandas as pd
import streamlit as st

from grocery import analytics, events, records, sheets
from grocery.schema import STORAGE
from grocery.views import product_ui
from grocery.views.common import back_home

DOT = {"green": "🟢", "orange": "🟠", "red": "🔴"}
EATEN, WASTED, LINE = "#2E8B5A", "#E8A33D", "#4A5059"


def render():
    back_home()
    st.title("Insights")

    items = [i for i in sheets.read_tab("items") if i.get("tracked") != "no"]
    if not items:
        st.info("No items yet.")
        return
    cfg = analytics.settings()
    batches, all_events = sheets.read_tab("batches"), events.all_events()
    levels = {i["item_id"]: analytics.data_level(i["item_id"], batches, all_events, cfg) for i in items}
    items.sort(key=lambda i: (-levels[i["item_id"]][1], i["name"]))

    st.caption(f"🟢 {cfg['min_batches']}+ finished · 🟠 some · 🔴 none yet")
    pick = st.selectbox("Item", items, format_func=lambda i: f"{DOT[levels[i['item_id']][0]]} "
                        f"{i.get('emoji', '')} {i['name']}  ({levels[i['item_id']][1]})",
                        label_visibility="collapsed", key="ins_item")
    _picture(pick)
    _kept_in(pick)
    summary = analytics.item_summary(pick["item_id"])
    _best_place(summary["best"], pick, cfg)
    _lifespan(summary, pick)
    _products(pick)


def _products(item):
    rows = [p for p in sheets.read_tab("products") if p.get("item_id") == item["item_id"]]
    if not rows:
        return
    st.subheader("Products", anchor=False)
    for p in rows:
        with st.container(border=True):
            with st.container(horizontal=True, vertical_alignment="center"):
                product_ui.photo(p, item.get("emoji", ""), width=56, item=item)
                st.markdown(f"**{p['name']}**  \n:gray[{p.get('brand', '')}]")
            product_ui.nicknames(p, f"ins_nick_{p['product_id']}")


def _picture(item):
    """The item's picture, with a way to swap it if two items look alike."""
    with st.container(horizontal=True, vertical_alignment="center"):
        product_ui.photo(None, item.get("emoji", ""), width=72, item=item)
        st.markdown(f"### {item['name']}")
        with st.popover("Change picture", icon=":material/image:", key=f"pic_{item['item_id']}"):
            word = st.text_input("Picture of…", key=f"picq_{item['item_id']}",
                                 placeholder="e.g. Egg white, Ilish, Half and half")
            if st.button("Find picture", key=f"picgo_{item['item_id']}", disabled=not word.strip()):
                pic = records.item_picture(word)
                if pic in ("", "none"):
                    st.warning("No picture found for that. Try another word.")
                else:
                    records.set_item_picture(item["item_id"], pic)
                    st.rerun()


def _kept_in(item):
    """Where this item is kept; changing it moves it in Pantry."""
    if item.get("kind") == "One-time":
        return
    current = item.get("storage") if item.get("storage") in STORAGE else "Fridge"
    choice = st.segmented_control("Kept in", STORAGE, default=current, key=f"kept_{item['item_id']}")
    if choice and choice != current:
        sheets.update_rows("items", "item_id", {item["item_id"]: {"storage": choice}})
        st.toast(f"{item['name']} now kept in {choice}")
        st.rerun()


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
        per = item["name"].lower() if unit == "each" else unit
        st.metric(f"Usual lifespan, per {per}", f"{summary['per_unit']:.1f} days")
    if not done:
        st.markdown(":red-badge[no finished batches yet]")
        return
    rows = []
    for n, (b, avg) in enumerate(zip(done, summary["running"]), start=1):
        label = f"{n}. {b['purchased_on'][5:]} {b.get('store', '')[:8]}"
        rows.append({"batch": label, "n": n, "part": "Eaten", "days": b["days"] * b["eaten_share"]})
        rows.append({"batch": label, "n": n, "part": "Wasted", "days": b["days"] * b["wasted"]})
    bars = pd.DataFrame(rows)
    line = pd.DataFrame([{"batch": f"{n}. {b['purchased_on'][5:]} {b.get('store', '')[:8]}",
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
