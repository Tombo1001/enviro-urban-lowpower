# Minimal PCF85063A driver for the Enviro power-latch use case.
# We only use the countdown timer (as a periodic, self-reloading wake source),
# the flags, CLKOUT control and the 1-byte RAM register. Wall-clock time is not
# needed: MQTT/Home Assistant timestamp readings on receipt.

ADDR = 0x51

REG_CTRL1 = 0x00
REG_CTRL2 = 0x01
REG_RAM = 0x03
REG_SECONDS = 0x04
REG_ALARM_SEC = 0x0B      # 0x0B..0x0F alarm registers, bit7 = AEN (1 = disabled)
REG_TIMER_VALUE = 0x10
REG_TIMER_MODE = 0x11

CTRL2_AIE = 0x80
CTRL2_AF = 0x40
CTRL2_MI = 0x20
CTRL2_HMI = 0x10
CTRL2_TF = 0x08
COF_MASK = 0x07
COF_OFF = 0x07            # CLKOUT disabled. POR default is 32.768 kHz, which
                          # lights the red ALARM LED and costs ~1 mA forever.
COF_1HZ = 0x06

# Timer_mode: TCF[4:3]=11 (1/60 Hz source), TE=1, TIE=1, TI_TP=0 (INT follows TF)
TIMER_MODE_MINUTES = 0x1E
TIMER_MODE_SECONDS_TEST = 0x16   # TCF=10 (1 Hz), TE=1, TIE=1, TI_TP=0


class PCF85063A:
    def __init__(self, i2c):
        self.i2c = i2c

    def _r(self, reg, n=1):
        return self.i2c.readfrom_mem(ADDR, reg, n)

    def _r1(self, reg):
        return self._r(reg)[0]

    def _w(self, reg, val):
        self.i2c.writeto_mem(ADDR, reg, bytes([val & 0xFF]))

    # --- status -------------------------------------------------------------
    def ctrl2(self):
        return self._r1(REG_CTRL2)

    def timer_flag(self):
        return bool(self.ctrl2() & CTRL2_TF)

    def alarm_flag(self):
        return bool(self.ctrl2() & CTRL2_AF)

    def oscillator_stopped(self):
        """OS flag: set at power-on-reset of the RTC, i.e. the always-on rail
        (and so the battery) dropped out since we last cleared it."""
        return bool(self._r1(REG_SECONDS) & 0x80)

    def clear_oscillator_stopped(self):
        self._w(REG_SECONDS, self._r1(REG_SECONDS) & 0x7F)

    def clear_flags(self):
        # AF and TF are cleared by writing 0; writing 1 leaves them unchanged.
        self._w(REG_CTRL2, self.ctrl2() & ~(CTRL2_AF | CTRL2_TF))

    # --- 1 byte of battery-backed RAM -----------------------------------------
    def ram(self):
        return self._r1(REG_RAM)

    def set_ram(self, v):
        self._w(REG_RAM, v)

    # --- configuration --------------------------------------------------------
    def configure(self, interval_min, clkout=COF_OFF):
        """Idempotently put the RTC into the state we rely on.
        Returns True if anything had to be changed."""
        if not 1 <= interval_min <= 255:
            raise ValueError("interval_min must be 1..255")
        changed = False

        # Control_1: running (STOP=0), 24h, 7 pF load cap (matches the
        # SC32S-7PF20PPM crystal on the Enviro), no correction interrupt.
        if self._r1(REG_CTRL1) != 0x00:
            self._w(REG_CTRL1, 0x00)
            changed = True

        # Disable every alarm field so only the timer can assert INT.
        for reg in range(REG_ALARM_SEC, REG_ALARM_SEC + 5):
            if not self._r1(reg) & 0x80:
                self._w(reg, 0x80)
                changed = True

        # Control_2: alarm/minute/half-minute interrupts off, CLKOUT as asked.
        c2 = self.ctrl2()
        want = (c2 & (CTRL2_AF | CTRL2_TF)) | clkout
        if c2 != want:
            self._w(REG_CTRL2, want)
            changed = True

        # Countdown timer, auto-reloading every interval_min minutes. Because it
        # reloads in hardware, the board keeps getting woken even if a previous
        # run crashed before it could "re-arm" anything.
        if (self._r1(REG_TIMER_MODE) != TIMER_MODE_MINUTES
                or self._r1(REG_TIMER_VALUE) != interval_min):
            self._w(REG_TIMER_MODE, 0x18)          # TE=0 while changing value
            self._w(REG_TIMER_VALUE, interval_min)
            self._w(REG_TIMER_MODE, TIMER_MODE_MINUTES)
            changed = True
        return changed

    def dump(self):
        return {"ctrl1": self._r1(REG_CTRL1), "ctrl2": self.ctrl2(),
                "ram": self.ram(), "seconds": self._r1(REG_SECONDS),
                "alarms": list(self._r(REG_ALARM_SEC, 5)),
                "timer_value": self._r1(REG_TIMER_VALUE),
                "timer_mode": self._r1(REG_TIMER_MODE)}
