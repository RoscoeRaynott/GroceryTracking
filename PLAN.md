# GroceryTracking — Build Plan (v1)

_Draft for review. Edit this file freely (on GitHub or by asking Claude). **No code is written until you approve it.** Requirements live in `SPEC.md`; this file is how we build them._

---

## 1. What we're building, in one paragraph

A phone-first Streamlit app that records every grocery purchase (from receipt photos, order screenshots, barcode photos, or voice), sorts each line into **food / household consumable / one-time buy**, and keeps each purchase as a **batch** until you say by voice that it's finished or partly wasted. From that history it learns **how long each item lasts per unit**, warns you about forgotten perishables, tells you the **best place to buy** each item (store + brand + pack size), and counts the **ingredients and nutrients** you actually ate. All data lives in a Google Sheet you can read.

---

## 2. Tech stack

| Piece | Choice | Why |
|---|---|---|
| App | **Streamlit** (Python) | Your choice. Deploys from this GitHub repo |
| Storage | **Google Sheets** via `gspread` + `google-auth` (service account) | Same as Bookshelf Scanner. Human-readable |
| AI | **Gemini API** (AI Studio key, Tier 1), called over REST | Reads images **and** audio. Same pattern as Bookshelf Scanner |
| Model choice | **Configurable per task** in Settings | Lets you run arenas later |
| Barcode decoding | `zxing-cpp` (Python library) on the photo | Free, local, no API cost |
| Product info | **Open Food Facts** API (free) | Ingredients, nutrition, additives, palm-oil flag, photo |
| Charts | Altair (ships with Streamlit) | No extra install |

**Secrets** (Streamlit → Settings → Secrets), same layout as Bookshelf Scanner:
```toml
GEMINI_API_KEY = "..."
[gsheets]
spreadsheet_url = "https://docs.google.com/spreadsheets/d/..."
[gcp_service_account]
# the service account JSON fields
```

---

## 3. Google Sheet layout (raw facts only)

Every number shown in the app (lifespans, costs, verdicts, nutrient totals) is **calculated from these tabs**, never stored. Changing a formula recalculates all history.

| Tab | One row per… | Key columns |
|---|---|---|
| `items` | generic item (Avocado, Whole milk, Laundry detergent – pods) | name, kind (food / consumable / one-time), perishable (Y/N), standard unit (each / gal / lb / fl oz / loads), storage (open air / fridge / freezer), emoji, tracked (Y/N) |
| `products` | specific product (Kirkland Avocados 6ct) | item, brand, name, barcode, pack size in standard units, serving size, servings per pack, ingredients, flags (HFCS, palm oil, artificial colors…), nutrients per serving (amount + %DV), ABV %, photo URL, info source (Open Food Facts / label photo) |
| `stores` | store | name, type (chain / local) |
| `purchases` | shopping trip | store, date & time, source (receipt / screenshot / barcode / voice), notes |
| `batches` | one item line from a trip | purchase, item, product, quantity in standard units, price paid (after discounts, no tax), expiry date, status (open / closed), closed date |
| `events` | anything said or done to a batch | batch, type (finished / wasted / expiry set / edit), wasted fraction, **date it happened** (supports "3 days ago"), date recorded, source (voice / manual), what you said, undone (Y/N) |
| `settings` | one setting | e.g. min batches before warnings = 3, warning multiplier = 1.25, running-average N = 5–10, expiry warning days = 2, model per task |
| `ai_log` | one AI call | task, model, tokens, time taken. Feeds future arenas |

---

## 4. Screens (phone-first)

Bottom-to-top priority is the build order (section 6). The full visual dashboard is **later**.

1. **Add Purchase**
   - **Receipt / screenshot**: add 1+ photos → AI reads them (detects printed receipt vs app screenshot, merges overlapping photos, applies discounts, drops tax) → **Review**.
   - **Barcode photo**: one item at a time → Open Food Facts → if not found, asks for an ingredients-label photo (once per product, remembered forever) → **Review**.
   - **Voice quick-add**: "6 avocados, the 2-gallon milk, and a salmon fillet from Costco" → matched to past purchases with last price → **Review**.
