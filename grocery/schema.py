"""Google Sheet layout: one tab per kind of raw fact.

Every number the app shows is calculated from these rows, never stored.
Rows that point at other rows by ID also carry plain names so the Sheet
reads on its own.
"""

TABS = {
    "items": [
        "item_id", "name", "kind", "perishable", "unit", "storage",
        "emoji", "tracked", "created_at", "photo_url",
    ],
    "products": [
        "product_id", "item_id", "item", "brand", "name", "barcode", "packaged",
        "serving_size", "servings_per_pack", "ingredients", "nutrients_json", "abv",
        "photo_url", "info_source", "info_url", "info_status", "source", "created_at", "details",
        "nicknames",
    ],
    # One row per thing bought. Rows from the same receipt share a purchase_id.
    "batches": [
        "batch_id", "purchase_id", "item_id", "item", "product_id", "product",
        "store", "purchased_on", "purchased_time", "source", "units", "unit", "price_paid",
        "receipt_total", "expiry_date", "status", "closed_on", "created_at",
    ],
    "events": [
        "event_id", "change_id", "batch_id", "item", "type", "wasted_fraction", "happened_on",
        "expiry_date", "recorded_at", "source", "said", "previous", "undone",
    ],
    "ai_readings": ["reading_id", "purchase_id", "store", "at", "task", "model", "reading"],
    "drafts": ["draft_id", "photo_no", "photo_name", "created_at", "model", "reading"],
    "settings": ["key", "value"],
    "ai_log": [
        "at", "task", "model", "input_tokens", "output_tokens", "seconds", "ok",
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
}

KINDS = ["Food", "Household", "One-time"]
STORAGE = ["Fridge", "Freezer", "Open air"]
UNITS = ["each", "lb", "oz", "gal", "qt", "fl oz", "loads"]
