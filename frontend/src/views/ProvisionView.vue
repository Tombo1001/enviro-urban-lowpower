<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { RawRepl, serialSupported } from '../rawrepl'
import type { Overview } from '../types'

const props = defineProps<{ overview: Overview }>()
const fw = computed(() => props.overview.firmware)

function nextName() {
  const taken = new Set(props.overview.devices.map((d) => d.id))
  for (let n = 1; n < 1000; n++) {
    const name = `enviro-${String(n).padStart(3, '0')}`
    if (!taken.has(name)) return name
  }
  return 'enviro-urban'
}
const prefix = computed(() => props.overview.mqtt.topic.split('/')[0] || 'enviro')

const form = reactive({
  NAME: nextName(),
  WIFI_SSID: '',
  WIFI_PASSWORD: '',
  WIFI_COUNTRY: 'GB',
  MQTT_BROKER: props.overview.mqtt.host ?? '',
  MQTT_PORT: props.overview.mqtt.port ?? 1883,
  MQTT_USER: '',
  MQTT_PASSWORD: '',
  INTERVAL_MIN: 15,
  PM_EVERY_N: 4,
  HA_DISCOVERY: true,
})

const problems = computed(() => {
  const out: string[] = []
  if (!/^[A-Za-z0-9_-]{1,32}$/.test(form.NAME)) out.push('Name: letters, digits, dash or underscore, up to 32.')
  if (props.overview.devices.some((d) => d.id === form.NAME)) out.push(`A board called ${form.NAME} already reports here. A second one with that name would mix their readings.`)
  if (!form.WIFI_SSID) out.push('WiFi network name is empty.')
  if (!/^[A-Z]{2}$/.test(form.WIFI_COUNTRY)) out.push('WiFi country is a two-letter code, such as GB.')
  if (!form.MQTT_BROKER) out.push('MQTT broker address is empty.')
  if (!(form.INTERVAL_MIN >= 1 && form.INTERVAL_MIN <= 255)) out.push('Wake interval is 1 to 255 minutes.')
  if (!(form.PM_EVERY_N >= 1 && form.PM_EVERY_N <= 15)) out.push('PM every is 1 to 15 wakes.')
  return out
})

/** A value as a Python literal. JSON strings are valid Python strings. */
function py(v: string | number | boolean | null) {
  if (v === null || v === '') return 'None'
  if (typeof v === 'boolean') return v ? 'True' : 'False'
  return typeof v === 'number' ? String(v) : JSON.stringify(v)
}

