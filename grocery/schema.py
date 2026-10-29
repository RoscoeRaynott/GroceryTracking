"""Google Sheet layout: one tab per kind of raw fact.

Every number the app shows is calculated from these rows, never stored.
"""

TABS = {
    "items": [
        "item_id", "name", "kind", "perishable", "unit", "storage",
        "emoji", "tracked", "created_at",
    ],
    "products": [
        "product_id", "item_id", "brand", "name", "barcode", "pack_units",
        "serving_size", "servings_per_pack", "ingredients", "flags",
        "nutrients_json", "abv", "photo_url", "source", "created_at",
    ],
    "stores": ["store_id", "name", "type", "created_at"],
    "purchases": [
        "purchase_id", "store_id", "purchased_at", "source", "total_paid",
        "raw_ai", "notes", "created_at",
    ],
    "batches": [
        "batch_id", "purchase_id", "item_id", "product_id", "units",
        "price_paid", "expiry_date", "status", "closed_on", "created_at",
    ],
    "events": [
        "event_id", "batch_id", "type", "wasted_fraction", "happened_on",
        "recorded_at", "source", "said", "undone",
    ],
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
    "model_receipts": "",
    "model_voice": "",
    "model_labels": "",
}
