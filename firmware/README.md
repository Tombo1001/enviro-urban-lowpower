# Enviro Urban low-power firmware

This is a replacement for Pimoroni's `enviro` firmware, written for one job on battery power:

1. The RTC wakes the board.
2. It reads the battery, the BME280, the microphone and (every Nth wake) the PM sensor.
3. It sends one MQTT message.
4. It cuts its own power.

It runs on **stock MicroPython** (`RPI_PICO_W`), not Pimoroni's custom build.

## What was actually wrong

The hardware is fine. The Enviro Urban schematic is a good low-power design:

- The RTC (PCF85063A) and a 1 µA-Iq LDO run on an always-on rail.
- A P-FET latch (Q3/Q5) disconnects the **whole Pico W** from the battery.
- The latch turns on when the RTC INT line asserts, when STATUS is pressed, or while GP2 (`HOLD_VSYS_EN`) is high.

Asleep, the board should draw a few µA, which is years of standby on AAs.

Batteries died because the **RP2040 hung while holding GP2 high**. Two separate cases were reported:

- The CPU locked up after WiFi or MQTT errors.
- It also locked up sometimes during boot.

A hung board stays fully powered at tens of mA, so a set of AAs lasted days. The evidence for this comes from the issue threads:

- sjefferson99 put a meter on a hung board (enviro#87). GP2 was at 3.3 V and VSYS/3V3 were up. Shorting 3V3_EN shut the board down, and "the RTC woke it up shortly after". So the latch and the RTC both worked, and the CPU was stuck.
- Pimoroni never fixed the hangs. The repo's main branch has had no commits since April 2024.

**The "thinks it's on USB" theory (enviro#210) is a misreading of the logs.**

- The stock firmware prints `on usb power (so can't shutdown)` after **every** shutdown. It never checks anything: it releases the latch, then logs that line unconditionally (`enviro/__init__.py`, `sleep()`). On battery the line still gets written because the supply takes a few milliseconds to collapse.
- The USB-detection flag (`vbus_present`) only affects the wake-reason label, WiFi power-save and the Indoor board's temperature offset. It never decides whether the board sleeps.
- The `2000-01-01 ... wake reason: usb_powered` entries that follow a hang come from the user. They pulled the battery (which resets the RTC) and plugged in USB to read the log.
- sjefferson99 measured about 0.75 V on VBUS on battery, well below what would read as "USB present".

This firmware reads the USB-detection pin only to report it.

The stock firmware also has three smaller problems:

- **No watchdog.** Nothing recovers a hung board.
- **The wake depends on software re-arming an alarm** every cycle, against a wall clock that sometimes reset to 2000-01-01.
- **Battery reading was disabled in v0.0.9.** Reading VSYS uses GP29, which is also the wifi chip's SPI clock, and reading it with wifi running crashed the board.

## How this firmware avoids it

| Problem | What this firmware does |
|---|---|
| CPU hangs and keeps the latch held | The hardware watchdog (8 s) resets the RP2040. A reset releases GP2. The RTC flag is cleared early in every wake, so nothing else holds the latch, and **a reset means the board powers off**. |
| Wake depends on re-arming an alarm | The RTC **countdown timer auto-reloads in hardware**. The board gets woken again even if the previous run crashed. No wall clock or NTP is needed; MQTT/Home Assistant timestamp each reading on arrival. |
| WiFi stack left in a bad state | WiFi starts at most once per wake, every step has a timeout, and the run ends with `disconnect` → `active(False)` → `deinit`. Since the board then loses power, every wake starts from clean silicon. |
| Battery reading crashed wifi | VSYS is read **before the wifi chip is ever powered** in that wake, and again under PM-fan load, still before wifi. The GP29 pad register is saved and restored. |
| A crash leaves no trace | A breadcrumb in the RTC's battery-backed RAM byte records the stage the board reached. If a wake dies, the next message carries `"last_fault": "wifi"` (or whichever stage). |
| Red LED drains ~1 mA forever after an RTC reset | CLKOUT is forced off on every wake. If the RTC lost power, `rtc_power_lost: true` is reported. |
| PM readings unstable (enviro#172) | 30 s warm-up (configurable). Frames are checksum-validated and three are averaged. Atmospheric values are reported, not the CF=1 "factory" values the stock firmware used. |
| BME280 reads hot | Forced mode, and the board is unpowered between readings, so there is little self-heating. `TEMP_OFFSET` is available if needed. |

**Remaining risk:** if the chip locks up *before* `boot.py` runs, the RTC INT line is still holding power and the watchdog has not started yet. The board would stay on until the next battery check. Lock-ups during boot were among the reports on the stock firmware. This firmware runs on stock MicroPython, so it has none of the early power-hold code from Pimoroni's custom build. The breadcrumb would show such a wake as `last_fault: boot`.

## Files

```
boot.py              grabs the power latch first thing
main.py              the wake cycle
config_example.py    copy to config.py and edit
diag.py              hardware self-test (run over USB)
lib/urban_hw.py      pins, battery ADC, microphone, PM sensor
lib/pcf85063a_lite.py
lib/bme280_lite.py
lib/umqtt/simple.py  MQTT client from micropython-lib (MIT), included so no download is needed
```

## Install

The dashboard's **Provision** tab does all of this from the browser, apart from flashing MicroPython itself. See the top-level README. The steps below are the same install by hand.

You need Python on the PC for `mpremote`:

```powershell
pip install mpremote
```

1. **Back up the old setup** if you want it, then wipe it. A different UF2 can keep the old filesystem, so start clean.
   - Hold **BOOTSEL** on the Pico W while plugging USB in. An `RPI-RP2` drive appears.
   - Copy `flash_nuke.uf2` onto it. Get it from https://datasheets.raspberrypi.com/soft/flash_nuke.uf2.
2. **Flash MicroPython.** BOOTSEL again, then copy the latest stable `RPI_PICO_W` UF2 from https://micropython.org/download/RPI_PICO_W/.
3. **Check for MQTT support:**
   ```powershell
   mpremote exec "import umqtt.simple; print('ok')"
   ```
   MicroPython v1.29.0 does not include it. Either copy the bundled one (`mpremote mkdir :lib/umqtt` then `mpremote cp lib/umqtt/simple.py :lib/umqtt/`), or run `mpremote mip install umqtt.simple`, which downloads it on the PC and copies it over.
4. **Copy the files**, with `config.py` edited first:
   ```powershell
   cd enviro-urban-lowpower/firmware
   copy config_example.py config.py   # then edit it
   mpremote mkdir :lib
   mpremote cp lib/urban_hw.py lib/pcf85063a_lite.py lib/bme280_lite.py :lib/
   mpremote cp config.py diag.py boot.py :
   ```
5. **Run the hardware check** (next section). Copy `main.py` across only after it passes:
   ```powershell
   mpremote cp main.py :
   ```

### Maintenance mode

Once `main.py` is installed, the watchdog would reset the board in the middle of an `mpremote` session. To stop that:

- **Hold STATUS for 3 s** while plugging in USB, or while pressing RESET.
- The white LED stays on, there is no watchdog and no sleep, and the REPL is free.
- Press RESET to leave.

## Hardware check: is the board OK?

```powershell
mpremote run diag.py
```

It checks:

- VSYS, both idle and with the PM fan running (shows the sag).
- Both I2C devices are present.
- The RTC: oscillator-stop flag, STOP bit, whether CLKOUT is driving the red LED, and that the clock is actually ticking.
- **The wake path electrically:** RTC timer → INT → Q1 → GP8. This is the signal that switches the latch on.
- BME280 values are plausible.
- The microphone produces a signal.
- The PMSA003I returns checksum-valid frames.
- VBUS sense.
- WiFi connect time and RSSI.
- A test MQTT publish to `<topic>/diag`.

The one thing it cannot test over USB is whether the latch actually **cuts** power, because USB powers the Pico directly. That needs the battery test.

## Battery test

1. In `config.py`, set `INTERVAL_MIN = 1` and `PM_EVERY_N = 1` for a stress soak. The old firmware usually failed within hours at a 1-minute interval.
2. Put a multimeter in series with the battery, on the **mA range**.
   - During a wake you should see tens of mA, then about 150–200 mA while the PM fan runs.
   - It should then drop to about **0.00 mA**.
   - Switch to µA only while the board is asleep, and bridge the meter before the next wake. The µA shunt's voltage drop will brown the board out when it starts.
   - Expect about **1–5 µA asleep**. More than ~50 µA means something is back-powering or the red LED is on.
3. Watch MQTT:
   ```
   mosquitto_sub -h <broker> -t 'enviro/#' -v
   ```
   - One `state` message per minute.
   - `awake_ms` should be around 8–15 s, or about 40 s on PM wakes.
   - No `last_fault`.
4. Run it for 24 h, which is about 1,440 wakes. Every `last_fault` is a hang the watchdog recovered from. It is no longer a flat battery, but it is worth reporting: it shows *where* the board hangs.
5. Also check `log.txt` (`mpremote cp :log.txt .`). A line saying `still powered after latch release` while on battery would mean the latch really is not releasing. That would be a genuine hardware fault, and the line includes the pin states.

Afterwards, set the real interval.

## Power budget (estimates, not measured)

| | Current | Time | Charge per wake |
|---|---|---|---|
| Asleep | ~2–5 µA | always | ~0.1 mAh/day |
| Base wake (sensors, wifi, MQTT) | ~50–80 mA avg | ~8–10 s | ~0.2 mAh |
| PM fan (5 V charge pump from 3V3) | ~150–200 mA | +30 s | ~1.3 mAh |

The PM fan is most of the budget. With about 2,000 mAh usable from 3×AA (less in the cold):

| Setting | Per day | Estimated life |
|---|---|---|
| 15 min, PM every wake | ~150 mAh | ~2 weeks |
| 15 min, PM hourly (`PM_EVERY_N = 4`, the default) | ~50 mAh | ~6 weeks |
| 15 min, PM every 2 h (`PM_EVERY_N = 8`) | ~35 mAh | ~2 months |
| 30 min, no PM (`PM_MIN_V = 99`) | ~10 mAh | ~6 months |

Things that reduce the per-wake cost:

- A static IP (`WIFI_STATIC`) and a broker given as an IP address.
- Shorter `PM_WARMUP_S`, at the cost of PM accuracy.
- A strong wifi signal. Slow associations cost the most after the fan.

## MQTT output

`<topic>/state` (retained):

```json
{"fw":"urban-lp 0.1","battery_v":4.512,"battery_v_load":4.38,"wake":"timer",
 "temperature":12.3,"humidity":71.2,"pressure":1011.4,"noise":0.042,
 "rssi":-61,"usb":0,"awake_ms":9120}
```

Optional fields:

- `last_fault`: the stage where the previous wake died.
- `last_error`: the last exception text.
- `rtc_power_lost`: the battery dropped out since the last wake.
- `rtc_reconfigured`: the RTC settings had to be rewritten.

`<topic>/pm` (retained, on PM wakes only): `pm1`, `pm2_5`, `pm10` (atmospheric), the `_cf1` variants, and particle counts per litre.

With `HA_DISCOVERY = True`, sensors appear in Home Assistant automatically. Discovery is sent on button and power-on wakes, not on timer wakes, to save airtime.
