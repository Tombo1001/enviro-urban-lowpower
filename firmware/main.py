# Enviro Urban low-power firmware for stock MicroPython (RPI_PICO_W).
#
# One wake = read battery -> sensors -> (PM every Nth wake) -> wifi -> MQTT ->
# cut our own power. The RTC countdown timer re-wakes us. See README.md.
#
# Hang protection, in layers:
#  1. Every network step has a timeout.
#  2. A soft deadline (MAX_AWAKE_S) aborts the wake.
#  3. The RP2040 hardware watchdog resets the chip if Python stops running.
#     A reset floats GP2 (HOLD_VSYS_EN), and because the RTC flag was already
#     cleared, the latch drops and the board powers off. The RTC timer
#     auto-reloads, so the next wake still happens.
#  4. A breadcrumb (stage) in the RTC's RAM byte tells the next wake where the
#     previous one died, and gets published as "last_fault".
import time
T0 = time.ticks_ms()

import machine
import json
import os
from machine import Pin

hold = Pin(2, Pin.OUT, value=1)   # boot.py already did this; harmless to repeat

FW = "urban-lp 0.1"

import config
import urban_hw as hw
from pcf85063a_lite import PCF85063A

ST_BOOT, ST_SENSORS, ST_PM, ST_WIFI, ST_MQTT, ST_DONE = 1, 2, 3, 4, 5, 15
STAGE_NAMES = {ST_BOOT: "boot", ST_SENSORS: "sensors", ST_PM: "pm",
               ST_WIFI: "wifi", ST_MQTT: "mqtt"}


def cfg(name, default=None):
    return getattr(config, name, default)


# Settings that can be changed remotely: publish a retained JSON object to
# <MQTT_TOPIC>/set, e.g. {"INTERVAL_MIN": 15}. The board reads it during its
# normal MQTT session, stores it in overrides.json and applies it at once.
REMOTE_KEYS = {"INTERVAL_MIN": int, "PM_EVERY_N": int, "PM_WARMUP_S": float,
               "PM_MIN_V": float, "LOW_BATTERY_V": float, "TEMP_OFFSET": float,
               "VSYS_CAL": float, "LED": bool, "HA_DISCOVERY": bool,
               "MAX_AWAKE_S": int, "WIFI_TIMEOUT_S": int}


def load_overrides():
    try:
        with open("overrides.json") as f:
            o = json.load(f)
    except (OSError, ValueError):
        return {}
    for k, v in o.items():
        if k in REMOTE_KEYS:
            setattr(config, k, v)
    return o


OVERRIDES = load_overrides()
NAME = cfg("NAME", "enviro-urban")
INTERVAL = cfg("INTERVAL_MIN", 15)
PM_EVERY_N = max(1, min(15, cfg("PM_EVERY_N", 4)))


def apply_remote_config(raw):
    """Validate a /set payload; persist and apply what changed. Returns a list
    of changed keys, or raises ValueError for a bad payload."""
    global INTERVAL, PM_EVERY_N, OVERRIDES
    d = json.loads(raw)
    if not isinstance(d, dict):
        raise ValueError("set payload must be a JSON object")
    new = dict(OVERRIDES)
    for k, v in d.items():
        if k not in REMOTE_KEYS:
            raise ValueError("unknown key " + k)
        v = REMOTE_KEYS[k](v)
        if k == "INTERVAL_MIN" and not 1 <= v <= 255:
            raise ValueError("INTERVAL_MIN must be 1..255")
        if k == "PM_EVERY_N" and not 1 <= v <= 15:
            raise ValueError("PM_EVERY_N must be 1..15")
        new[k] = v
    changed = [k for k in new if OVERRIDES.get(k) != new[k] or cfg(k) != new[k]]
    if not changed:
        return []
    with open("overrides.json", "w") as f:
        json.dump(new, f)
    OVERRIDES = new
    for k, v in new.items():
        setattr(config, k, v)
    INTERVAL = cfg("INTERVAL_MIN", 15)
    PM_EVERY_N = max(1, min(15, cfg("PM_EVERY_N", 4)))
    if rtc:
        rtc.configure(INTERVAL)   # takes effect from this sleep
    return changed

wdt = None
rtc = None
counter = 0
shutting_down = False


class Deadline(Exception):
    pass


