"""Lookup lab: try the three kinds of product search one at a time and see what each brings back.

A test bench only: nothing here is saved to your products. Each search is still logged in ai_log.
"""

import time

import streamlit as st

from grocery import productinfo, records, sheets
from grocery.gemini import GeminiError
from grocery.views.common import back_home, go

PARTS = [("product", "🏷 Product only", "name, brand, photo, pack size, link"),
         ("nutrition", "📊 Nutrition only", "serving and nutrients (numbers)"),
         ("ingredients", "🧾 Ingredients only", "the ingredients list")]


def render():
    if st.button("Settings", icon=":material/arrow_back:", type="tertiary", key="lab_back"):
        go("settings")
    st.title("🧪 Lookup lab")
    st.caption("Try each kind of search on one product and see what comes back, or whether Google blocks it "
               "(RECITATION). Nothing here is saved.")

    live = records.live_batches()
    in_pantry = {b.get("product_id") for b in live if b.get("status") == "open"}
    products = [p for p in records.products() if p["product_id"] in in_pantry] or records.products()
    if not products:
        st.info("No products yet.")
        return
    product = st.selectbox("Product", products, format_func=lambda p: f"{p['name']}  ·  {p.get('brand', '')}",
                           key="lab_product")
    pid = product["product_id"]
    store = records.lookup_store(product)
    query = st.text_area("Search for", value=records.product_search_text(product), key=f"lab_q_{pid}")
    st.markdown(f":blue-badge[searches {store}'s site first]" if store else ":gray-badge[searches anywhere]")
    unit = product.get("pack_unit") or records.item_unit(product["item_id"])

    for part, label, what in PARTS:
        with st.container(border=True):
            with st.container(horizontal=True, vertical_alignment="center"):
                go_now = st.button(label, key=f"lab_go_{part}_{pid}", type="primary")
                st.caption(what)
            if go_now:
                with st.spinner("Searching…"):
                    st.session_state[f"lab_res_{part}_{pid}"] = _run(part, query, unit, store)
            res = st.session_state.get(f"lab_res_{part}_{pid}")
            if res:
                _show(part, res)


def _run(part, query, unit, store):
    started = time.time()
    try:
        reply, sources = productinfo.lab_lookup(sheets.get_settings()["model_lookup"], part, query, unit, store)
        return {"ok": True, "reply": reply, "sources": sources, "secs": time.time() - started}
    except GeminiError as ex:
        blocked = "RECITATION" in str(ex)
        return {"ok": False, "blocked": blocked, "error": str(ex), "secs": time.time() - started}
    except Exception as ex:
        return {"ok": False, "blocked": False, "error": f"{type(ex).__name__}: {ex}", "secs": time.time() - started}


def _show(part, res):
    secs = f":gray-badge[⏱ {res['secs']:.0f}s]"
    if not res["ok"]:
        badge = ":red-badge[🚫 blocked (RECITATION)]" if res["blocked"] else ":orange-badge[⚠️ failed]"
        st.markdown(f"{badge} {secs}")
        st.caption(res["error"][:300])
        return
    r = res["reply"]
    found = r.get("product_found", r.get("found"))
    st.markdown((":green-badge[✅ got an answer]" if found else ":orange-badge[not found]") + f" {secs}")
    if part == "product":
        if r.get("image_url"):
            st.image(r["image_url"], width=90)
        st.markdown(f"**{r.get('matched_name', '')}**  \n:gray[{r.get('matched_brand', '')}]")
        st.markdown(f":violet-badge[📦 {r.get('pack_text') or '?'}] "
                    f":blue-badge[count {r.get('pack_count') or '?'} × {r.get('each_in_unit') or '?'}]")
    elif part == "nutrition":
        st.caption(f"Serving: {r.get('serving_size') or '?'} · servings per pack: {r.get('servings_per_pack') or '?'}")
        n = r.get("nutrients") or {}
        st.markdown(" ".join(f":gray-badge[{k.replace('_', ' ')} {v.get('amount')}]" for k, v in n.items()
                             if isinstance(v, dict)) or ":gray[no nutrients]")
    else:
        st.write(r.get("ingredients") or "No ingredients.")
    if r.get("reason"):
        st.caption(r["reason"])
    for title, url in res.get("sources", [])[:3]:
        st.markdown(f"- [{title or url}]({url})")
