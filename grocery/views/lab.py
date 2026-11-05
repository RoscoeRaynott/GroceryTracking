"""Lookup lab: try the three kinds of product search one at a time and see what each brings back.

A test bench only: nothing here is saved to your products. Each search is still logged in ai_log.
"""

import json
import time

import streamlit as st

from grocery import productinfo, records, sheets, usda
from grocery.gemini import GeminiError
from grocery.views.common import back_home, go

PARTS = [("product", "🏷 Product only", "name, brand, photo, pack size, link"),
         ("nutrition", "📊 Nutrition only", "serving and nutrients (numbers)"),
         ("ingredients", "🧾 Ingredients only", "the ingredients list"),
         ("ingredient_items", "🧩 Ingredients as separate items", "each ingredient as its own short word")]


def render():
    if st.button("Settings", icon=":material/arrow_back:", type="tertiary", key="lab_back"):
        go("settings")
    st.title("🧪 Lookup lab")
    st.caption("Try each kind of search on one product and see what comes back, or whether Google blocks it "
               "(RECITATION). Nothing here is saved.")

    _usda_test()
    st.divider()

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
    elif part == "ingredient_items":
        items = [str(x) for x in (r.get("ingredient_items") or []) if str(x).strip()]
        st.markdown(" ".join(f":gray-badge[{x[:40]}]" for x in items) or "No ingredients.")
    else:
        st.write(r.get("ingredients") or "No ingredients.")
    if r.get("reason"):
        st.caption(r["reason"])
    for title, url in res.get("sources", [])[:3]:
        st.markdown(f"- [{title or url}]({url})")


def _usda_test():
    """Shows USDA's raw answer (trimmed) so its format can be checked. Nothing is saved; the key is never shown."""
    with st.container(border=True):
        st.markdown("**📚 USDA test** :gray[raw answer, to copy into the chat]")
        if not usda.api_key():
            st.warning("No USDA_API_KEY found in Streamlit secrets.")
            return
        q = st.text_input("Food", value="banana raw", key="lab_usda_q")
        if st.button("Ask USDA", key="lab_usda_go", type="primary") and q.strip():
            with st.spinner("Asking USDA…"):
                try:
                    found = usda.search(q.strip())
                    foods = found.get("foods") or []
                    detail = usda.food(foods[0]["fdcId"]) if foods else {}
                    st.session_state["lab_usda_out"] = json.dumps(_trim(found, detail), indent=1)[:12000]
                except Exception as ex:
                    st.session_state["lab_usda_out"] = f"ERROR: {ex}"
        if st.session_state.get("lab_usda_out"):
            st.code(st.session_state["lab_usda_out"], language="json")


def _trim(found, detail):
    """Just the parts that matter: the matches, their nutrients, and the first match's portions."""
    def nutrients(rows):
        out = []
        for n in rows or []:
            name = n.get("nutrientName") or (n.get("nutrient") or {}).get("name")
            unit = n.get("unitName") or (n.get("nutrient") or {}).get("unitName")
            value = n.get("value", n.get("amount"))
            if name and value is not None:
                out.append(f"{name} = {value} {unit}")
        return out
    return {
        "totalHits": found.get("totalHits"),
        "matches": [{"fdcId": f.get("fdcId"), "description": f.get("description"), "dataType": f.get("dataType"),
                     "keys": sorted(f.keys()), "nutrients": nutrients(f.get("foodNutrients"))[:40]}
                    for f in (found.get("foods") or [])[:3]],
        "first_match_detail": {
            "keys": sorted(detail.keys()),
            "description": detail.get("description"), "dataType": detail.get("dataType"),
            "foodPortions": [{k: p.get(k) for k in ("amount", "gramWeight", "modifier", "portionDescription",
                                                     "measureUnit", "sequenceNumber")}
                             for p in (detail.get("foodPortions") or [])][:10],
            "nutrient_row_example": (detail.get("foodNutrients") or [None])[0],
        },
    }
