# Enviro Urban (PIM629) hardware helpers shared by main.py and diag.py.
# Pin map from the Pimoroni schematic (29/09/2022).
import time
from machine import Pin, ADC, I2C, mem32

HOLD_VSYS_EN = 2
ACT_LED = 6           # white LED via 1k
SWITCH_STATUS = 7     # STATUS button, active high
RTC_ALARM = 8         # RTC INT inverted by Q1: high while the RTC asserts INT
SENSOR_RESET = 9      # PMSA003I RESET
SENSOR_EN = 10        # PMSA003I SET (high = run, low = sleep)
BOOST_EN = 11         # 3V3->5V charge pump for the PM fan; also powers the PM I2C pull-ups
PM_SDA, PM_SCL = 14, 15
MIC_ADC = 0           # GP26
WL_CS = 25
VSYS_ADC_GPIO = 29    # shared with the CYW43 SPI clock on the Pico W

_PAD_GP29 = 0x4001C000 + 4 + 4 * VSYS_ADC_GPIO


def main_i2c():
    return I2C(0, sda=Pin(4), scl=Pin(5), freq=100000)


def read_vsys(samples=16, cal=1.0):
    """VSYS (= battery voltage, less a few mV across the Q3 latch FET).

    Only call this while the CYW43 is NOT running (before any network/WL_GPIO
    access, or after wlan.deinit()). GP29 is the wifi chip's SPI clock; the
    stock Enviro firmware crashed doing this with wifi alive. We also save and
    restore the GP29 pad register, because ADC init disables the pad's digital
    input and the wifi driver does not re-enable it."""
    saved = mem32[_PAD_GP29]
    Pin(WL_CS, Pin.OUT, value=1)      # deselect the wifi chip
    adc = ADC(VSYS_ADC_GPIO)
    time.sleep_ms(2)                  # let the divider settle (Julia7676, enviro#145)
    total = 0
    for _ in range(samples):
        total += adc.read_u16()
    mem32[_PAD_GP29] = saved
    return round(total / samples * 3.3 * 3 / 65535 * cal, 3)


def mic_vpp(sample_ms=500, feed=None):
    adc = ADC(MIC_ADC)
    lo = hi = adc.read_u16()
    t0 = time.ticks_ms()
    n = 0
    while time.ticks_diff(time.ticks_ms(), t0) < sample_ms:
        v = adc.read_u16()
        if v < lo:
            lo = v
        if v > hi:
            hi = v
        n += 1
        if feed and n & 0x3FF == 0:
            feed()
    return round((hi - lo) * 3.3 / 65535, 3)


class PMSensor:
    """PMSA003I on its own I2C bus, powered from the 5V charge pump."""

    def __init__(self):
        self.on = False

    def power_on(self):
        Pin(SENSOR_RESET, Pin.OUT, value=1)
        Pin(BOOST_EN, Pin.OUT, value=1)
        Pin(SENSOR_EN, Pin.OUT, value=1)
        self.on = True

    def power_off(self):
        # Leave nothing driving into the unpowered sensor.
        Pin(SENSOR_EN, Pin.OUT, value=0)
        Pin(BOOST_EN, Pin.OUT, value=0)
        Pin(SENSOR_RESET, Pin.IN)
        Pin(PM_SDA, Pin.IN)
        Pin(PM_SCL, Pin.IN)
        self.on = False

    def read_frame(self):
        """Returns a dict, or raises ValueError on a bad frame."""
        i2c = I2C(1, sda=Pin(PM_SDA), scl=Pin(PM_SCL), freq=100000)
        d = i2c.readfrom_mem(0x12, 0x00, 32)
        if d[0] != 0x42 or d[1] != 0x4D:
            raise ValueError("bad header %02x%02x" % (d[0], d[1]))
        if sum(d[0:30]) & 0xFFFF != (d[30] << 8) | d[31]:
            raise ValueError("bad checksum")
        w = [(d[i] << 8) | d[i + 1] for i in range(4, 30, 2)]
        # w[0..2] = PM1/2.5/10 "CF=1" (factory), w[3..5] = atmospheric.
        # The stock firmware reports CF=1; Plantower says use atmospheric
        # values for ambient air. CF=1 reads high at higher concentrations.
        return {"pm1": w[3], "pm2_5": w[4], "pm10": w[5],
                "pm1_cf1": w[0], "pm2_5_cf1": w[1], "pm10_cf1": w[2],
                "n0_3": w[6] * 10, "n0_5": w[7] * 10, "n1": w[8] * 10,
                "n2_5": w[9] * 10, "n5": w[10] * 10, "n10": w[11] * 10,
                "pm_err": d[29]}
