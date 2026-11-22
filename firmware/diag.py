# Enviro Urban hardware check. Run over USB with the board in maintenance mode
# (hold STATUS ~3 s at wake) or before main.py is installed:
#     mpremote run diag.py
# Prints PASS/FAIL/WARN per check. Does not touch config.py or the log files.
import time
from machine import Pin

Pin(2, Pin.OUT, value=1)
import urban_hw as hw
from pcf85063a_lite import PCF85063A, TIMER_MODE_SECONDS_TEST

results = []


def report(ok, name, detail=""):
    tag = {True: "PASS", False: "FAIL", None: "WARN"}[ok]
    results.append((tag, name))
    print("[%s] %-28s %s" % (tag, name, detail))


# 1. Battery / VSYS (before anything wakes the wifi chip)
v = hw.read_vsys()
report(1.8 <= v <= 5.6, "VSYS voltage", "%.3f V (USB alone reads ~4.6-5.0 V)" % v)

# 2. Main I2C bus
i2c = hw.main_i2c()
found = i2c.scan()
report(0x51 in found, "RTC on I2C 0x51", str([hex(a) for a in found]))
report(0x77 in found, "BME280 on I2C 0x77")
extra = [hex(a) for a in found if a not in (0x51, 0x77)]
if extra:
    report(None, "unexpected I2C devices", str(extra) + " (QW/ST add-ons?)")

# 3. RTC
rtc = PCF85063A(i2c)
d = rtc.dump()
print("     RTC registers:", d)
os_flag = rtc.oscillator_stopped()
report(not os_flag, "RTC oscillator-stop flag clear",
       "set: RTC has been reset/power-lost since last clear" if os_flag else "")
rtc.clear_oscillator_stopped()
report((d["ctrl1"] & 0x20) == 0, "RTC not STOPped", "ctrl1=0x%02x" % d["ctrl1"])
cof = d["ctrl2"] & 0x07
report(cof == 0x07, "RTC CLKOUT off",
       "" if cof == 0x07 else "COF=%d: red LED is being driven, ~1 mA drain while asleep" % cof)

s0 = rtc._r1(0x04) & 0x7F
time.sleep(2.2)
s1 = rtc._r1(0x04) & 0x7F
report(s0 != s1, "RTC clock ticking", "seconds 0x%02x -> 0x%02x" % (s0, s1))

# 4. Wake path: RTC INT -> Q1 -> RTC_ALARM (GP8). This is the signal that
#    turns the power latch on. Uses a 2 s countdown, then restores the config.
alarm = Pin(hw.RTC_ALARM, Pin.IN, Pin.PULL_DOWN)
saved_mode, saved_val = d["timer_mode"], d["timer_value"]
rtc.clear_flags()
time.sleep_ms(20)
idle = alarm.value()
rtc._w(0x11, 0x10)                     # TE=0
rtc._w(0x10, 2)
rtc._w(0x11, TIMER_MODE_SECONDS_TEST)  # 1 Hz source, INT enabled
t0 = time.ticks_ms()
while not rtc.timer_flag() and time.ticks_diff(time.ticks_ms(), t0) < 4000:
    time.sleep_ms(10)
fired_ms = time.ticks_diff(time.ticks_ms(), t0)
asserted = alarm.value()
rtc._w(0x11, 0x10)                     # stop test timer before clearing the flag
rtc.clear_flags()
time.sleep_ms(20)
released = alarm.value()
rtc._w(0x10, saved_val)
rtc._w(0x11, saved_mode)
rtc.clear_flags()
report(idle == 0, "RTC_ALARM low when idle")
report(fired_ms < 4000, "RTC timer fires", "%d ms" % fired_ms)
report(asserted == 1, "RTC_ALARM high on timer", "GP8=%d" % asserted)
report(released == 0, "RTC_ALARM releases on clear", "GP8=%d" % released)

# 5. BME280
try:
    from bme280_lite import BME280
    t, h, p = BME280(i2c).read()
    ok = -40 < t < 85 and 0 <= h <= 100 and 300 < p < 1100
    report(ok, "BME280 reading", "%.2f C  %.1f %%RH  %.2f hPa" % (t, h, p))
except Exception as e:
    report(False, "BME280 reading", repr(e))

# 6. Microphone
vpp = hw.mic_vpp(500)
report(0.0 < vpp < 3.0, "Microphone signal", "Vpp=%.3f V (0 = dead/stuck)" % vpp)

# 7. Buttons
report(Pin(hw.SWITCH_STATUS, Pin.IN, Pin.PULL_DOWN).value() == 0, "STATUS button released")

# 8. Particulate sensor
pm = hw.PMSensor()
try:
    pm.power_on()
    time.sleep(2)
    vload = hw.read_vsys()
    report(vload > 3.0, "VSYS with PM fan on", "%.3f V (sag %.0f mV)" % (vload, (v - vload) * 1000))
    print("     waiting 10 s for the PM sensor...")
    time.sleep(8)
    good = bad = 0
    last = None
    for _ in range(5):
        try:
            last = pm.read_frame()
            good += 1
        except Exception as e:
            bad += 1
            print("     frame error:", repr(e))
        time.sleep(1)
    report(good >= 3, "PMSA003I frames", "%d good, %d bad; last=%s" % (good, bad, last))
finally:
    pm.power_off()

# 9. Wifi (last: after this GP29 belongs to the wifi chip). Needs config.py.
try:
    import config
    import network
    import rp2
    rp2.country(getattr(config, "WIFI_COUNTRY", "GB"))
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    usb = Pin("WL_GPIO2", Pin.IN).value()
    report(None if usb else True, "VBUS sense", "USB present" if usb else "no USB")
    t0 = time.ticks_ms()
    wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
    while wlan.status() not in (3, -1, -2, -3) and time.ticks_diff(time.ticks_ms(), t0) < 20000:
        time.sleep_ms(100)
    st = wlan.status()
    ms = time.ticks_diff(time.ticks_ms(), t0)
    report(st == 3, "WiFi connect", "status=%d in %d ms, rssi=%s, ip=%s" % (
        st, ms, wlan.status("rssi") if st == 3 else "-", wlan.ifconfig()[0]))
    if st == 3:
        try:
            from umqtt.simple import MQTTClient
            c = MQTTClient("enviro-diag", config.MQTT_BROKER, port=getattr(config, "MQTT_PORT", 1883),
                           user=getattr(config, "MQTT_USER", None),
                           password=getattr(config, "MQTT_PASSWORD", None))
            c.connect(timeout=5)
            c.publish(getattr(config, "MQTT_TOPIC", "enviro") + "/diag", b"diag ok", qos=1)
            c.disconnect()
            report(True, "MQTT publish", config.MQTT_BROKER)
        except Exception as e:
            report(False, "MQTT publish", repr(e))
    wlan.disconnect()
    wlan.active(False)
    wlan.deinit()
except ImportError as e:
    report(None, "WiFi/MQTT skipped", repr(e))

print()
fails = [n for t, n in results if t == "FAIL"]
print("SUMMARY:", "all checks passed" if not fails else "FAILED: " + ", ".join(fails))
print("Not testable over USB: whether the latch actually cuts power. See README 'Battery test'.")
