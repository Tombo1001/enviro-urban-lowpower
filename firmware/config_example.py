# Copy to config.py on the board and fill in.

NAME = "enviro-urban"            # used in MQTT topics, HA device name and hostname

WIFI_SSID = "your-ssid"
WIFI_PASSWORD = "your-password"
WIFI_COUNTRY = "GB"
# Optional static IP to skip DHCP (saves ~1-2 s of radio time per wake):
# (ip, netmask, gateway, dns)
WIFI_STATIC = None               # e.g. ("192.168.1.60", "255.255.255.0", "192.168.1.1", "192.168.1.1")
WIFI_TIMEOUT_S = 15

MQTT_BROKER = "192.168.1.10"     # use an IP, not a hostname: avoids a DNS lookup
MQTT_PORT = 1883
MQTT_USER = None
MQTT_PASSWORD = None
MQTT_TOPIC = "enviro/" + NAME    # state is published retained to <topic>/state
MQTT_QOS = 1                     # 1 = wait for the broker's PUBACK before powering off
HA_DISCOVERY = True              # publish Home Assistant discovery on button/power-on wakes

INTERVAL_MIN = 15                # RTC countdown timer, 1..255 minutes

# The PM sensor's fan dominates the energy budget (see README). Run it on every
# Nth wake only (1 = every wake, max 15). Plantower spec says 30 s for stable data.
PM_EVERY_N = 4
PM_WARMUP_S = 30
PM_MIN_V = 3.3                   # skip the PM sensor below this battery voltage
LOW_BATTERY_V = 2.9              # below this, skip wifi too and just go back to sleep

VSYS_CAL = 1.0                   # multiply the ADC battery reading by this (calibrate with a meter)
TEMP_OFFSET = 0.0                # degrees C added to the BME280 temperature

WATCHDOG = True                  # hardware watchdog; on a hang the board resets -> power off
MAX_AWAKE_S = 90                 # soft deadline for one wake
LED = False                      # white activity LED while awake (~1.5 mA)
LOG = True                       # append one line per wake to log.txt (rotated at 16 kB)
