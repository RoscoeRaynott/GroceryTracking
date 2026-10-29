"""Getting photos from the phone into the app.

Lessons from Bookshelf Scanner:
- shrink photos on the phone before upload, so big files don't drop the connection;
- copy each photo into session memory the moment it arrives, so it survives the
  reconnect that happens when Android pauses the tab while you're in the gallery;
- Pixel "High efficiency" photos are HEIC: fall back to the standard uploader and
  decode them on the server with pillow-heif.
"""

import base64
import io
import os

import streamlit as st
import streamlit.components.v1 as components

try:
    from PIL import Image, ImageOps
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

MAX_DIM = 3072
_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploader")
_uploader = components.declare_component("receipt_uploader", path=_DIR)


def pick_photos(queue_key):
    """Show the uploader and add any new photos to st.session_state[queue_key].

    Each photo is {"name": str, "data": jpeg bytes}.
    """
    queue = st.session_state.setdefault(queue_key, [])
    nonce = st.session_state.setdefault(f"{queue_key}_nonce", 0)

    if st.session_state.get(f"{queue_key}_fallback"):
        files = st.file_uploader(
            "Receipt photos or screenshots", type=["jpg", "jpeg", "png", "webp", "heic", "heif"],
            accept_multiple_files=True, key=f"{queue_key}_std_{nonce}",
        )
        seen = st.session_state.setdefault(f"{queue_key}_seen", set())
        for f in files or []:
            tag = f"{f.name}:{f.size}"
            if tag in seen:
                continue
            seen.add(tag)
            try:
                queue.append({"name": f.name, "data": to_jpeg(f.getvalue())})
            except Exception as ex:
                st.error(f"Couldn't open {f.name}: {ex}")
        if st.button("Use the quick uploader instead", type="tertiary"):
            st.session_state[f"{queue_key}_fallback"] = False
            st.session_state[f"{queue_key}_nonce"] = nonce + 1
            st.rerun()
        return

    value = _uploader(key=f"{queue_key}_up_{nonce}")
    if not isinstance(value, dict):
        return
    if value.get("need_fallback"):
        st.session_state[f"{queue_key}_fallback"] = True
        st.rerun()
    batch = value.get("batch_id")
    if batch and batch != st.session_state.get(f"{queue_key}_batch"):
        st.session_state[f"{queue_key}_batch"] = batch
        for f in value.get("files", []):
            data_url = f.get("data", "")
            try:
                data = base64.b64decode(data_url.split(",", 1)[-1])
            except Exception:
                continue
            if data:
                queue.append({"name": f.get("name", "photo.jpg"), "data": data})


def to_jpeg(raw):
    """Decode any supported image (incl. HEIC), fix rotation, shrink, return JPEG bytes."""
    if not PIL_AVAILABLE:
        return raw
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    img = img.convert("RGB")
    img.thumbnail((MAX_DIM, MAX_DIM))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()
