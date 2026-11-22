"""Turn the message log into each board's dashboard summary: latest readings,
battery estimate, anomalies and chart series.

Pure functions over the log. Nothing here talks to MQTT or writes a file.
"""
import json
import math
import os
import statistics

# Approximate resting LiPo voltage -> state of charge (%). Tune after a full discharge.
OCV = [(4.20, 100), (4.10, 90), (4.00, 80), (3.92, 70), (3.87, 60), (3.82, 50),
       (3.79, 40), (3.77, 30), (3.74, 20), (3.68, 10), (3.45, 5), (3.30, 0)]

CAPACITY_MAH = 2000.0
DEFAULT_SETTINGS = {"capacity_mah": 2000, "charge_ma": 100, "charge_interval_min": 0, "auto": None, "name": None}
EMPTY_V = 3.5            # treat as empty, for the cell's health
LOW_V = 3.6              # highlight below this
I_AWAKE_MA = 70.0        # Pico W + WiFi average while awake (assumed)
I_FAN_EXTRA_MA = 150.0   # PM fan through the 5 V boost, on top of I_AWAKE_MA (assumed)
PM_WARMUP_S = 30.0
SLEEP_UA = 5.0
DEFAULT_INTERVAL_MIN = 1
DEFAULT_PM_EVERY = 4
# The worst drain seen in early testing was about 3.6 times the model. Until a
# fit exists, that ratio is the pessimistic bound.
PRIOR_PESSIMISTIC_SCALE = 3.6
MIN_FIT_WAKES = 20
MIN_FIT_HOURS = 2.0
MAX_POINTS = 4000

ANOMALY_FIELDS = {
    "last_fault": ("serious", "Previous wake died at stage"),
    "last_error": ("serious", "Exception on a previous wake"),
    "latch_held": ("critical", "Board stayed powered after cutting power"),
    "rtc_power_lost": ("serious", "RTC reset, the battery dropped out"),
    "config_error": ("serious", "Remote config rejected"),
    "rtc_reconfigured": ("notice", "RTC settings rewritten"),
    "config_changed": ("notice", "Remote config applied"),
}


def soc(v):
    if v >= OCV[0][0]:
        return 100.0
    for (v1, s1), (v2, s2) in zip(OCV, OCV[1:]):
        if v2 <= v <= v1:
            return s2 + (v - v2) / (v1 - v2) * (s1 - s2)
    return 0.0


def parse_ts(s):
    from datetime import datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


class DeviceLog:
    """Everything logged for one board."""

    def __init__(self, device_id):
        self.id = device_id
        self.wakes = []   # {"t": epoch, "p": payload}, non-retained state messages
        self.pm = []      # same shape, non-retained pm messages
        self.sets = []    # remote config messages, retained replays included
        self.seen = 0.0   # last message of any kind, retained replays included
        self.version = 0


class Store:
    """Incrementally reads messages.jsonl and splits it by board.

    Topics look like <prefix>/<device id>/<state|pm|set>. A board is discovered
    the first time any of its messages shows up. Only complete lines are consumed.
    """

    def __init__(self, path):
        self.path = path
        self._reset()

    def _reset(self):
        self.offset = 0
        self.inode = None
        self.devices = {}
        self.version = 0

    def refresh(self):
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return
        if st.st_ino != self.inode or st.st_size < self.offset:
            self._reset()
            self.inode = st.st_ino
        if st.st_size == self.offset:
            return
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            chunk = f.read()
        end = chunk.rfind(b"\n")
        if end < 0:
            return
        self.offset += end + 1
        self.version += 1
        for line in chunk[:end].split(b"\n"):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                if not isinstance(r.get("payload"), dict):
                    continue
                parts = r["topic"].split("/")
                if len(parts) < 3 or parts[-1] not in ("state", "pm", "set"):
                    continue
                rec = {"t": parse_ts(r["ts"]), "p": r["payload"]}
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            dev = self.devices.get(parts[-2])
            if dev is None:
                dev = self.devices[parts[-2]] = DeviceLog(parts[-2])
            dev.version += 1
            dev.seen = max(dev.seen, rec["t"])
            if parts[-1] == "set":
                dev.sets.append(rec)
            elif r.get("retained"):
                continue  # a replay from the broker, not a new wake
            elif parts[-1] == "state":
                dev.wakes.append(rec)
            else:
                dev.pm.append(rec)