def elapsed_ms():
    return time.ticks_diff(time.ticks_ms(), T0)


def feed():
    if wdt:
        wdt.feed()
    if not shutting_down and elapsed_ms() > cfg("MAX_AWAKE_S", 90) * 1000:
        raise Deadline("awake for %d ms" % elapsed_ms())


def nap(seconds):
    end = time.ticks_add(time.ticks_ms(), int(seconds * 1000))
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        feed()
        time.sleep_ms(min(200, max(1, time.ticks_diff(end, time.ticks_ms()))))


def log(msg):
    print(msg)
    if not cfg("LOG", True):
        return
    try:
        if os.stat("log.txt")[6] > 16384:
            os.rename("log.txt", "log.old")
    except OSError:
        pass
    try:
        with open("log.txt", "a") as f:
            f.write("%7d %s\n" % (elapsed_ms(), msg))
    except OSError:
        pass


current_stage = 0


def set_stage(stage):
    global current_stage
    current_stage = stage
    try:
        rtc.set_ram((stage << 4) | (counter & 0x0F))
    except Exception:
        pass


# --- networking ---------------------------------------------------------------
def wifi_up():
    import network
    import rp2
    rp2.country(cfg("WIFI_COUNTRY", "GB"))
    try:
        network.hostname(NAME[:32])
    except Exception:
        pass
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.config(pm=getattr(network.WLAN, "PM_NONE", 0xA11140))
    if cfg("WIFI_STATIC"):
        wlan.ifconfig(cfg("WIFI_STATIC"))
    retries = 1
    wlan.connect(cfg("WIFI_SSID"), cfg("WIFI_PASSWORD"))
    t0 = time.ticks_ms()
    while True:
        st = wlan.status()
        if st == 3:
            return wlan
        if st < 0:
            # -1/-2 are sometimes transient on the CYW43 (enviro#241): retry once
            if retries and st != -3:
                retries -= 1
                wlan.disconnect()
                nap(0.5)
                wlan.connect(cfg("WIFI_SSID"), cfg("WIFI_PASSWORD"))
            else:
                raise OSError("wifi status %d" % st)
        if time.ticks_diff(time.ticks_ms(), t0) > cfg("WIFI_TIMEOUT_S", 15) * 1000:
            raise OSError("wifi timeout, status %d" % st)
        nap(0.1)


def wifi_down(wlan):
    for step in (wlan.disconnect, lambda: wlan.active(False), wlan.deinit):
        try:
            step()
        except Exception:
            pass


SENSORS = (
    # key, name, unit, device_class, topic suffix
    ("temperature", "Temperature", "°C", "temperature", "state"),
    ("humidity", "Humidity", "%", "humidity", "state"),
    ("pressure", "Pressure", "hPa", "atmospheric_pressure", "state"),
    ("noise", "Noise (Vpp)", "V", None, "state"),
    ("battery_v", "Battery", "V", "voltage", "state"),
    ("rssi", "WiFi signal", "dBm", "signal_strength", "state"),
    ("pm1", "PM1", "µg/m³", "pm1", "pm"),
    ("pm2_5", "PM2.5", "µg/m³", "pm25", "pm"),
    ("pm10", "PM10", "µg/m³", "pm10", "pm"),
)


