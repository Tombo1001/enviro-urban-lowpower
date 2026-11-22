"""Product photos for the diagrams, fetched from the vendors on first start.

The photos are the vendors' copyright, so none ship with this project. Each one
is downloaded once, cropped to the framing the callouts expect, and kept in
STATE_DIR/images. Until that works, or with PHOTOS=off, the page shows the
drawn diagrams in static/drawn instead.
"""
import io
import os
import threading
import urllib.request

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:  # the drawn diagrams need nothing
    Image = None

UA = "Mozilla/5.0 (compatible; enviro-urban-lowpower)"
MAX_BYTES = 8_000_000


def _square_crop(box):
    def process(im):
        # Pimoroni's shots are square, 1200 px wide on request.
        return im.resize((1200, 1200)).crop(box)
    return process


def _charger(im):
    # Adafruit's shot is on a dark background. Cut the board and its USB socket out.
    im = im.resize((970, 728))
    mask = Image.new("L", im.size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((266, 188, 714, 562), radius=40, fill=255)
    d.rounded_rectangle((240, 281, 300, 468), radius=12, fill=255)
    out = im.convert("RGBA")
    out.putalpha(mask.filter(ImageFilter.GaussianBlur(1.2)))
    return out.crop((232, 180, 722, 570))


def _battery(im):
    # Turn the cell on its side so the print reads left to right.
    im = im.resize((1200, 1200)).rotate(90, expand=True)
    s = 1200 / 700
    im = im.crop((int(100 * s), int(160 * s), int(595 * s), int(545 * s)))
    im.thumbnail((900, 900))
    return im


SOURCES = {
    "board-front": {
        "url": "https://cdn.shopify.com/s/files/1/0174/1800/products/EnviroUrban_1of3.jpg?width=1200",
        "credit": "Pimoroni", "aspect": 1.0, "process": _square_crop((225, 15, 925, 1185)),
    },
    "board-back": {
        "url": "https://cdn.shopify.com/s/files/1/0174/1800/products/EnviroUrban_2of3.jpg?width=1200",
        "credit": "Pimoroni", "aspect": 1.0, "process": _square_crop((245, 15, 945, 1185)),
    },
    "charger": {
        "url": "https://cdn-shop.adafruit.com/970x728/4410-04.jpg",
        "credit": "Adafruit", "aspect": 970 / 728, "process": _charger,
    },
    "battery": {
        "url": "https://www.dnkpower.com/wp-content/uploads/2023/05/3.7v-2000mAh-Lipo-Battery-1200x1200.jpg",
        "credit": "DNK Power", "aspect": 1.0, "process": _battery,
    },
}


class Photos:
    def __init__(self, state_dir):
        self.dir = os.path.join(state_dir, "images")
        self.enabled = os.environ.get("PHOTOS", "on").lower() not in ("off", "0", "false", "no")
        self.errors = {}

    def path(self, key):
        """The fetched photo for `key`, or None to use the drawn diagram."""
        if not self.enabled or key not in SOURCES:
            return None
        p = os.path.join(self.dir, key + ".webp")
        return p if os.path.exists(p) else None

    def status(self):
        return {k: {"photo": self.path(k) is not None, "credit": v["credit"], "error": self.errors.get(k)}
                for k, v in SOURCES.items()}

    def _fetch(self, key, src):
        url = os.environ.get("PHOTO_URL_" + key.upper().replace("-", "_"), src["url"])
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("image is too large")
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        if abs(im.width / im.height - src["aspect"]) > 0.03:
            raise ValueError(f"unexpected shape {im.width}x{im.height}, the vendor may have changed the photo")
        out = src["process"](im)
        os.makedirs(self.dir, exist_ok=True)
        tmp = os.path.join(self.dir, key + ".tmp")
        out.save(tmp, "WEBP", quality=88, method=6)
        os.replace(tmp, os.path.join(self.dir, key + ".webp"))

    def fetch_missing(self):
        if not self.enabled:
            return
        if Image is None:
            self.errors = {k: "Pillow is not installed" for k in SOURCES}
            return
        for key, src in SOURCES.items():
            if self.path(key):
                continue
            try:
                self._fetch(key, src)
                self.errors.pop(key, None)
                print(f"photos: fetched {key}", flush=True)
            except Exception as e:
                self.errors[key] = str(e) or repr(e)
                print(f"photos: {key} not fetched ({e!r}), using the drawn diagram", flush=True)

    def start(self):
        threading.Thread(target=self.fetch_missing, daemon=True).start()