def num(p, key):
    v = p.get(key)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def is_pm_wake(p):
    return "battery_v_load" in p


def wake_charge(p):
    """Modelled charge for one wake, mAh, before calibration."""
    q = (num(p, "awake_ms") or 5500) / 1000 * I_AWAKE_MA / 3600
    if is_pm_wake(p):
        q += PM_WARMUP_S * I_FAN_EXTRA_MA / 3600
    return q


def linfit(xs, ys):
    """Least squares. Returns slope, intercept, standard error of the slope."""
    n = len(xs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if n < 3 or sxx <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    icpt = my - slope * mx
    sse = sum((y - (icpt + slope * x)) ** 2 for x, y in zip(xs, ys))
    se = math.sqrt(sse / (n - 2) / sxx)
    return slope, icpt, se


def cadence(wakes):
    interval, every, source = DEFAULT_INTERVAL_MIN, DEFAULT_PM_EVERY, "assumed"
    for w in reversed(wakes):
        if num(w["p"], "interval_min"):
            interval = w["p"]["interval_min"]
            every = num(w["p"], "pm_every_n")
            every = DEFAULT_PM_EVERY if every is None else every
            source = "reported"
            break
    return {"interval_min": interval, "pm_every_n": every, "source": source}


CHARGE_STEP_V = 0.012     # two readings this far above the recent median start a charge
CHARGE_SURE_V = 0.05       # one reading this far above is enough
CHARGE_END_DROP_V = 0.025  # a sudden drop this big means the charger came off
FULL_V = 4.15
RELAX_S = 1800             # skip this long after a charge before fitting drain


def split_runs(batt):
    """Split battery wakes into discharge runs and charge periods.

    A charger wired to the cell shows up as the voltage stepping up and then
    climbing. Returns (runs, charge) where runs is a list of (start, end,
    after_charge) index ranges and charge is the open charge period, if any.
    """
    v = [w["p"]["battery_v"] for w in batt]
    runs, start, after_charge = [], 0, False
    base, cand = [], None      # recent discharge readings, index of a possible charge start
    charging, cstart, soc_start, v_start = False, None, None, None
    for i, w in enumerate(batt):
        if not charging:
            if w["p"].get("rtc_power_lost") and i > start:
                runs.append((start, i, after_charge))
                start, after_charge, base, cand = i, False, [], None
            med = statistics.median(base[-5:]) if len(base) >= 3 else None
            if med is not None and v[i] - med >= CHARGE_STEP_V:
                # A small step needs a second reading to confirm. A big one doesn't.
                if cand is None and v[i] - med < CHARGE_SURE_V:
                    cand = i
                    continue
                charging, cstart = True, i if cand is None else cand
                soc_start, v_start = soc(med), med
                runs.append((start, cstart, after_charge))
                cand = None
                continue
            if cand is not None:
                base.append(v[cand])
                cand = None
            base.append(v[i])
            continue
        seg = v[cstart:i + 1]
        # Two low readings in a row. A real board showed a single 67 mV dip in
        # the middle of a charge, and one reading must not end it.
        before = seg[-5:-2]
        dropped = bool(before) and max(before) - max(seg[-2:]) >= CHARGE_END_DROP_V
        sagging = len(seg) >= 6 and statistics.median(seg[-3:]) < statistics.median(seg[-6:-3]) - 0.008
        # A swapped or externally charged cell steps up once and then never climbs.
        stalled = (len(seg) >= 6 and batt[i]["t"] - batt[cstart]["t"] >= 3600 and v[i] < FULL_V - 0.03
                   and statistics.median(seg[-3:]) - statistics.median(seg[:3]) < 0.005)
        # Once full, the charger stops and nothing steps down sharply. Watch for
        # the voltage settling 15 mV under its peak, however slowly it gets there.
        med3 = [statistics.median(seg[k - 2:k + 1]) for k in range(2, len(seg))]
        topped = bool(med3) and max(med3) >= FULL_V and med3[-1] <= max(med3) - 0.015
        if dropped or sagging or stalled or topped:
            charging = False
            start = cstart if stalled else i
            after_charge = not stalled
            base = list(seg) if stalled else [v[i]]
    charge = None
    if charging:
        charge = {"start": cstart, "soc_start": soc_start, "v_start": v_start}
    else:
        # Hold an unconfirmed high reading out of the run so it can't bend the fit.
        runs.append((start, len(batt) if cand is None else cand, after_charge))
    return [r for r in runs if r[1] > r[0]], charge


def fit_run(batt, run, capacity):
    """Regress remaining mAh (from voltage) on cumulative modelled mAh for one run."""
    i0, i1, after_charge = run
    pts = batt[i0:i1]
    if after_charge:
        settled = [w for w in pts if w["t"] - pts[0]["t"] >= RELAX_S]
        pts = settled if len(settled) >= MIN_FIT_WAKES else pts
    if len(pts) < 2:
        return None
    xs, ys, x = [], [], 0.0
    for i, w in enumerate(pts):
        if i:
            x += SLEEP_UA / 1000 * (w["t"] - pts[i - 1]["t"]) / 3600
        ys.append(soc(w["p"]["battery_v"]) / 100 * capacity)
        xs.append(x)           # voltage is read before this wake spends its charge
        x += wake_charge(w["p"])
    hours = (pts[-1]["t"] - pts[0]["t"]) / 3600
    out = {"hours": hours, "n": len(pts), "modelled_mah": x, "scale": None, "note": None}
    if len(pts) >= MIN_FIT_WAKES and hours >= MIN_FIT_HOURS:
        fit = linfit(xs, ys)
        if fit and -fit[0] > 0.2:
            s, se = -fit[0], fit[2]
            # The OCV table is approximate, so never claim better than +/-25%.
            margin = max(2 * se, 0.25 * s)
            out["scale"] = {"mid": s, "lo": max(0.2, s - margin), "hi": s + margin}
            out["confidence"] = "low" if hours < 12 else "medium" if hours < 48 else "good"
        else:
            out["note"] = "Voltage has not dropped measurably yet, so the fit is not usable."
    return out


def battery_estimate(wakes, cad, capacity=CAPACITY_MAH):
    batt = [w for w in wakes if w["p"].get("usb") == 0 and num(w["p"], "battery_v")]
    if not batt:
        return None
    runs, charge = split_runs(batt)

    # Typical charge per wake from recent wakes of each kind.
    base = [wake_charge(w["p"]) for w in wakes[-200:] if not is_pm_wake(w["p"])]
    pm = [wake_charge(w["p"]) for w in wakes[-200:] if is_pm_wake(w["p"])]
    q_base = statistics.median(base) if base else 5.5 * I_AWAKE_MA / 3600
    q_pm = statistics.median(pm) if pm else (38 * I_AWAKE_MA + PM_WARMUP_S * I_FAN_EXTRA_MA) / 3600
    sleep_day = SLEEP_UA * 24 / 1000

    # Use the newest run that has a usable fit. An older one is carried over
    # while the current run is too short, or while the cell is charging.
    fits = [fit_run(batt, r, capacity) for r in runs]
    current = fits[-1] if fits and not charge else None
    method, confidence, carried, fit_note = "model", "none", False, None
    scale = {"mid": 1.0, "lo": 1.0, "hi": PRIOR_PESSIMISTIC_SCALE}
    for f in reversed(fits):
        if f and f["scale"]:
            scale, method, confidence = f["scale"], "measured", f["confidence"]
            carried = f is not current
            break
    if method == "model" and current:
        fit_note = current["note"]

    run = batt[runs[-1][0]:runs[-1][1]] if runs and not charge else batt[charge["start"]:]
    v_now = statistics.median([w["p"]["battery_v"] for w in run[-5:]])
    soc_now = soc(v_now)
    soc_empty = soc(EMPTY_V)
    remaining = max(0.0, (soc_now - soc_empty) / 100 * capacity)
    hours = (run[-1]["t"] - run[0]["t"]) / 3600
    vt = linfit([(w["t"] - run[0]["t"]) / 3600 for w in run],
                [w["p"]["battery_v"] for w in run]) if len(run) >= 5 else None

    def per_day(interval_min, every):
        n = 1440 / interval_min
        n_pm = n / every if every else 0
        return (n - n_pm) * q_base + n_pm * q_pm + sleep_day

    model_day = per_day(cad["interval_min"], cad["pm_every_n"])
    mah_day = {k: model_day * scale[k] for k in scale}
    # A high drain scale gives the short end of the range.
    days = {"mid": remaining / mah_day["mid"], "short": remaining / mah_day["hi"],
            "long": remaining / mah_day["lo"]}

    sags = [(w["p"]["battery_v"] - w["p"]["battery_v_load"]) * 1000
            for w in run if num(w["p"], "battery_v_load")]

    charging = None
    if charge:
        first = batt[charge["start"]]
        charging = {
            "since_ts": first["t"],
            "soc_start": charge["soc_start"],
            "v_start": charge["v_start"],
            "full": v_now >= FULL_V,
            "board_ma": mah_day["mid"] / 24,
        }

    return {
        "capacity_mah": capacity,
        "empty_v": EMPTY_V,
        "low_v": LOW_V,
        "soc_empty": soc_empty,
        "v": v_now,
        "v_last": run[-1]["p"]["battery_v"],
        "v_ts": run[-1]["t"],
        "soc": soc_now,
        "remaining_mah": remaining,
        "low": v_now < LOW_V,
        "sag_mv_latest": sags[-1] if sags else None,
        "sag_mv_median": statistics.median(sags) if sags else None,
        "run": {
            "start_ts": run[0]["t"], "hours": hours, "n": len(run),
            "v_start": run[0]["p"]["battery_v"],
            "mv_per_h": vt[0] * 1000 if vt else None,
            "after_charge": bool(runs and runs[-1][2]) and not charge,
        },
        "charging": charging,
        "method": method,
        "confidence": confidence,
        "carried": carried,
        "fit_note": fit_note,
        "scale": scale,
        "per_wake": {"base_mah": q_base, "pm_mah": q_pm, "sleep_mah_day": sleep_day},
        "mah_day": mah_day,
        "days": days,
    }


def find_events(wakes):
    events = []
    for i, w in enumerate(wakes):
        p, t = w["p"], w["t"]
        for key, (sev, text) in ANOMALY_FIELDS.items():
            if key in p and p[key] not in (False, None, 0, ""):
                val = p[key]
                if isinstance(val, list):
                    val = ", ".join(str(v) for v in val)
                detail = "" if val is True or val == 1 else str(val)
                events.append({"ts": t, "severity": sev, "kind": key, "text": text, "detail": detail})
        if p.get("usb") == 0 and p.get("reset_cause") not in (None, "power_on"):
            events.append({"ts": t, "severity": "serious", "kind": "reset_cause",
                           "text": "Woke on battery without a power-on reset",
                           "detail": str(p.get("reset_cause"))})
        if i:
            prev = wakes[i - 1]
            expected = (num(prev["p"], "interval_min") or DEFAULT_INTERVAL_MIN) * 60
            gap = t - prev["t"]
            if gap > 1.5 * expected + 45:
                missed = max(1, round(gap / expected) - 1)
                events.append({"ts": prev["t"], "severity": "warning", "kind": "missed_wake",
                               "text": f"Missed about {missed} wake{'s' if missed != 1 else ''}",
                               "detail": f"{gap / 60:.1f} min gap, expected {expected / 60:g} min"})
    return events


CONFIG_KEYS = {"INTERVAL_MIN": "interval_min", "PM_EVERY_N": "pm_every_n"}


def config_state(dev):
    """The last remote config message and whether the board has taken it up."""
    if not dev.sets:
        return None
    last = dev.sets[-1]
    wanted = {field: last["p"][key] for key, field in CONFIG_KEYS.items() if key in last["p"]}
    if not wanted:
        return None
    since = [w for w in dev.wakes if w["t"] >= last["t"]]
    state, error = "pending", None
    # The board reports the cadence it will use next, so the newest wake decides.
    for w in since:
        if all(w["p"].get(f) == v for f, v in wanted.items()):
            state, error = "applied", None
        elif "config_error" in w["p"]:
            state, error = "rejected", str(w["p"]["config_error"])
    if not since and dev.wakes and all(dev.wakes[-1]["p"].get(f) == v for f, v in wanted.items()):
        state = "applied"
    return {"ts": last["t"], "wanted": wanted, "raw": last["p"], "state": state,
            "error": error, "wakes_since": len(since)}


def summarise(dev, now, settings=None):
    wakes, pms = dev.wakes, dev.pm
    cad = cadence(wakes)
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    out = {
        "now": now,
        "device": {"id": dev.id, "fw": None},
        "cadence": cad,
        "config": config_state(dev),
        "latest": None, "latest_pm": None,
        "settings": settings,
        "battery": battery_estimate(wakes, cad, float(settings["capacity_mah"])),
        "events": [], "stats": {"wakes": len(wakes)},
        "series": None, "pm_series": None, "recent": [],
    }
    if not wakes:
        return out

    last = wakes[-1]
    out["device"]["fw"] = last["p"].get("fw")
    out["latest"] = {"ts": last["t"], **last["p"]}
    if pms:
        out["latest_pm"] = {"ts": pms[-1]["t"], **pms[-1]["p"]}

    events = find_events(wakes)
    out["events"] = events[::-1][:200]
    out["stats"] = {
        "wakes": len(wakes),
        "battery_wakes": sum(1 for w in wakes if w["p"].get("usb") == 0),
        "pm_wakes": len(pms),
        "missed_gaps": sum(1 for e in events if e["kind"] == "missed_wake"),
        "first_ts": wakes[0]["t"],
        "span_h": (last["t"] - wakes[0]["t"]) / 3600,
    }

    stride = max(1, math.ceil(len(wakes) / MAX_POINTS))
    pick = wakes[::stride]
    if pick[-1] is not last:
        pick.append(last)
    on_batt = [w["p"].get("usb") == 0 for w in pick]
    out["series"] = {
        "t": [round(w["t"]) for w in pick],
        "usb": [0 if b else 1 for b in on_batt],
        "battery_v": [num(w["p"], "battery_v") if b else None for w, b in zip(pick, on_batt)],
        "battery_v_load": [num(w["p"], "battery_v_load") if b else None for w, b in zip(pick, on_batt)],
        "temperature": [num(w["p"], "temperature") for w in pick],
        "humidity": [num(w["p"], "humidity") for w in pick],
        "pressure": [num(w["p"], "pressure") for w in pick],
        "noise": [num(w["p"], "noise") for w in pick],
        "rssi": [num(w["p"], "rssi") for w in pick],
        "awake_s": [None if num(w["p"], "awake_ms") is None else w["p"]["awake_ms"] / 1000 for w in pick],
    }
    pstride = max(1, math.ceil(len(pms) / MAX_POINTS))
    ppick = pms[::pstride]
    out["pm_series"] = {
        "t": [round(w["t"]) for w in ppick],
        "pm1": [num(w["p"], "pm1") for w in ppick],
        "pm2_5": [num(w["p"], "pm2_5") for w in ppick],
        "pm10": [num(w["p"], "pm10") for w in ppick],
    }
    keep = ("battery_v", "battery_v_load", "usb", "temperature", "humidity", "pressure",
            "noise", "rssi", "awake_ms", "wake", "reset_cause")
    out["recent"] = [{"ts": w["t"], **{k: w["p"].get(k) for k in keep}} for w in wakes[-40:][::-1]]
    return out
