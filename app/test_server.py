"""Checks for server.py with MQTT publishing stubbed out: settings, the on-charge
cadence automation and the firmware hand-out.

Run: python3 app/test_server.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp()
os.environ.update(DATA_DIR=tmp, STATE_DIR=tmp, PHOTOS="off", MQTT_SUBSCRIBE="0",
                  FIRMWARE_DIR=os.path.join(HERE, "..", "firmware"))
sys.path.insert(0, HERE)

import provision  # noqa: E402
import server  # noqa: E402
import test_estimate as T  # noqa: E402

sent = []
server.mqtt.publish_retained = lambda topic, payload: sent.append((topic, json.loads(payload)))
BOARD = "enviro-001"


def use(hours, charge=None):
    T.synth(server.DATA_FILE, hours, 15, 4, 0.65, start_soc=60, charge=charge)
    server.store._reset()
    server.cache.clear()


def auto():
    return server.get_summary(BOARD)["settings"]["auto"]


def raises(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return True
    return False


def main():
    # An old flat settings file moves to the one board it was written for.
    with open(server.SETTINGS_FILE, "w") as f:
        json.dump({"charge_ma": 500, "capacity_mah": 1200}, f)
    server.load_settings()
    use(16, charge=(13, 99, 100))
    s = server.get_summary(BOARD)["settings"]
    assert (s["charge_ma"], s["capacity_mah"]) == (500, 1200), s
    assert "legacy" not in server.settings and BOARD in server.settings["devices"]

    # Bad values are refused.
    assert raises(server.save_device_settings, BOARD, {"capacity_mah": 5})
    assert raises(server.save_device_settings, BOARD, {"charge_ma": 250})
    assert raises(server.save_device_settings, BOARD, {"charge_interval_min": 2})
    assert raises(server.send_cadence, BOARD, {"interval_min": 7, "pm_every_n": 4})
    assert raises(server.send_cadence, BOARD, {"interval_min": 15, "pm_every_n": 0})
    server.save_device_settings(BOARD, {"name": "  Garden  "})
    assert server.get_summary(BOARD)["settings"]["name"] == "Garden"

    # Off by default: a charge sends nothing.
    server.auto_step(BOARD)
    assert not sent, sent

    # Switched on: one faster-cadence message, and the old cadence is remembered.
    server.save_device_settings(BOARD, {"charge_interval_min": 5})
    server.auto_step(BOARD)
    server.auto_step(BOARD)
    assert sent == [("enviro/enviro-001/set", {"INTERVAL_MIN": 5, "PM_EVERY_N": 4})], sent
    assert auto()["active"] and auto()["restore"] == {"interval_min": 15, "pm_every_n": 4}

    # A battery cadence saved during the charge is kept for afterwards, not sent.
    payload, deferred = server.send_cadence(BOARD, {"interval_min": 30, "pm_every_n": 4})
    assert deferred and len(sent) == 1 and auto()["restore"]["interval_min"] == 30

    # The charge ends: the remembered cadence goes back, once.
    use(12)
    server.auto_step(BOARD)
    server.auto_step(BOARD)
    assert len(sent) == 2 and sent[-1][1] == {"INTERVAL_MIN": 30, "PM_EVERY_N": 4}, sent
    assert not auto()["active"] and auto()["why"] == "charge ended"

    # Off charge, a cadence change is sent straight away.
    payload, deferred = server.send_cadence(BOARD, {"interval_min": 60, "pm_every_n": 8})
    assert not deferred and sent[-1][1] == {"INTERVAL_MIN": 60, "PM_EVERY_N": 8}

    # Settings survive a restart.
    server.settings.update(defaults={}, devices={})
    server.load_settings()
    assert server.settings["devices"][BOARD]["name"] == "Garden"

    # The overview lists the board and the firmware.
    o = server.overview()
    assert [d["id"] for d in o["devices"]] == [BOARD] and o["devices"][0]["name"] == "Garden"
    json.dumps(o, allow_nan=False)
    fw = o["firmware"]
    assert fw["available"] and fw["version"], fw

    # Only the known firmware files are handed out.
    root = os.environ["FIRMWARE_DIR"]
    assert provision.resolve(root, "main.py") and provision.resolve(root, "lib/umqtt/simple.py")
    for bad in ("../app/server.py", "/etc/passwd", "README.md", "config.py"):
        assert provision.resolve(root, bad) is None, bad
    assert provision.bundle(root)[:2] == b"PK"
    print("ok")


if __name__ == "__main__":
    main()