def mqtt_publish(state, pm):
    # Payloads must be bytes: umqtt.simple sizes packets with len(), which for a
    # str counts characters, so non-ASCII text (°C, µg/m³) corrupts the packet.
    from umqtt.simple import MQTTClient
    uid = hw_uid()
    base = cfg("MQTT_TOPIC", "enviro/" + NAME)
    qos = cfg("MQTT_QOS", 1)
    c = MQTTClient(uid, cfg("MQTT_BROKER"), port=cfg("MQTT_PORT", 1883),
                   user=cfg("MQTT_USER"), password=cfg("MQTT_PASSWORD"),
                   keepalive=0)
    c.connect(timeout=5)
    feed()
    try:
        # Remote config: a retained message on <topic>/set arrives straight
        # after SUBACK. Costs one round trip, plus up to 0.4 s if none is set.
        set_msgs = []
        c.set_callback(lambda t, m: set_msgs.append(m))
        c.subscribe(base + "/set", qos=0)
        t0 = time.ticks_ms()
        while not set_msgs and time.ticks_diff(time.ticks_ms(), t0) < 400:
            c.check_msg()
            time.sleep_ms(20)
        c.sock.settimeout(5)
        changed = []
        if set_msgs:
            try:
                changed = apply_remote_config(set_msgs[-1])
            except Exception as e:
                state["config_error"] = repr(e)[:100]
        if changed:
            state["config_changed"] = changed
        state["interval_min"] = INTERVAL
        state["pm_every_n"] = PM_EVERY_N
        feed()

        # Discovery carries expire_after, which depends on the interval, so
        # resend it whenever the cadence changes.
        if cfg("HA_DISCOVERY", True) and (state.get("wake") != "timer" or changed):
            dev = {"identifiers": [uid], "name": NAME, "manufacturer": "Pimoroni",
                   "model": "Enviro Urban (PIM629)", "sw_version": FW}
            for key, name, unit, dc, suffix in SENSORS:
                expire = INTERVAL * 60 * 3 * (PM_EVERY_N if suffix == "pm" else 1)
                d = {"name": name, "unique_id": uid + "_" + key,
                     "state_topic": base + "/" + suffix,
                     "value_template": "{{ value_json.%s }}" % key,
                     "unit_of_measurement": unit, "state_class": "measurement",
                     "expire_after": expire, "device": dev}
                if dc:
                    d["device_class"] = dc
                c.publish("homeassistant/sensor/%s_%s/config" % (uid, key),
                          json.dumps(d).encode(), retain=True, qos=qos)
                feed()
        if pm:
            c.publish(base + "/pm", json.dumps(pm).encode(), retain=True, qos=qos)
            feed()
        state["awake_ms"] = elapsed_ms()
        c.publish(base + "/state", json.dumps(state).encode(), retain=True, qos=qos)
    finally:
        try:
            c.disconnect()
        except Exception:
            pass


def hw_uid():
    import ubinascii
    return "enviro_" + ubinascii.hexlify(machine.unique_id()).decode()


