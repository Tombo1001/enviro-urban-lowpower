"""Enviro Urban dashboard server.

Logs MQTT messages from the boards, serves the web page, and answers its API.
Standard library only, apart from Pillow for the optional product photos.

Environment:
  MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASSWORD   broker login
  MQTT_TOPIC       topic filter to log (default enviro/#)
  MQTT_SUBSCRIBE   "0" to read a log another process writes (default 1)
  DATA_DIR         where messages.jsonl lives (default ./data)
  STATE_DIR        settings and fetched photos (default DATA_DIR)
  STATIC_DIR       built web page (default ../frontend/dist)
  FIRMWARE_DIR     board firmware to hand out on the Provision tab (default ../firmware)
  PHOTOS           "off" to use the drawn diagrams only
  HOST, PORT       listen address (default 0.0.0.0:8870)
"""
import json
import mimetypes
import os
import re
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

import mqtt
import provision
from estimate import DEFAULT_SETTINGS, Store, summarise
from images import Photos

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(HERE, "..", "data"))
STATE_DIR = os.environ.get("STATE_DIR", DATA_DIR)
STATIC_DIR = os.environ.get("STATIC_DIR", os.path.join(HERE, "..", "frontend", "dist"))
FIRMWARE_DIR = os.environ.get("FIRMWARE_DIR", os.path.join(HERE, "..", "firmware"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8870"))
TOPIC = os.environ.get("MQTT_TOPIC", "enviro/#")
SUBSCRIBE = os.environ.get("MQTT_SUBSCRIBE", "1").lower() not in ("0", "false", "no", "off")
DATA_FILE = os.path.join(DATA_DIR, "messages.jsonl")
SETTINGS_FILE = os.path.join(STATE_DIR, "settings.json")
TOPIC_PREFIX = TOPIC.split("/")[0] if "/" in TOPIC else "enviro"

INTERVALS = {1, 5, 15, 30, 60}
PM_EVERY = {1, 2, 4, 8, 12}
DEVICE_KEYS = ("capacity_mah", "charge_ma", "charge_interval_min", "auto", "name")
AUTO_MAX_S = 24 * 3600

store = Store(DATA_FILE)
photos = Photos(STATE_DIR)
subscriber = None
lock = threading.Lock()
cache = {}       # device id -> (key, summary)
settings = {"defaults": {}, "devices": {}}
settings_rev = 0
wake_auto = threading.Event()


# ---- settings ---------------------------------------------------------------

def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    if "devices" in data or "defaults" in data:
        settings["defaults"] = dict(data.get("defaults") or {})
        settings["devices"] = dict(data.get("devices") or {})
    elif data:
        # A settings file from before boards had their own settings.
        settings["legacy"] = {k: data[k] for k in DEVICE_KEYS if k in data}


def write_settings():
    """Call with the lock held."""
    global settings_rev
    settings_rev += 1
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=1)
    os.replace(tmp, SETTINGS_FILE)


def device_settings(device_id):
    """Call with the lock held. Defaults overlaid with this board's own values."""
    if "legacy" in settings and device_id in store.devices:
        settings["devices"].setdefault(device_id, {}).update(settings.pop("legacy"))
        write_settings()
    return {**DEFAULT_SETTINGS, **settings["defaults"], **settings["devices"].get(device_id, {})}


def update_device(device_id, new):
    with lock:
        settings["devices"].setdefault(device_id, {}).update(new)
        write_settings()


def check_battery_fields(body, allow):
    new = {}
    if "capacity_mah" in body:
        cap = body["capacity_mah"]
        if not isinstance(cap, (int, float)) or isinstance(cap, bool) or not 100 <= cap <= 20000:
            raise ValueError("capacity_mah must be between 100 and 20000")
        new["capacity_mah"] = round(cap)
    if "charge_ma" in body:
        if body["charge_ma"] not in (100, 500):
            raise ValueError("charge_ma must be 100 or 500")
        new["charge_ma"] = body["charge_ma"]
    if "charge_interval_min" in body and "charge_interval_min" in allow:
        if body["charge_interval_min"] not in (0, 1, 5):
            raise ValueError("charge_interval_min must be 0 (off), 1 or 5")
        new["charge_interval_min"] = body["charge_interval_min"]
    if "name" in body and "name" in allow:
        name = body["name"]
        if name is not None and (not isinstance(name, str) or len(name) > 40):
            raise ValueError("name must be text of up to 40 characters")
        new["name"] = (name or "").strip() or None
    if not new:
        raise ValueError("nothing to change")
    return new


def save_device_settings(device_id, body):
    new = check_battery_fields(body, DEVICE_KEYS)
    update_device(device_id, new)
    if "charge_interval_min" in new:
        wake_auto.set()
    return new


def save_defaults(body):
    new = check_battery_fields(body, ())
    with lock:
        settings["defaults"].update(new)
        write_settings()
    return new


# ---- reading the log --------------------------------------------------------

def get_summary(device_id):
    """A board's summary as a dict, or None if it has never been seen."""
    with lock:
        store.refresh()
        dev = store.devices.get(device_id)
        if dev is None:
            return None
        key = (dev.version, settings_rev)
        hit = cache.get(device_id)
        if not hit or hit[0] != key:
            hit = cache[device_id] = (key, summarise(dev, time.time(), device_settings(device_id)))
        return {**hit[1], "now": time.time()}


def overview():
    with lock:
        store.refresh()
        ids = sorted(store.devices)
    devices = []
    for device_id in ids:
        s = get_summary(device_id)
        latest, battery = s["latest"], s["battery"]
        devices.append({
            "id": device_id,
            "name": s["settings"].get("name"),
            "fw": s["device"]["fw"],
            "last_ts": latest["ts"] if latest else None,
            "interval_min": s["cadence"]["interval_min"],
            "pm_every_n": s["cadence"]["pm_every_n"],
            "usb": bool(latest and latest.get("usb")),
            "battery_v": battery["v_last"] if battery else None,
            "soc": battery["soc"] if battery else None,
            "charging": bool(battery and battery["charging"]),
            "wakes": s["stats"]["wakes"],
            "capacity_mah": s["settings"]["capacity_mah"],
            "charge_ma": s["settings"]["charge_ma"],
        })
    with lock:
        defaults = {**DEFAULT_SETTINGS, **settings["defaults"]}
    return {
        "now": time.time(),
        "devices": devices,
        "defaults": {"capacity_mah": defaults["capacity_mah"], "charge_ma": defaults["charge_ma"]},
        "mqtt": mqtt_status(),
        "photos": photos.status(),
        "firmware": provision.describe(FIRMWARE_DIR),
    }


def mqtt_status():
    out = {"configured": mqtt.configured(), "topic": TOPIC, "mode": "subscribe" if SUBSCRIBE else "file",
           "host": os.environ.get("MQTT_HOST", ""), "port": int(os.environ.get("MQTT_PORT", "1883"))}
    if subscriber:
        out.update(subscriber.status)
    return out


# ---- sending config to a board ----------------------------------------------

def publish_config(device_id, interval, every):
    with lock:
        store.refresh()
        dev = store.devices.get(device_id)
        if dev is None:
            raise ValueError("no such board")
        # Keep any other keys from the last config message.
        payload = dict(dev.sets[-1]["p"]) if dev.sets else {}
    payload.update({"INTERVAL_MIN": interval, "PM_EVERY_N": every})
    mqtt.publish_retained(f"{TOPIC_PREFIX}/{device_id}/set", json.dumps(payload, separators=(",", ":")))
    return payload


def send_cadence(device_id, body):
    """Publish a retained config message for the board to read at its next wake."""
    interval, every = body.get("interval_min"), body.get("pm_every_n")
    if interval not in INTERVALS or every not in PM_EVERY:
        raise ValueError("interval_min or pm_every_n is not one of the allowed values")
    with lock:
        auto = device_settings(device_id).get("auto") or {}
    if auto.get("active"):
        # On charge: this is the battery cadence to return to. Only a changed PM
        # frequency goes to the board now, with the on-charge interval kept.
        if every != auto["restore"]["pm_every_n"]:
            publish_config(device_id, auto["interval_min"], every)
        update_device(device_id, {"auto": {**auto, "restore": {"interval_min": interval, "pm_every_n": every}}})
        return {"INTERVAL_MIN": interval, "PM_EVERY_N": every}, True
    return publish_config(device_id, interval, every), False


def auto_step(device_id):
    """Speed a board up while a charge is detected, and put it back after."""
    summary = get_summary(device_id)
    battery, cad, now = summary["battery"], summary["cadence"], time.time()
    charging = battery and battery["charging"]
    want = summary["settings"].get("charge_interval_min") or 0
    auto = summary["settings"].get("auto") or {}
    if auto.get("active"):
        why = ("switched off" if not want else "charge ended" if not charging
               else "24 hour limit" if now - auto["started"] > AUTO_MAX_S else None)
        if why:
            r = auto["restore"]
            publish_config(device_id, r["interval_min"], r["pm_every_n"])
            update_device(device_id, {"auto": {**auto, "active": False, "ended": now, "why": why}})
            print(f"{device_id}: on-charge cadence off ({why}), back to every {r['interval_min']} min", flush=True)
        elif want != auto["interval_min"]:
            publish_config(device_id, want, auto["restore"]["pm_every_n"])
            update_device(device_id, {"auto": {**auto, "interval_min": want}})
    elif charging and want and charging["since_ts"] != auto.get("charge_ts"):
        new = {"active": False, "charge_ts": charging["since_ts"]}
        if cad["interval_min"] > want:
            publish_config(device_id, want, cad["pm_every_n"])
            new = {"active": True, "charge_ts": charging["since_ts"], "started": now, "interval_min": want,
                   "restore": {"interval_min": cad["interval_min"], "pm_every_n": cad["pm_every_n"]}}
            print(f"{device_id}: charge detected, on-charge cadence every {want} min", flush=True)
        update_device(device_id, {"auto": new})


def auto_loop():
    while True:
        with lock:
            store.refresh()
            ids = list(store.devices)
        for device_id in ids:
            try:
                auto_step(device_id)
            except Exception as e:  # a failed publish is retried on the next pass
                print(f"{device_id}: on-charge cadence: {e!r}", flush=True)
        wake_auto.wait(20)
        wake_auto.clear()


# ---- http -------------------------------------------------------------------

DEVICE_ROUTE = re.compile(r"^/api/devices/([A-Za-z0-9_.-]{1,64})/(summary|settings|cadence)$")
DRAWN = {"board-front", "board-back", "charger", "battery"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=STATIC_DIR, **kw)

    def send_json(self, code, obj):
        body = json.dumps(obj, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, download_name=None):
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        ctype = "text/plain; charset=utf-8" if path.endswith(".py") else mimetypes.guess_type(path)[0]
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = unquote(self.path.split("?")[0])
        try:
            if path == "/api/overview":
                return self.send_json(200, overview())
            m = DEVICE_ROUTE.match(path)
            if m and m.group(2) == "summary":
                summary = get_summary(m.group(1))
                return self.send_json(200, summary) if summary else self.send_json(404, {"error": "no such board"})
            if path.startswith("/img/") and path[5:] in DRAWN:
                photo = photos.path(path[5:])
                return self.send_file(photo or os.path.join(STATIC_DIR, "drawn", path[5:] + ".svg"))
            if path == "/api/firmware/bundle.zip":
                body = provision.bundle(FIRMWARE_DIR)
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", 'attachment; filename="enviro-urban-lowpower.zip"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if path.startswith("/api/firmware/"):
                found = provision.resolve(FIRMWARE_DIR, path[len("/api/firmware/"):])
                return self.send_file(found) if found else self.send_json(404, {"error": "not found"})
            if path.startswith("/api/"):
                return self.send_json(404, {"error": "not found"})
        except Exception as e:  # keep serving, report the failure
            return self.send_json(500, {"error": repr(e)})
        super().do_GET()

    def do_POST(self):
        path = unquote(self.path.split("?")[0])
        try:
            # Browsers can't send this content type cross-site without a preflight.
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("send JSON")
            n = int(self.headers.get("Content-Length", "0"))
            if n > 20000:
                raise ValueError("body too large")
            body = json.loads(self.rfile.read(n))
            if not isinstance(body, dict):
                raise ValueError("send a JSON object")
            m = DEVICE_ROUTE.match(path)
            if m and m.group(1) not in store.devices:
                return self.send_json(404, {"error": "no such board"})
            if m and m.group(2) == "settings":
                return self.send_json(200, {"saved": save_device_settings(m.group(1), body)})
            if m and m.group(2) == "cadence":
                try:
                    sent, deferred = send_cadence(m.group(1), body)
                except ValueError:
                    raise
                except Exception as e:
                    return self.send_json(502, {"error": f"MQTT publish failed: {e}"})
                print(f"{m.group(1)}: {'stored' if deferred else 'sent'} config {sent}", flush=True)
                return self.send_json(200, {"sent": sent, "deferred": deferred})
            if path == "/api/settings":
                return self.send_json(200, {"saved": save_defaults(body)})
            return self.send_json(404, {"error": "not found"})
        except ValueError as e:
            return self.send_json(400, {"error": str(e)})
        except Exception as e:
            return self.send_json(500, {"error": repr(e)})

    def end_headers(self):
        if not self.path.startswith("/assets/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # one line per request is noise at a 10 second poll


def main():
    global subscriber
    load_settings()
    photos.start()
    if SUBSCRIBE and mqtt.configured():
        subscriber = mqtt.Subscriber(DATA_FILE, TOPIC)
        subscriber.start()
    elif SUBSCRIBE:
        print("MQTT_HOST is not set, so nothing will be logged", flush=True)
    threading.Thread(target=auto_loop, daemon=True).start()
    print(f"dashboard on http://{HOST}:{PORT}  log={DATA_FILE}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
