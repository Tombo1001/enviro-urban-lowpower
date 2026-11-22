<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import BatteryCard from '../components/BatteryCard.vue'
import BoardFigure, { type Callout } from '../components/BoardFigure.vue'
import CogButton from '../components/CogButton.vue'
import SettingsDialog from '../components/SettingsDialog.vue'
import { chargeState } from '../charge'
import LineChart from '../components/LineChart.vue'
import { ago, clock, clockSec, dateTime, fix, span } from '../format'
import type { Summary } from '../types'

const props = defineProps<{ id: string; credits: string }>()
const api = computed(() => `api/devices/${encodeURIComponent(props.id)}`)
const data = ref<Summary | null>(null)
const error = ref<string | null>(null)
const now = ref(Date.now() / 1000)
let skew = 0
let poll: number | undefined
let tick: number | undefined

async function load() {
  try {
    const r = await fetch(`${api.value}/summary`, { cache: 'no-store' })
    const j = await r.json()
    if (!r.ok) throw new Error(j.error ?? r.statusText)
    skew = j.now - Date.now() / 1000
    data.value = j
    error.value = null
  } catch (e) {
    error.value = String(e)
  }
}
onMounted(() => {
  load()
  poll = window.setInterval(load, 10_000)
  tick = window.setInterval(() => (now.value = Date.now() / 1000 + skew), 1000)
})
onBeforeUnmount(() => { clearInterval(poll); clearInterval(tick) })

const latest = computed(() => data.value?.latest ?? null)
const onUsb = computed(() => latest.value?.usb === 1)
const intervalS = computed(() => (data.value?.cadence.interval_min ?? 1) * 60)
const nextDue = computed(() => (latest.value ? latest.value.ts + intervalS.value : null))
const overdue = computed(() => (latest.value ? now.value - latest.value.ts > 1.5 * intervalS.value + 45 : false))

const cadenceText = computed(() => {
  const c = data.value?.cadence
  if (!c) return ''
  const pm = c.pm_every_n ? `PM every ${c.pm_every_n === 1 ? 'wake' : c.pm_every_n + ' wakes'}` : 'PM off'
  return `every ${c.interval_min} min, ${pm}${c.source === 'assumed' ? ' (assumed)' : ''}`
})

const front = computed<Callout[]>(() => {
  const l = latest.value
  const pm = data.value?.latest_pm
  if (!l) return []
  return [
    { id: 'wake', tag: 'Wake', x: 64.5, y: 16.8, ly: 11, value: l.wake ?? '–', sub: `every ${data.value!.cadence.interval_min} min` },
    {
      id: 'pm', tag: 'PMS5003', x: 30, y: 55, ly: 38, value: fix(pm?.pm2_5, 1), unit: 'µg/m³ PM2.5',
      sub: pm ? `PM1 ${fix(pm.pm1, 1)} · PM10 ${fix(pm.pm10, 1)}${l.ts - pm.ts > 60 ? '\nread ' + clock(pm.ts) : ''}` : 'no reading yet',
    },
    { id: 'mic', tag: 'Mic', x: 67.3, y: 93, ly: 65, value: l.noise == null ? '–' : (l.noise * 1000).toFixed(0), unit: 'mV p-p' },
    {
      id: 'bme', tag: 'BME280', x: 42.9, y: 92.8, ly: 90, value: fix(l.temperature, 1), unit: '°C',
      sub: `${fix(l.humidity, 0)}% RH · ${fix(l.pressure, 0)} hPa`,
    },
  ]
})

