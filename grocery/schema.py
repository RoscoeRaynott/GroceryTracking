"""Google Sheet layout: one tab per kind of raw fact.

Every number the app shows is calculated from these rows, never stored.
Rows that point at other rows by ID also carry plain names so the Sheet
reads on its own.
"""

TABS = {
    "items": [
        "item_id", "name", "kind", "perishable", "unit", "storage",
        "emoji", "tracked", "created_at", "photo_url",
        "no_expiry",  # "yes": no date is printed on it (avocados); its lifespan comes from your own history
        "alcohol",  # "yes": an alcoholic drink (its ABV is looked up, so Insights can count alcohol); "no": not
    ],
    "products": [
        "product_id", "item_id", "item", "brand", "name", "barcode", "packaged",
        "serving_size", "servings_per_pack", "ingredients", "nutrients_json", "abv",
        "photo_url", "info_source", "info_url", "info_status", "source", "created_at", "details",
        "nicknames", "pack_size", "pack_unit",
        "nutrition_status",  # "none" = you said it has no nutrition label worth tracking
        "pack_count",  # how many separate units in one pack as sold (2 tubs); pack_size is the total
        "piece_grams",  # typical weight of one piece for items counted in "each" (USDA: medium banana = 118 g)
        "pack_varies",  # "yes": sold by weight, each pack differs (salmon, chicken); each purchase records its own weight
        "no_brand",  # "yes": it has no brand (loose avocados)
        "lookup_missed_on",  # date Look up all last found nothing; it skips the product for 30 days
        "group_suggestion",  # the group (item) the AI suggests for it
        "group_status",  # "ok": you confirmed its group
        "storage",  # where this product is kept; "" = its group's usual place
        "pack_grams",  # weight (g) or volume (ml) of one whole pack, when you gave it (for nutrition totals)
        "ingredients_norm",  # {"items": tidied ingredient names, "watch": watch-list entries it contains}
        "density",  # g per ml, entered by you, for a liquid whose serving is a weight (or the other way round)
    ],
    # One row per thing bought. Rows from the same receipt share a purchase_id.
    "batches": [
        "batch_id", "purchase_id", "item_id", "item", "product_id", "product",
        "store", "purchased_on", "purchased_time", "source", "units", "unit", "price_paid",
        "receipt_total", "expiry_date", "status", "closed_on", "created_at",
        "packs",  # packs as sold (5 crates); units = packs x the product's pack size
        "started_on",  # the day you started eating it; "" = the day it was bought
    ],
    "events": [
        "event_id", "change_id", "batch_id", "item", "type", "wasted_fraction", "happened_on",
        "expiry_date", "recorded_at", "source", "said", "previous", "undone",
        "packs",  # pack_finished: how many packs of a multi-pack purchase were finished
    ],
    "ai_readings": ["reading_id", "purchase_id", "store", "at", "task", "model", "reading"],
    "drafts": ["draft_id", "photo_no", "photo_name", "created_at", "model", "reading"],
    "settings": ["key", "value"],
    "ai_log": [
        "at", "task", "model", "input_tokens", "output_tokens", "seconds", "ok",
        "thought_tokens",  # thinking, billed as output
        "tool_tokens",  # Google search results the model read, billed as input
        "searches",  # Google searches it ran (each billed)
        "cost_usd",  # worked out from the prices in Settings
        "error",  # why it failed, e.g. RECITATION (Google blocked an answer that would copy label text)
    ],
}

# Starting values for the settings tab (all editable later in the app).
DEFAULT_SETTINGS = {
    "min_batches_before_warning": "3",
    "warning_multiplier": "1.25",
    "running_average_batches": "5",
    "expiry_warning_days": "2",
    "best_buy_min_batches": "2",
    "model_receipts": "gemini-3.8-flash",
    "model_voice": "gemini-3.8-flash",
    "model_labels": "gemini-3.5-flash-lite",
    "model_lookup": "gemini-3.8-flash",
    # Dollars per 1M tokens, input/output (thinking counts as output). From third-party price lists (Nov 2026):
    # check Google's pricing page and edit in Settings.
    "ai_prices": "gemini-3.8-flash=0.75/3.75; gemini-3.5-flash-lite=0.30/2.50",
    "price_per_1000_searches": "14",
    "watch_list": "high fructose corn syrup, palm oil, artificial colors, added sugar, hydrogenated oils, MSG, sodium nitrite",
}

KINDS = ["Food", "Household", "One-time"]
STORAGE = ["Fridge", "Freezer", "Open air"]
UNITS = ["each", "lb", "oz", "gal", "qt", "fl oz", "ml", "loads"]
