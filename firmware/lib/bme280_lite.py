# Minimal BME280 driver: forced mode, 1x oversampling, float compensation
# (Bosch BME280 datasheet section 8.1). Forced mode means the sensor sleeps
# between readings, and the Pico is off between readings, so there is almost no
# self-heating - unlike the stock firmware on USB power.
import time
import struct


class BME280:
    def __init__(self, i2c, addr=0x77):
        self.i2c = i2c
        self.addr = addr
        chip = self._r(0xD0, 1)[0]
        if chip != 0x60:
            raise OSError("BME280 chip id 0x%02x, expected 0x60" % chip)
        c = self._r(0x88, 26)
        (self.T1, self.T2, self.T3,
         self.P1, self.P2, self.P3, self.P4, self.P5,
         self.P6, self.P7, self.P8, self.P9) = struct.unpack("<HhhHhhhhhhhh", c[:24])
        self.H1 = c[25]
        h = self._r(0xE1, 7)
        self.H2 = struct.unpack("<h", h[0:2])[0]
        self.H3 = h[2]
        h4 = (h[3] << 4) | (h[4] & 0x0F)
        h5 = (h[5] << 4) | (h[4] >> 4)
        self.H4 = h4 - 4096 if h4 & 0x800 else h4
        self.H5 = h5 - 4096 if h5 & 0x800 else h5
        self.H6 = h[6] - 256 if h[6] & 0x80 else h[6]

    def _r(self, reg, n):
        return self.i2c.readfrom_mem(self.addr, reg, n)

    def _w(self, reg, val):
        self.i2c.writeto_mem(self.addr, reg, bytes([val]))

    def read(self, timeout_ms=200):
        """Returns (temperature C, humidity %RH, pressure hPa)."""
        self._w(0xF2, 0x01)                         # osrs_h x1 (must precede ctrl_meas)
        self._w(0xF4, (1 << 5) | (1 << 2) | 0x01)   # osrs_t x1, osrs_p x1, forced
        t0 = time.ticks_ms()
        time.sleep_ms(10)
        while self._r(0xF3, 1)[0] & 0x08:           # measuring
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                raise OSError("BME280 measurement timeout")
            time.sleep_ms(2)
        d = self._r(0xF7, 8)
        adc_p = (d[0] << 12) | (d[1] << 4) | (d[2] >> 4)
        adc_t = (d[3] << 12) | (d[4] << 4) | (d[5] >> 4)
        adc_h = (d[6] << 8) | d[7]

        v1 = (adc_t / 16384.0 - self.T1 / 1024.0) * self.T2
        v2 = (adc_t / 131072.0 - self.T1 / 8192.0) ** 2 * self.T3
        t_fine = v1 + v2
        temp = t_fine / 5120.0

        v1 = t_fine / 2.0 - 64000.0
        v2 = v1 * v1 * self.P6 / 32768.0
        v2 = v2 + v1 * self.P5 * 2.0
        v2 = v2 / 4.0 + self.P4 * 65536.0
        v1 = (self.P3 * v1 * v1 / 524288.0 + self.P2 * v1) / 524288.0
        v1 = (1.0 + v1 / 32768.0) * self.P1
        if v1 == 0:
            press = 0.0
        else:
            p = 1048576.0 - adc_p
            p = (p - v2 / 4096.0) * 6250.0 / v1
            v1 = self.P9 * p * p / 2147483648.0
            v2 = p * self.P8 / 32768.0
            press = (p + (v1 + v2 + self.P7) / 16.0) / 100.0

        h = t_fine - 76800.0
        h = (adc_h - (self.H4 * 64.0 + self.H5 / 16384.0 * h)) * (
            self.H2 / 65536.0 * (1.0 + self.H6 / 67108864.0 * h *
                                 (1.0 + self.H3 / 67108864.0 * h)))
        h = h * (1.0 - self.H1 * h / 524288.0)
        hum = min(100.0, max(0.0, h))
        return temp, hum, press