const back = computed<Callout[]>(() => {
  const l = latest.value
  const b = data.value?.battery
  if (!l) return []
  const rssi = l.rssi
  const badReset = l.usb === 0 && l.reset_cause != null && l.reset_cause !== 'power_on'
  return [
    {
      id: 'batt', tag: 'Batt', x: 43, y: 16.5, ly: 10, value: fix(b?.v_last, 3), unit: 'V',
      sub: !b ? 'no battery reading' : b.charging ? (b.charging.full ? 'topping off' : 'charging') : onUsb.value ? `${fix(b.soc, 0)}% · read ${clock(b.v_ts)}` : `${fix(b.soc, 0)}% charge`,
      status: b?.low ? 'critical' : undefined,
    },
    {
      id: 'wifi', tag: 'WiFi', x: 50, y: 43, ly: 30, value: fix(rssi, 0), unit: 'dBm',
      sub: rssi == null ? '' : rssi > -60 ? 'strong' : rssi > -72 ? 'fair' : 'weak',
    },
    {
      id: 'rtc', tag: 'RTC', x: 82, y: 53.5, ly: 50, value: nextDue.value ? clock(nextDue.value) : '–', unit: 'next wake',
      sub: overdue.value ? 'overdue' : undefined, status: overdue.value ? 'warning' : undefined,
    },
    {
      id: 'mcu', tag: 'RP2040', x: 50, y: 62, ly: 70, value: l.awake_ms == null ? '–' : (l.awake_ms / 1000).toFixed(1), unit: 's awake',
      sub: `reset: ${l.reset_cause ?? 'unknown'}`, status: badReset ? 'warning' : undefined,
    },
    {
      id: 'usb', tag: 'Power', x: 50, y: 94, ly: 90, value: onUsb.value ? 'USB' : b?.charging ? 'Charging' : 'Battery',
      sub: onUsb.value ? `${fix(l.battery_v, 2)} V rail, not charging the cell` : b?.charging ? 'charger on the cell' : b?.sag_mv_latest != null ? `fan sag ${fix(b.sag_mv_latest, 0)} mV` : undefined,
    },
  ]
})

