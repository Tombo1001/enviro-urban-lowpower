# Runs before main.py. Grab the power latch (HOLD_VSYS_EN, GP2) as early as
# possible. Until this pin goes high, the board is only powered because the RTC
# INT line (or the STATUS button) is holding VSYS_EN on.
from machine import Pin
Pin(2, Pin.OUT, value=1)