2. **Review** (shared by all of the above)
   - One **card per item**, no wide tables. **Problems first**: unreadable lines, "how many did you buy?" overlap questions, unknown products.
   - Each card: item name, product/brand, quantity + unit, price, kind, storage dropdown (new items), perishable toggle.
   - If an item already has an open batch: "**Is your last X finished?** Yes / No".
   - Save.
3. **Voice Update**
   - Tap mic → speak ("half the spinach and two avocados rotted; butter finished 3 days ago; milk expires Nov 2").
   - **Preview list of exactly what will change** → Approve.
   - **Recent changes** list with **Undo**.
4. **Pantry** (simple v1): open batches grouped by **Open air / Fridge / Freezer**, days open, warnings (overdue vs usual lifespan, expiry in ≤2 days).
5. **Insights**, per item:
   - **Best place to buy** verdict, or "Not enough data yet". Tap for the explanation (price per eaten unit, waste).
   - **Lifespan bar chart**: one bar per batch, wasted part shaded, running-average line.
   - **Nutrients & flags**: monthly totals (amounts + %DV; flag ingredients as servings). Chart design will be refined later.
6. **Settings**: models per task, formula numbers, Sheet connection check.

---

## 5. The formulas (v1, all editable later)

- **Standard units**: everything is converted to one unit per item (½-gal jar = 0.5 gal; 10 apples = 10 each).
- **Wasted share** of a batch = sum of its waste events (fraction of the batch).
- **Lifespan of a batch** = date closed − purchase date.
- **Usual days per unit** (over the last N finished batches):
  `Σ(eaten share × days) ÷ Σ(units eaten)`, where `units eaten = units bought × (1 − wasted share)`.
  A fully spoiled batch adds nothing.
- **Expected lifespan of an open batch** = units × usual days per unit.
- **Forgot-it warning** (perishables only, after ≥3 finished batches): days open > 1.25 × expected lifespan. **Expiry warning**: 2 days before a known expiry.
- **Price per eaten unit** = price paid ÷ units eaten.
- **Best place to buy**: compare **store + brand + pack size** options by average price per eaten unit. Only shown when ≥2 options each have ≥2 finished batches.
- **Servings eaten** = servings per pack × packs × (1 − wasted share), spread evenly over the batch's days, and split across months by days.
  - **Nutrients**: servings × amount per serving (and %DV).
  - **Flag ingredients**: count of servings containing them.

---

## 6. Build phases

Each phase ends with **you testing it on your Pixel** before the next one starts.

| Phase | Delivers | You'll need to |
|---|---|---|
| **0. Setup** | App skeleton, Sheet connection, tabs auto-created, Settings page | Create a new Google Sheet, share it with the service account, add secrets, deploy on Streamlit |
| **1. Receipts → Review → Save** | Multi-photo receipts & screenshots, overlap merge, discount handling, item classification, mobile card review | Test with real Costco / Walmart / Safeway receipts and app screenshots |
| **2. Barcodes & product info** | Barcode photo → Open Food Facts → label-photo fallback, remembered per product | Scan a few packaged items |
| **3. Voice** | Finished / wasted / backdated / expiry updates with preview + undo; voice quick-add purchases | Try real phrases |
| **4. Lifespan & best place to buy** | Usual lifespan, Pantry list with warnings, per-item bar chart, verdicts with explanations | Use it for a few weeks so there's data |
| **5. Nutrition & ingredients** | Monthly nutrient %DV and flag-ingredient servings | Decide which charts and flags matter (we discuss first) |

**Later (not in v1):** full visual dashboard with pictures · chart redesign · live barcode scanning · model arenas · Hey Google / Alexa · multiple users with logins.

---

## 7. Known risks (being upfront)

- **Photo upload on phones** was painful in Bookshelf Scanner. We test this first in Phase 1 and pick whichever Streamlit input works best on your Pixel.
- **Receipt reading is not perfect**: abbreviations, faded print, overlapping photos. This is why Review always comes before Save.
- **Open Food Facts coverage** is patchy for store brands (Kirkland, Great Value). The label photo covers the gap.
- **Streamlit on mobile** has layout limits. We'll use single-column cards and big buttons, but it won't feel exactly like a native app.
- **Insights need time**: verdicts and warnings stay "not enough data" for the first weeks.

---

## 8. Still to discuss (doesn't block Phases 0–3)

- Dashboard details and pictures
- Chart designs and which nutrients/flags to highlight
- Overall look & feel (colors, theme)
