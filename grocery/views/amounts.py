"""Amounts typed the way packs print them: pounds + ounces, gallons + fluid ounces."""

import math

import streamlit as st

# unit -> (smaller unit, how many of it make one unit)
SPLIT = {"lb": ("oz", 16), "gal": ("fl oz", 128)}


def amount_input(label, value, unit, key, placeholder=None):
    """A number box in `unit`; for lb and gal a second box for oz / fl oz (starts at 0).
    Returns the total in `unit` (1 lb + 6 oz = 1.375), or None if left empty."""
    small = SPLIT.get(unit)
    if not small:
        return st.number_input(f"{label} ({unit})", value=value, min_value=0.0, step=1.0, format="%g", key=key,
                               placeholder=placeholder)
    name, per = small
    whole = math.floor(value + 1e-9) if value is not None else None
    part = round((value - whole) * per, 2) if value is not None else 0.0
    with st.container(horizontal=True):
        big = st.number_input(f"{label} ({unit})", value=float(whole) if whole is not None else None,
                              min_value=0.0, step=1.0, format="%g", key=key, placeholder=placeholder)
        extra = st.number_input(name, value=float(part), min_value=0.0, step=1.0, format="%g", key=f"{key}_oz")
    if big is None and not extra:
        return None
    return (big or 0.0) + (extra or 0.0) / per


def amount_text(units, unit):
    """1.375 lb -> "1 lb 6 oz"; 0.5 lb -> "8 oz"; other units -> "2,839 ml"."""
    try:
        units = float(units)
    except (TypeError, ValueError):
        return f"{units or ''} {unit or ''}".strip()
    small = SPLIT.get(unit)
    if not small or units == int(units):
        return f"{units:,g} {unit}"
    name, per = small
    whole = math.floor(units)
    part = round((units - whole) * per, 1)
    if part >= per:
        whole, part = whole + 1, 0
    if not whole:
        return f"{part:g} {name}"
    return f"{whole:,g} {unit} {part:g} {name}" if part else f"{whole:,g} {unit}"


def pack_input(total, count, unit, key):
    """One pack as sold = how many are in it × the size of each (2 tubs × 32 oz).
    Returns (total in `unit` or None, count)."""
    count = int(count) if count and float(count) >= 1 else 1
    each = total / count if total else None
    n = st.number_input("Items in one pack", value=count, min_value=1, step=1, key=f"{key}_n",
                        help="How many separate items the store sells together as one pack (a 12-roll pack = 12)")
    if unit == "each":  # counted in items: one item is 1 each, nothing more to ask
        st.caption(f"1 pack = **{n} each**")
        return float(n), n
    size = amount_input("Size of one item", each, unit, key=f"{key}_each", placeholder=f"{unit} in one item")
    if size is None:
        st.caption(f"Type the size of one item ({unit}) to save.")
        return None, n
    st.caption(f"1 pack = {n} × {amount_text(size, unit)} = **{amount_text(n * size, unit)}**")
    return n * size, n