async function saveSettings(patch: Partial<Summary['settings']>) {
  const r = await fetch(`${api.value}/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  const j = await r.json()
  if (!r.ok) throw new Error(j.error ?? r.statusText)
  await load()
}

const narrow = ref(false)
let mq: MediaQueryList | undefined
const onMq = () => (narrow.value = !!mq?.matches)
onMounted(() => { mq = window.matchMedia('(max-width: 720px)'); onMq(); mq.addEventListener('change', onMq) })
onBeforeUnmount(() => mq?.removeEventListener('change', onMq))

const rateDialog = ref<InstanceType<typeof SettingsDialog>>()
const charge = computed(() =>
  data.value?.battery ? chargeState(data.value.battery, data.value.settings.charge_ma, now.value) : null,
)
const charger = computed<Callout[]>(() => {
  const b = data.value?.battery
  const c = charge.value
  const rate = data.value?.settings.charge_ma ?? 100
  return [
    { id: 'usb', tag: 'USB-C in', side: 'left', x: 17.3, y: 47.4, ly: 22, value: c ? (c.full ? 'Topping off' : 'Charging') : 'Idle', sub: c ? `since ${clock(c.since)}` : 'no charge seen' },
    { id: 'rate', tag: 'Rate', side: 'left', x: 46.5, y: 91.5, ly: 78, value: String(rate), unit: 'mA', sub: rate === 500 ? 'jumper closed' : 'jumper open' },
    { id: 'bat', tag: 'Bat', side: 'right', x: 81.6, y: 47.4, ly: 16, value: fix(b?.v_last, 3), unit: 'V', sub: c ? 'cell on charge' : 'cell at rest' },
    { id: 'chg', tag: 'Chg', side: 'right', x: 76, y: 87, ly: 50, value: c ? fix(c.soc, 0) : '–', unit: c ? '% counted' : undefined, sub: c ? `from ${fix(c.from, 0)}%` : undefined },
    { id: 'done', tag: 'Done', side: 'right', x: 65.7, y: 87, ly: 84, value: c ? c.text.value : '–', unit: c ? `${c.text.unit} to full` : undefined, sub: c ? `about ${clock(c.at)}` : undefined },
  ]
})

const ranges = [
  { key: '1h', label: '1 h', s: 3600 },
  { key: '6h', label: '6 h', s: 6 * 3600 },
  { key: '24h', label: '24 h', s: 86400 },
  { key: '7d', label: '7 d', s: 7 * 86400 },
  { key: 'all', label: 'All', s: Infinity },
]
const range = ref('24h')
const domain = computed<[number, number]>(() => {
  const t = data.value?.series?.t ?? []
  if (!t.length) return [0, 1]
  const end = t[t.length - 1]
  const s = ranges.find((r) => r.key === range.value)!.s
  const start = Math.max(t[0], end - s)
  return end - start < 60 ? [start - 30, end + 30] : [start, end]
})

const C1 = 'var(--series-1)'
const C2 = 'var(--series-2)'
const C3 = 'var(--series-3)'

const flagged = computed(() => (data.value?.events ?? []).filter((e) => e.severity !== 'notice'))
const sevLabel: Record<string, string> = { critical: 'Critical', serious: 'Serious', warning: 'Warning', notice: 'Notice' }
</script>

<template>
  <div class="device">
    <header class="top">
      <div>
        <h2 class="name">{{ data?.settings.name || id }}</h2>
        <p class="meta">
          <span class="pill">{{ id }}</span>
          <span v-if="data?.device.fw">{{ data.device.fw }}</span>
          <span v-if="cadenceText">{{ cadenceText }}</span>
        </p>
      </div>
      <div v-if="latest" class="status" :class="{ late: overdue }">
        <span class="lamp" />
        <div>
          <b>{{ overdue ? 'Overdue' : 'Reporting' }}</b>
          <span class="num">last wake {{ ago(now - latest.ts) }} at {{ clockSec(latest.ts) }}</span>
        </div>
      </div>
    </header>

    <p v-if="error" class="banner">Can't reach the dashboard server. {{ error }}</p>
    <p v-if="data && !latest" class="card">No wakes logged yet. The page fills in when the first one arrives.</p>

    <template v-if="data && latest">
      <div class="lead">
        <div class="col">
        <section class="card diagram">
          <div class="eyebrow">Last reading, {{ dateTime(latest.ts) }}</div>
          <div class="boards">
            <BoardFigure src="img/board-front" alt="Enviro Urban, front" caption="Front" labels="left" :callouts="front" :pulse="latest.ts" />
            <BoardFigure src="img/board-back" alt="Enviro Urban, back, with the Pico W" caption="Back" labels="right" :callouts="back" :pulse="latest.ts" />
          </div>
          <div class="chargerhead">
            <span class="eyebrow">Charger, Adafruit Micro Lipo{{ charge ? '' : ', not charging' }}</span>
            <CogButton label="Set charge rate" @click="rateDialog?.open()" />
          </div>
          <BoardFigure
            src="img/charger" alt="Adafruit Micro Lipo charger" caption="" labels="right" :callouts="charger" :pulse="latest.ts"
            :img-left="narrow ? 39.5 : 40.5" :img-width="narrow ? 21 : 19" :img-aspect="490 / 390" :aspect="narrow ? 1.5 : 3.6" :dim="!charge"
          />
          <SettingsDialog
            ref="rateDialog" title="Charge rate" unit="mA" :options="[100, 500]"
            :value="data.settings.charge_ma" :save="(v) => saveSettings({ charge_ma: v })"
          >
            The Micro Lipo charges at 100 mA unless its 500 mA jumper is soldered closed. The board can't measure
            charge current, so time to full is counted from this setting.
          </SettingsDialog>
        </section>
      <section class="card events" :class="{ quiet: !flagged.length }">
        <div class="eyebrow">Faults and notices</div>
        <p v-if="!data.events.length" class="none">
          Nothing flagged in {{ data.stats.wakes }} wakes over {{ span((data.stats.span_h ?? 0) * 3600) }}.
        </p>
        <ul v-else tabindex="0" :aria-label="`${data.events.length} faults and notices, newest first`">
          <li v-for="(e, i) in data.events" :key="i">
            <span class="sev" :class="e.severity"><i />{{ sevLabel[e.severity] }}</span>
            <span class="what">{{ e.text }}<code v-if="e.detail">{{ e.detail }}</code></span>
            <time class="num">{{ dateTime(e.ts) }}</time>
          </li>
        </ul>
        <p v-if="data.events.length > 4" class="more">{{ data.events.length }} entries, newest first. Scroll for older ones.</p>
      </section>

        </div>
        <BatteryCard v-if="data.battery" :device-id="id" :battery="data.battery" :cadence="data.cadence" :config="data.config" :settings="data.settings" :save-settings="saveSettings" :next-due="nextDue" :on-usb="onUsb" :now="now" @sent="load" />
        <section v-else class="card">
          <div class="eyebrow">Battery life left</div>
          <p>No battery readings yet. Every wake so far was on USB.</p>
        </section>
      </div>

      <div class="rangebar">
        <h2>History</h2>
        <div class="chips" role="group" aria-label="Time range">
          <button v-for="r in ranges" :key="r.key" :class="{ on: range === r.key }" @click="range = r.key">{{ r.label }}</button>
        </div>
      </div>
      <div v-if="data.series" class="charts">
        <LineChart
          title="Battery voltage" unit="V" :digits="3" :t="data.series.t" :domain="domain" :min-span="0.03"
          :series="[
            { name: 'Before WiFi', color: C1, values: data.series.battery_v },
            { name: 'Fan running', color: C2, values: data.series.battery_v_load, dots: true },
          ]"
          :ref-lines="[{ value: 3.6, label: 'low, 3.6 V' }, { value: 3.5, label: 'empty, 3.5 V' }]"
          note="Battery wakes only. Wakes on USB are left out."
        />
        <LineChart
          v-if="data.pm_series" title="Particulates" unit="µg/m³" :digits="1" :t="data.pm_series.t" :domain="domain" :min-span="4"
          :series="[
            { name: 'PM1', color: C1, values: data.pm_series.pm1 },
            { name: 'PM2.5', color: C2, values: data.pm_series.pm2_5 },
            { name: 'PM10', color: C3, values: data.pm_series.pm10 },
          ]"
        />
        <LineChart title="Temperature" unit="°C" :digits="1" :t="data.series.t" :domain="domain" :min-span="2" :series="[{ name: 'Temperature', color: C1, values: data.series.temperature }]" />
        <LineChart title="Humidity" unit="% RH" :digits="0" :t="data.series.t" :domain="domain" :min-span="6" :series="[{ name: 'Humidity', color: C1, values: data.series.humidity }]" />
        <LineChart title="Pressure" unit="hPa" :digits="1" :t="data.series.t" :domain="domain" :min-span="1.5" :series="[{ name: 'Pressure', color: C1, values: data.series.pressure }]" />
        <LineChart title="Noise" unit="V p-p" :digits="3" :t="data.series.t" :domain="domain" :min-span="0.03" :series="[{ name: 'Noise', color: C1, values: data.series.noise }]" />
        <LineChart title="WiFi signal" unit="dBm" :digits="0" :t="data.series.t" :domain="domain" :min-span="15" :series="[{ name: 'RSSI', color: C1, values: data.series.rssi }]" />
        <LineChart title="Time awake per wake" unit="s" :digits="0" :t="data.series.t" :domain="domain" :min-span="6" :series="[{ name: 'Awake', color: C1, values: data.series.awake_s }]" note="About 5 s normally, about 40 s when the PM fan runs." />
      </div>

      <details class="card table">
        <summary>Last {{ data.recent.length }} wakes as a table</summary>
        <div class="scroll">
          <table class="num">
            <thead>
              <tr><th>Time</th><th>Power</th><th>V</th><th>V fan</th><th>°C</th><th>% RH</th><th>hPa</th><th>Noise</th><th>dBm</th><th>Awake s</th><th>Wake</th><th>Reset</th></tr>
            </thead>
            <tbody>
              <tr v-for="w in data.recent" :key="w.ts">
                <td>{{ clockSec(w.ts) }}</td>
                <td>{{ w.usb ? 'USB' : 'battery' }}</td>
                <td>{{ fix(w.battery_v, 3) }}</td>
                <td>{{ fix(w.battery_v_load, 3) }}</td>
                <td>{{ fix(w.temperature, 2) }}</td>
                <td>{{ fix(w.humidity, 1) }}</td>
                <td>{{ fix(w.pressure, 2) }}</td>
                <td>{{ fix(w.noise, 3) }}</td>
                <td>{{ fix(w.rssi, 0) }}</td>
                <td>{{ w.awake_ms == null ? '–' : (w.awake_ms / 1000).toFixed(1) }}</td>
                <td>{{ w.wake }}</td>
                <td>{{ w.reset_cause }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>

      <footer>
        {{ data.stats.wakes }} wakes logged since {{ dateTime(data.stats.first_ts ?? 0) }},
        {{ data.stats.battery_wakes }} on battery, {{ data.stats.pm_wakes }} with a PM reading,
        {{ data.stats.missed_gaps }} missed-wake gaps. {{ credits }}
      </footer>
    </template>
  </div>
</template>

<style scoped>
.device { display: flex; flex-direction: column; gap: 16px; }
.top { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; }
.name { font-size: 22px; font-weight: 800; letter-spacing: -0.02em; }
.meta { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13px; color: var(--ink-2); margin-top: 4px; }
.status { display: flex; gap: 10px; align-items: center; font-size: 13px; color: var(--ink-2); }
.status div { display: flex; flex-direction: column; }
.status b { color: var(--ink); font-size: 14px; }
.lamp { width: 12px; height: 12px; border-radius: 50%; background: var(--good); box-shadow: 0 0 0 4px rgba(12, 163, 12, 0.15); }
.late .lamp { background: var(--warning); border-radius: 2px; box-shadow: 0 0 0 4px rgba(250, 178, 25, 0.2); }
.banner { background: #fbeaea; border: 1px solid var(--critical); border-radius: 10px; padding: 10px 14px; font-size: 13.5px; }
.lead { display: grid; grid-template-columns: minmax(0, 1.85fr) minmax(320px, 1fr); gap: 16px; align-items: start; }
.col { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.diagram { background: var(--white); }
.chargerhead { display: flex; justify-content: space-between; align-items: center; margin: 22px 0 10px; padding-top: 16px; border-top: 1px solid var(--grid); }
.boards { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 14px; }
/* Four rows show. Older entries scroll. */
.events ul { list-style: none; margin: 10px 0 0; padding: 0 10px 0 0; max-height: 150px; overflow-y: auto; scrollbar-gutter: stable; }
.events ul:focus-visible { outline: 2px solid var(--series-1); outline-offset: 2px; }
.events li:first-child { border-top: 0; }
.more { margin-top: 8px; }
.events li { display: grid; grid-template-columns: 96px 1fr auto; gap: 12px; padding: 8px 0; border-top: 1px solid var(--grid); font-size: 13.5px; align-items: baseline; }
.events time, .more { color: var(--muted); font-size: 12.5px; }
.none { margin-top: 8px; font-size: 13.5px; color: var(--ink-2); }
.sev { font-weight: 650; font-size: 12.5px; display: flex; align-items: center; gap: 7px; }
.sev i { width: 9px; height: 9px; flex: none; }
.sev.critical i { background: var(--critical); transform: rotate(45deg); }
.sev.serious i { background: var(--serious); transform: rotate(45deg); }
.sev.warning i { background: var(--warning); border-radius: 2px; }
.sev.notice i { background: var(--axis); border-radius: 50%; }
code { font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; font-size: 12px; background: #f0efec; border-radius: 4px; padding: 1px 5px; margin-left: 8px; overflow-wrap: anywhere; }
.rangebar { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; }
h2 { font-size: 17px; font-weight: 700; }
.chips { display: flex; gap: 6px; }
.chips button { border: 1px solid var(--border); background: var(--white); border-radius: 999px; padding: 4px 12px; font-size: 13px; cursor: pointer; }
.chips button.on { background: var(--ink); color: #fff; border-color: var(--ink); }
.chips button:focus-visible { outline: 2px solid var(--series-1); outline-offset: 2px; }
.charts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.table summary { cursor: pointer; font-size: 13.5px; font-weight: 600; }
.scroll { overflow-x: auto; margin-top: 10px; }
table { border-collapse: collapse; font-size: 12.5px; width: 100%; white-space: nowrap; }
th, td { text-align: right; padding: 5px 10px; border-top: 1px solid var(--grid); }
th { color: var(--muted); font-weight: 600; border-top: 0; }
th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) { text-align: left; }
footer { font-size: 12px; color: var(--muted); }
@media (max-width: 1040px) {
  .lead { grid-template-columns: minmax(0, 1fr); }
  .lead > :nth-child(2) { order: -1; }
}
@media (max-width: 720px) {
  .boards { grid-template-columns: 1fr; gap: 26px; }
  .charts { grid-template-columns: minmax(0, 1fr); }
  .events li { grid-template-columns: 1fr; gap: 2px; }
  .events ul { max-height: 300px; }
}
</style>
