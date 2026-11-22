"""Checks for estimate.py against synthetic discharges and charges.

Run: python3 app/test_estimate.py
"""
import json
import os
import random
import tempfile
from datetime import datetime, timezone

import estimate as E


def v_for_soc(pct):
    for (v1, s1), (v2, s2) in zip(E.OCV, E.OCV[1:]):
        if s2 <= pct <= s1:
            return v2 + (pct - s2) / (s1 - s2) * (v1 - v2)
    return E.OCV[-1][0]


def synth(path, hours, interval_min, every, true_scale, start_soc=90.0, seed=1, charge=None):
    """Write a JSONL discharge where real drain is true_scale x the model.

    charge=(start_h, end_h, mA) puts a charger on the cell for that window.
    """
    rnd = random.Random(seed)
    t0 = t = datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()
    mah = start_soc / 100 * E.CAPACITY_MAH
    with open(path, "w") as f:
        for i in range(int(hours * 60 / interval_min)):
            h = (t - t0) / 3600
            on = charge and charge[0] <= h < charge[1]
            p = {"usb": 0, "reset_cause": "power_on", "wake": "timer",
                 "interval_min": interval_min, "pm_every_n": every,
                 # At full the charger stops and the cell settles a little under 4.2 V.
                 "battery_v": round((4.18 if on and mah >= E.CAPACITY_MAH - 1 else
                                     min(4.2, v_for_soc(mah / E.CAPACITY_MAH * 100) + (0.03 if on else 0)))
                                    + rnd.gauss(0, 0.003), 3),
                 "awake_ms": 5500, "temperature": 20, "humidity": 50, "pressure": 1000,
                 "noise": 0.03, "rssi": -50}
            if every and i % every == 0:
                p["awake_ms"] = 38000
                p["battery_v_load"] = round(p["battery_v"] - 0.014, 3)
            ts = datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            f.write(json.dumps({"ts": ts, "topic": "enviro/enviro-001/state", "retained": False, "payload": p}) + "\n")
            mah -= E.wake_charge(p) * true_scale
            if on and mah < E.CAPACITY_MAH:
                mah = min(E.CAPACITY_MAH, mah + charge[2] * interval_min / 60)
            t += interval_min * 60


def run(hours, interval_min, every, true_scale, **kw):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "m.jsonl")
        synth(path, hours, interval_min, every, true_scale, **kw)
        s = E.Store(path)
        s.refresh()
        return E.summarise(s.devices["enviro-001"], 0)["battery"]


def main():
    # Too little data: stay on the model and say so.
    b = run(1, 5, 4, 3.0)
    assert b["method"] == "model" and b["scale"] == {"mid": 1.0, "lo": 1.0, "hi": E.PRIOR_PESSIMISTIC_SCALE}, b

    # Enough data: the fit recovers the true drain multiple.
    for true_scale, hours, interval, every in ((1.0, 48, 5, 4), (3.0, 24, 5, 4), (2.0, 72, 15, 4), (3.5, 12, 1, 4)):
        b = run(hours, interval, every, true_scale)
        s = b["scale"]
        print(f"true x{true_scale}  {hours} h @ {interval} min: fitted x{s['mid']:.2f} "
              f"({s['lo']:.2f} to {s['hi']:.2f}), {b['confidence']}, "
              f"days {b['days']['short']:.1f} to {b['days']['long']:.1f}, soc {b['soc']:.0f}%")
        assert b["method"] == "measured", b
        assert s["lo"] <= true_scale <= s["hi"], (true_scale, s)
        assert abs(s["mid"] - true_scale) / true_scale < 0.2, (true_scale, s)

    # A charger on the cell: detected while it runs, and the old fit is carried over.
    for interval in (5, 15):
        b = run(16, interval, 4, 0.65, start_soc=60, charge=(13, 99, 100))
        c = b["charging"]
        assert c and b["method"] == "measured" and b["carried"], b
        began = (c["since_ts"] - datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()) / 3600
        print(f"charging @ {interval} min: detected from {began:.2f} h (true 13), start soc {c['soc_start']:.0f}%, "
              f"carried x{b['scale']['mid']:.2f}")
        assert 13 <= began <= 13.6, began
        # Charger off again: back to a discharge run, flagged as following a charge.
        b = run(40, interval, 4, 0.65, start_soc=60, charge=(13, 20, 100))
        assert b["charging"] is None and b["run"]["after_charge"], b
        print(f"   after 7 h on charge then 20 h off: x{b['scale']['mid']:.2f}, carried={b['carried']}, soc {b['soc']:.0f}%")
    # Left on the charger past full: the charge is called finished, not open for ever.
    b = run(30, 5, 4, 0.65, start_soc=60, charge=(13, 99, 500))
    assert b["charging"] is None and b["run"]["after_charge"], b
    print(f"left on charger past full: charge closed, soc {b['soc']:.0f}%")
    # Mid-charge at the same point it is still open.
    assert run(14.5, 5, 4, 0.65, start_soc=60, charge=(13, 99, 500))["charging"]
    # One stray low reading in the middle of a charge does not end it.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "m.jsonl")
        synth(path, 14, 5, 4, 0.65, start_soc=60, charge=(13, 99, 500))
        rows = [json.loads(l) for l in open(path)]
        rows[-6]["payload"]["battery_v"] -= 0.067
        open(path, "w").write("".join(json.dumps(r) + "\n" for r in rows))
        st = E.Store(path)
        st.refresh()
        assert E.summarise(st.devices["enviro-001"], 0)["battery"]["charging"], "a single dip ended the charge"
        print("single dip during a charge ignored")
    # No false charge detection in plain discharges.
    for seed in range(1, 30):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.jsonl")
            synth(path, 48, 5, 4, 1.0, seed=seed)
            st = E.Store(path)
            st.refresh()
            b = E.summarise(st.devices["enviro-001"], 0)["battery"]
            assert b["charging"] is None and b["run"]["n"] == 576, (seed, b["run"])

    # Two boards in one log are kept apart, and other topics are ignored.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "m.jsonl")
        synth(path, 6, 5, 4, 1.0)
        lines = open(path).read().splitlines()
        other = [l.replace("enviro-001", "enviro-002") for l in lines[:10]]
        junk = ['{"ts": "2026-11-01T00:00:00Z", "topic": "homeassistant/status", "retained": false, "payload": {"x": 1}}',
                '{"ts": "2026-11-01T00:00:00Z", "topic": "enviro/enviro-003/state", "retained": false, "payload": "not json"}',
                '{"broken']
        open(path, "w").write("\n".join(lines + other + junk) + "\n")
        st = E.Store(path)
        st.refresh()
        assert sorted(st.devices) == ["enviro-001", "enviro-002"], sorted(st.devices)
        assert len(st.devices["enviro-001"].wakes) == 72 and len(st.devices["enviro-002"].wakes) == 10
        json.dumps(E.summarise(st.devices["enviro-002"], 0), allow_nan=False)
        print("two boards split, junk ignored")
    print("ok")


if __name__ == "__main__":
    main()