/** Fills the firmware's own config_example.py, so the two can't drift apart. */
async function buildConfig() {
  const r = await fetch(`api/firmware/${fw.value.template}`, { cache: 'no-store' })
  if (!r.ok) throw new Error('The firmware template is missing from the server.')
  let text = await r.text()
  const values: Record<string, string> = {
    NAME: py(form.NAME),
    WIFI_SSID: py(form.WIFI_SSID),
    WIFI_PASSWORD: py(form.WIFI_PASSWORD),
    WIFI_COUNTRY: py(form.WIFI_COUNTRY),
    MQTT_BROKER: py(form.MQTT_BROKER),
    MQTT_PORT: py(Number(form.MQTT_PORT)),
    MQTT_USER: py(form.MQTT_USER),
    MQTT_PASSWORD: py(form.MQTT_PASSWORD),
    MQTT_TOPIC: `${py(prefix.value + '/')} + NAME`,
    INTERVAL_MIN: py(Number(form.INTERVAL_MIN)),
    PM_EVERY_N: py(Number(form.PM_EVERY_N)),
    HA_DISCOVERY: py(form.HA_DISCOVERY),
  }
  for (const [key, value] of Object.entries(values)) {
    const line = new RegExp(`^${key} = .*?(\\s+#.*)?$`, 'm')
    if (!line.test(text)) throw new Error(`The firmware template has no ${key} setting.`)
    text = text.replace(line, (_m, comment) => `${key} = ${value}${comment ?? ''}`)
  }
  return text.replace(/^# Copy to config\.py.*$/m, "# Written by the dashboard's Provision tab.")
}

async function downloadConfig() {
  log.value = ''
  try {
    const blob = new Blob([await buildConfig()], { type: 'text/x-python' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'config.py'
    a.click()
    URL.revokeObjectURL(a.href)
  } catch (e) {
    fail(e)
  }
}

// ---- install over USB --------------------------------------------------------
const canSerial = serialSupported()
const secure = window.isSecureContext
type Phase = 'idle' | 'copying' | 'checking' | 'checked' | 'finishing' | 'done'
const phase = ref<Phase>('idle')
const log = ref('')
const error = ref<string | null>(null)
const checkPassed = ref<boolean | null>(null)
let repl: RawRepl | null = null

const say = (line: string) => (log.value += line.endsWith('\n') ? line : line + '\n')
function fail(e: unknown) {
  error.value = e instanceof Error ? e.message : String(e)
}

async function fetchFile(path: string) {
  const r = await fetch(`api/firmware/${path}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`Could not fetch ${path} from the server.`)
  return new Uint8Array(await r.arrayBuffer())
}

async function install() {
  error.value = null
  log.value = ''
  checkPassed.value = null
  try {
    const config = new TextEncoder().encode(await buildConfig())
    repl = await RawRepl.connect()
    phase.value = 'copying'
    say('Connected. Stopping whatever is running…')
    await repl.enter()
    const version = await repl.exec("import sys\nprint(sys.implementation.name, '.'.join(str(x) for x in sys.implementation.version[:3]), sys.platform)\n")
    say(`Board runs ${version.trim()}`)
    if (!version.includes('micropython')) throw new Error('This is not MicroPython. Flash the MicroPython UF2 first (step 2).')
    await repl.mkdir('lib')
    await repl.mkdir('lib/umqtt')
    for (const path of fw.value.support) {
      await repl.writeFile(path, await fetchFile(path))
      say(`Copied ${path}`)
    }
    await repl.writeFile('config.py', config)
    say('Copied config.py')

    phase.value = 'checking'
    say('\nRunning the hardware check. The particulate sensor warms up for a while, so allow a minute or two.\n')
    const out = await repl.exec("exec(open('diag.py').read())\n", 240_000, (t) => (log.value += t))
    checkPassed.value = out.includes('SUMMARY: all checks passed')
    phase.value = 'checked'
  } catch (e) {
    fail(e)
    await repl?.close()
    repl = null
    phase.value = 'idle'
  }
}

async function finish() {
  if (!repl) return
  error.value = null
  try {
    phase.value = 'finishing'
    await repl.writeFile(fw.value.main, await fetchFile(fw.value.main))
    say(`\nCopied ${fw.value.main}. Restarting the board.`)
    await repl.reset()
    await repl.close()
    repl = null
    phase.value = 'done'
  } catch (e) {
    fail(e)
    phase.value = 'checked'
  }
}

async function abandon() {
  await repl?.close()
  repl = null
  phase.value = 'idle'
}
</script>

<template>
  <div class="provision">
    <section class="card intro">
      <h2>Set up a board</h2>
      <p>
        This puts the low-power firmware<template v-if="fw.version"> ({{ fw.version }})</template> on an Enviro Urban
        and points it at your WiFi and MQTT broker. Once it sends its first reading it gets a tab here by itself.
      </p>
      <p v-if="!fw.available" class="warn">The firmware files are missing from this server, so there is nothing to install.</p>
    </section>

    <section class="card">
      <h2><span class="n">1</span>Board settings</h2>
      <p class="lede">These become the board's <code>config.py</code>. The file is built in your browser, so the WiFi password is not sent to the dashboard server.</p>
      <div class="grid">
        <label>Name<input v-model.trim="form.NAME" type="text" maxlength="32" autocomplete="off" /><small>Its tab name and MQTT topic, <code>{{ prefix }}/{{ form.NAME || '…' }}</code></small></label>
        <label>WiFi network<input v-model="form.WIFI_SSID" type="text" autocomplete="off" /><small>2.4 GHz only. The Pico W has no 5 GHz radio.</small></label>
        <label>WiFi password<input v-model="form.WIFI_PASSWORD" type="password" autocomplete="new-password" /></label>
        <label>WiFi country<input v-model.trim="form.WIFI_COUNTRY" type="text" maxlength="2" class="short" /></label>
        <label>MQTT broker<input v-model.trim="form.MQTT_BROKER" type="text" autocomplete="off" /><small>An IP address saves a DNS lookup on every wake.</small></label>
        <label>MQTT port<input v-model.number="form.MQTT_PORT" type="number" min="1" max="65535" class="short" /></label>
        <label>MQTT user<input v-model="form.MQTT_USER" type="text" autocomplete="off" /><small>Leave empty if the broker needs no login.</small></label>
        <label>MQTT password<input v-model="form.MQTT_PASSWORD" type="password" autocomplete="new-password" /></label>
        <label>Wake every<span class="unit"><input v-model.number="form.INTERVAL_MIN" type="number" min="1" max="255" class="short" /> min</span></label>
        <label>PM reading every<span class="unit"><input v-model.number="form.PM_EVERY_N" type="number" min="1" max="15" class="short" /> wakes</span><small>The fan is most of the power budget.</small></label>
        <label class="check"><input v-model="form.HA_DISCOVERY" type="checkbox" /> Announce sensors to Home Assistant</label>
      </div>
      <ul v-if="problems.length" class="problems">
        <li v-for="p in problems" :key="p">{{ p }}</li>
      </ul>
    </section>

    <section class="card">
      <h2><span class="n">2</span>Put MicroPython on the Pico W</h2>
      <p class="lede">The firmware runs on stock MicroPython, not Pimoroni's build. A browser can't write to the Pico's boot drive, so this step is by hand.</p>
      <ol>
        <li>Unplug the battery. Hold <b>BOOTSEL</b> on the Pico W and plug in USB. A drive called <b>RPI-RP2</b> appears.</li>
        <li>Coming from Pimoroni's firmware? Wipe the old files first. Copy <a :href="fw.flash_nuke_url" rel="noopener">flash_nuke.uf2</a> onto the drive, wait for it to come back, and hold BOOTSEL again if it doesn't.</li>
        <li>Download the latest stable <b>RPI_PICO_W</b> build from <a :href="fw.micropython_url" target="_blank" rel="noopener">micropython.org</a> and copy the .uf2 onto the drive. The board restarts by itself.</li>
      </ol>
    </section>

    <section class="card">
      <h2><span class="n">3</span>Install the firmware</h2>
      <template v-if="canSerial">
        <p class="lede">
          Copies the drivers and your settings over USB, then runs the hardware check. The firmware itself goes on last, once you have seen the check's result.
          Re-doing a board that already runs this firmware? Hold <b>STATUS</b> for 3 seconds while pressing <b>RESET</b> first. The white LED stays on.
        </p>
        <div class="actions">
          <button v-if="phase === 'idle' || phase === 'done'" class="primary" :disabled="!fw.available || problems.length > 0" @click="install">
            {{ phase === 'done' ? 'Set up another board' : 'Connect and install' }}
          </button>
          <span v-if="phase === 'copying'" class="working"><i class="spin" />Copying files…</span>
          <span v-if="phase === 'checking'" class="working"><i class="spin" />Running the hardware check…</span>
          <span v-if="phase === 'finishing'" class="working"><i class="spin" />Installing {{ fw.main }}…</span>
          <template v-if="phase === 'checked'">
            <span class="verdict" :class="checkPassed ? 'pass' : 'fail'"><i />{{ checkPassed ? 'Hardware check passed' : 'Hardware check found problems' }}</span>
            <button class="primary" @click="finish">{{ checkPassed ? 'Finish the install' : 'Install anyway' }}</button>
            <button class="ghost" @click="abandon">Stop here</button>
          </template>
        </div>
        <p v-if="phase === 'done'" class="done">
          Installed. Unplug USB and connect the battery. The board wakes, sends a reading and powers itself off. Its tab appears here within a few seconds of that first reading.
        </p>
      </template>
      <template v-else>
        <p class="warn">
          <template v-if="!secure">Installing from the browser needs this page on HTTPS or on <code>localhost</code>. Browsers switch USB serial off on plain HTTP.</template>
          <template v-else>This browser has no USB serial support. Chrome and Edge on a desktop do.</template>
          Install by hand instead, with the two downloads below.
        </p>
      </template>
      <p v-if="error" class="warn" role="alert">{{ error }}</p>
      <pre v-if="log" class="log" aria-live="polite">{{ log }}</pre>

      <details :open="!canSerial">
        <summary>Install by hand with mpremote</summary>
        <p class="lede">Download both, put <code>config.py</code> in the unzipped folder, and follow <code>INSTALL.txt</code> inside it.</p>
        <div class="actions">
          <a class="ghost" href="api/firmware/bundle.zip" download>Download the firmware zip</a>
          <button class="ghost" :disabled="problems.length > 0" @click="downloadConfig">Download config.py</button>
        </div>
      </details>
    </section>
  </div>
</template>

<style scoped>
.provision { display: flex; flex-direction: column; gap: 16px; max-width: 900px; }
h2 { font-size: 16px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
.n { display: inline-grid; place-items: center; width: 24px; height: 24px; border-radius: 50%; background: var(--ink); color: #fff; font-size: 13px; }
.intro p, .lede { font-size: 13.5px; color: var(--ink-2); margin-top: 6px; max-width: 72ch; }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 20px; margin-top: 14px; }
label { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px; color: var(--ink-2); font-weight: 600; }
label small { font-weight: 400; color: var(--muted); font-size: 12px; }
label.check { flex-direction: row; align-items: center; gap: 8px; font-weight: 500; font-size: 13.5px; color: var(--ink); }
input[type='text'], input[type='password'], input[type='number'] {
  font: inherit;
  font-weight: 400;
  color: var(--ink);
  padding: 7px 9px;
  border: 1px solid var(--axis);
  border-radius: 8px;
  background: var(--white);
  width: 100%;
}
input.short { width: 96px; }
.unit { display: inline-flex; align-items: center; gap: 7px; font-weight: 400; }
ol { margin: 12px 0 0; padding-left: 20px; display: grid; gap: 8px; font-size: 13.5px; max-width: 72ch; }
a { color: var(--series-1); }
code { font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; font-size: 12.5px; background: #f0efec; border-radius: 4px; padding: 1px 5px; }
.problems { margin: 14px 0 0; padding-left: 18px; font-size: 13px; color: #8a2a2a; display: grid; gap: 3px; }
.warn { margin-top: 10px; font-size: 13.5px; background: #fdf3d7; border: 1px solid var(--warning); border-radius: 10px; padding: 9px 13px; max-width: 72ch; }
.done { margin-top: 10px; font-size: 13.5px; background: #e9f6e9; border: 1px solid var(--good); border-radius: 10px; padding: 9px 13px; max-width: 72ch; }
.actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 14px; }
.primary, .ghost { border-radius: 8px; padding: 8px 15px; font-size: 13.5px; font-weight: 600; cursor: pointer; text-decoration: none; font-family: inherit; }
.primary { background: var(--series-1); color: #fff; border: 1px solid var(--series-1); }
.ghost { background: var(--white); color: var(--ink); border: 1px solid var(--border); }
.primary:disabled, .ghost:disabled { opacity: 0.45; cursor: not-allowed; }
button:focus-visible, a:focus-visible, input:focus-visible { outline: 2px solid var(--series-1); outline-offset: 2px; }
.working { display: inline-flex; align-items: center; gap: 8px; font-size: 13.5px; }
.spin { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--series-1); border-top-color: transparent; animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.verdict { display: inline-flex; align-items: center; gap: 7px; font-size: 13.5px; font-weight: 650; }
.verdict i { width: 10px; height: 10px; border-radius: 50%; background: var(--good); }
.verdict.fail i { background: var(--critical); border-radius: 0; transform: rotate(45deg); }
.log {
  margin: 14px 0 0;
  background: #14140f;
  color: #e9e8e1;
  border-radius: 10px;
  padding: 12px 14px;
  font: 12px/1.5 ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  max-height: 340px;
  overflow: auto;
  white-space: pre-wrap;
}
details { margin-top: 16px; border-top: 1px solid var(--grid); padding-top: 12px; }
summary { cursor: pointer; font-size: 13.5px; font-weight: 600; }
@media (max-width: 640px) { .grid { grid-template-columns: minmax(0, 1fr); } }
@media (prefers-reduced-motion: reduce) { .spin { animation: none; } }
</style>