# --- the wake ----------------------------------------------------------------
def run():
    global wdt, rtc, counter
    led = Pin(hw.ACT_LED, Pin.OUT, value=1 if cfg("LED", False) else 0)
    button = Pin(hw.SWITCH_STATUS, Pin.IN, Pin.PULL_DOWN)
    state = {"fw": FW}
    # PWRON (1) = the latch really cut power last time. WDT/soft reset means we
    # restarted without losing power (USB, or a latch that did not release).
    rc = machine.reset_cause()
    state["reset_cause"] = {machine.PWRON_RESET: "power_on", machine.WDT_RESET: "watchdog",
                            getattr(machine, "SOFT_RESET", -1): "soft"}.get(rc, str(rc))

    # 1. Battery voltage first, while the wifi chip is guaranteed to be off.
    vbat = hw.read_vsys(cal=cfg("VSYS_CAL", 1.0))
    state["battery_v"] = vbat

    # 2. RTC: work out why we woke and what happened last time, then make sure
    #    it is configured, and release its INT line (we hold the latch now).
    rtc = PCF85063A(hw.main_i2c())
    ram = rtc.ram()
    prev_stage, counter = ram >> 4, ram & 0x0F
    timer_wake = rtc.timer_flag()
    state["wake"] = "timer" if timer_wake else ("button" if button.value() else "power")
    if prev_stage not in (0, ST_DONE):
        state["last_fault"] = STAGE_NAMES.get(prev_stage, str(prev_stage))
    if rtc.oscillator_stopped():
        # The RTC was reset: the battery dropped out since the last wake.
        state["rtc_power_lost"] = True
        rtc.clear_oscillator_stopped()
    if rtc.configure(INTERVAL):
        state["rtc_reconfigured"] = True
    rtc.clear_flags()
    set_stage(ST_BOOT)

    # 3. Maintenance mode: hold STATUS for 3 s at wake. Stays powered, no
    #    watchdog, REPL free for mpremote/Thonny. Reset or pull power to leave.
    if state["wake"] == "button":
        t = time.ticks_ms()
        while button.value() and time.ticks_diff(time.ticks_ms(), t) < 3000:
            time.sleep_ms(20)
        if button.value():
            led.value(1)
            set_stage(ST_DONE)
            print("maintenance mode: staying awake, watchdog off")
            return "maintenance"

    if cfg("WATCHDOG", True):
        wdt = machine.WDT(timeout=8000)

    try:
        with open("latch_held.txt") as f:
            state["latch_held"] = f.read()[:120]
        os.remove("latch_held.txt")
    except OSError:
        pass

    try:
        with open("last_error.txt") as f:
            state["last_error"] = f.read()[:200]
    except OSError:
        pass

    if 1.0 < vbat < cfg("LOW_BATTERY_V", 2.9):
        log("battery %.2fV below LOW_BATTERY_V, skipping this wake" % vbat)
        return "lowbatt"

    # 4. Environmental sensors.
    set_stage(ST_SENSORS)
    from bme280_lite import BME280
    t, h, p = BME280(rtc.i2c).read()
    state["temperature"] = round(t + cfg("TEMP_OFFSET", 0.0), 2)
    state["humidity"] = round(h, 1)
    state["pressure"] = round(p, 2)
    state["noise"] = hw.mic_vpp(500, feed)

    # 5. Particulates, every Nth timer wake (and on any manual wake).
    pm = None
    do_pm = counter % PM_EVERY_N == 0 or state["wake"] != "timer"
    if do_pm and vbat >= cfg("PM_MIN_V", 3.3):
        set_stage(ST_PM)
        sensor = hw.PMSensor()
        try:
            sensor.power_on()
            nap(2)
            # Battery under fan load - still safe, wifi has not started yet.
            state["battery_v_load"] = hw.read_vsys(cal=cfg("VSYS_CAL", 1.0))
            nap(max(0, cfg("PM_WARMUP_S", 30) - 2))
            frames = []
            for _ in range(6):
                try:
                    frames.append(sensor.read_frame())
                except (OSError, ValueError) as e:
                    log("pm frame: %r" % e)
                if len(frames) >= 3:
                    break
                nap(1)
            if frames:
                pm = {k: round(sum(f[k] for f in frames) / len(frames), 1)
                      for k in frames[0]}
                pm["frames"] = len(frames)
        finally:
            sensor.power_off()

    # 6. Network.
    set_stage(ST_WIFI)
    wlan = None
    try:
        wlan = wifi_up()
        state["rssi"] = wlan.status("rssi")
        state["usb"] = Pin("WL_GPIO2", Pin.IN).value()
        set_stage(ST_MQTT)
        mqtt_publish(state, pm)
        try:
            os.remove("last_error.txt")
        except OSError:
            pass
    finally:
        if wlan:
            wifi_down(wlan)
    log("ok " + json.dumps(state))
    return "ok"


def shutdown():
    global shutting_down
    shutting_down = True
    try:
        if rtc:
            rtc.set_ram((ST_DONE << 4) | ((counter + 1) % PM_EVERY_N))
            rtc.clear_flags()
    except Exception as e:
        log("rtc error at shutdown: %r" % e)
    Pin(hw.ACT_LED, Pin.OUT, value=0)
    hold.value(0)
    # On battery the rail collapses within milliseconds; nothing below runs.
    # If we are still here, something else is powering us (USB, button held,
    # or the RTC INT line is asserted).
    reported = False
    t0 = time.ticks_ms()
    while True:
        if wdt:
            wdt.feed()
        time.sleep_ms(250)
        if not reported and time.ticks_diff(time.ticks_ms(), t0) > 3000:
            reported = True
            try:
                usb = Pin("WL_GPIO2", Pin.IN).value()
            except Exception:
                usb = -1
            msg = "usb=%s rtc_alarm=%d button=%d" % (
                usb, Pin(hw.RTC_ALARM, Pin.IN).value(), Pin(hw.SWITCH_STATUS, Pin.IN).value())
            log("still powered after latch release: " + msg)
            if usb == 0:
                try:
                    with open("latch_held.txt", "w") as f:
                        f.write(msg)
                except OSError:
                    pass
        try:
            if rtc and rtc.timer_flag():
                machine.reset()
        except OSError:
            pass
        if Pin(hw.SWITCH_STATUS, Pin.IN).value():
            machine.reset()


result = None
try:
    result = run()
except Exception as e:
    msg = "%s: %r" % (STAGE_NAMES.get(current_stage, "init"), e)
    log("error " + msg)
    try:
        with open("last_error.txt", "w") as f:
            f.write(msg)
    except OSError:
        pass
finally:
    if result != "maintenance":
        shutdown()
