"""Hands the board firmware to the Provision tab.

The firmware is plain MicroPython files. The page copies them to the board over
USB serial from the browser, or offers them as a zip to install with mpremote.
The board's config.py, with its WiFi password, is built in the browser and never
passes through this server.
"""
import io
import os
import re
import zipfile

MICROPYTHON_URL = "https://micropython.org/download/RPI_PICO_W/"
FLASH_NUKE_URL = "https://datasheets.raspberrypi.com/soft/flash_nuke.uf2"

# Copy order matters: main.py goes last, after the hardware check has passed.
SUPPORT = ["lib/urban_hw.py", "lib/pcf85063a_lite.py", "lib/bme280_lite.py",
           "lib/umqtt/simple.py", "boot.py", "diag.py"]
MAIN = "main.py"
TEMPLATE = "config_example.py"

INSTALL_TXT = """Enviro Urban low-power firmware, manual install
================================================

You need Python and mpremote on your computer:  pip install mpremote

1. Put stock MicroPython on the Pico W. Hold BOOTSEL while plugging in USB,
   then copy the RPI_PICO_W UF2 from {micropython} onto the RPI-RP2 drive.
2. Save the config.py you downloaded from the Provision tab into this folder.
3. Copy the support files and run the hardware check:

     mpremote mkdir :lib
     mpremote mkdir :lib/umqtt
     mpremote cp lib/urban_hw.py lib/pcf85063a_lite.py lib/bme280_lite.py :lib/
     mpremote cp lib/umqtt/simple.py :lib/umqtt/
     mpremote cp config.py diag.py boot.py :
     mpremote run diag.py

4. When the check passes, install the firmware itself:

     mpremote cp main.py :

To get back in later, hold STATUS for 3 seconds while pressing RESET. The white
LED stays on and the board waits for you with the watchdog off.
"""


def _files(root):
    return [f for f in SUPPORT + [MAIN, TEMPLATE] if os.path.isfile(os.path.join(root, f))]


def describe(root):
    files = _files(root)
    ready = all(f in files for f in SUPPORT + [MAIN, TEMPLATE])
    version = None
    if MAIN in files:
        with open(os.path.join(root, MAIN), encoding="utf-8") as f:
            m = re.search(r'^FW\s*=\s*"([^"]+)"', f.read(), re.M)
        version = m.group(1) if m else None
    return {
        "available": ready,
        "version": version,
        "support": [f for f in SUPPORT if f in files],
        "main": MAIN,
        "template": TEMPLATE,
        "micropython_url": MICROPYTHON_URL,
        "flash_nuke_url": FLASH_NUKE_URL,
    }


def bundle(root):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in _files(root):
            z.write(os.path.join(root, f), "enviro-urban-lowpower/" + f)
        z.writestr("enviro-urban-lowpower/INSTALL.txt", INSTALL_TXT.format(micropython=MICROPYTHON_URL))
    return buf.getvalue()


def resolve(root, rel):
    """The path of one firmware file, or None. Only the known files are served."""
    return os.path.join(root, rel) if rel in _files(root) else None
