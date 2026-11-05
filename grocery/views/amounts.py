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
