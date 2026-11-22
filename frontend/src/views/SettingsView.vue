<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { ago, fix } from '../format'
import type { Overview } from '../types'

const props = defineProps<{ overview: Overview; now: number }>()
const emit = defineEmits<{ changed: [] }>()

interface Draft { name: string; capacity_mah: number; charge_ma: number }
const drafts = reactive<Record<string, Draft>>({})
const defaults = reactive({ capacity_mah: props.overview.defaults.capacity_mah, charge_ma: props.overview.defaults.charge_ma })
const busy = ref<string | null>(null)
const message = ref<{ ok: boolean; text: string } | null>(null)

// Fill a row's draft once, so a refresh doesn't overwrite what is being typed.
watch(
  () => props.overview.devices,
  (list) => {
    for (const b of list) {
      if (!drafts[b.id]) drafts[b.id] = { name: b.name ?? '', capacity_mah: b.capacity_mah, charge_ma: b.charge_ma }
    }
  },
  { immediate: true },
)

async function post(url: string, body: object, what: string) {
  busy.value = what
  message.value = null
  try {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const j = await r.json()
    if (!r.ok) throw new Error(j.error ?? r.statusText)
    message.value = { ok: true, text: `Saved ${what}.` }
    emit('changed')
  } catch (e) {
    message.value = { ok: false, text: `Not saved. ${e instanceof Error ? e.message : e}` }
  } finally {
    busy.value = null
  }
}
const saveBoard = (id: string) => post(`api/devices/${encodeURIComponent(id)}/settings`, drafts[id], id)
const saveDefaults = () => post('api/settings', defaults, 'the defaults')
</script>

<template>
  <div class="settings">
    <p v-if="message" class="msg" :class="{ bad: !message.ok }" role="status">{{ message.text }}</p>

    <section class="card">
      <h2>Boards</h2>
      <p class="lede">
        A board appears here, and gets its own tab, the first time one of its messages arrives on <code>{{ overview.mqtt.topic }}</code>.
      </p>
      <p v-if="!overview.devices.length" class="empty">None yet. Set one up on the Provision tab.</p>
      <div v-else class="scroll">
        <table>
          <thead>
            <tr><th>Board</th><th>Display name</th><th>Battery</th><th>Charger</th><th>Last wake</th><th>Firmware</th><th /></tr>
          </thead>
          <tbody>
            <tr v-for="b in overview.devices" :key="b.id">
              <td><a :href="`#/board/${encodeURIComponent(b.id)}`">{{ b.id }}</a></td>
              <td><input v-model.trim="drafts[b.id].name" type="text" maxlength="40" :placeholder="b.id" :aria-label="`Display name for ${b.id}`" /></td>
              <td>
                <span class="field"><input v-model.number="drafts[b.id].capacity_mah" type="number" min="100" max="20000" step="50" :aria-label="`Battery capacity for ${b.id}`" /> mAh</span>
              </td>
              <td>
                <select v-model.number="drafts[b.id].charge_ma" :aria-label="`Charger rate for ${b.id}`">
                  <option :value="100">100 mA</option>
                  <option :value="500">500 mA</option>
                </select>
              </td>
              <td class="num">{{ b.last_ts ? ago(now - b.last_ts) : 'never' }}<span v-if="b.battery_v" class="dim"> · {{ fix(b.battery_v, 2) }} V</span></td>
              <td>{{ b.fw ?? '–' }}</td>
              <td><button class="primary" :disabled="busy === b.id" @click="saveBoard(b.id)">Save</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <h2>Defaults for new boards</h2>
      <p class="lede">Used until a board has its own values. Capacity sets the mAh figures, the estimate before a fit exists, and the charge time.</p>
      <div class="row">
        <label>Battery capacity <span class="field"><input v-model.number="defaults.capacity_mah" type="number" min="100" max="20000" step="50" /> mAh</span></label>
        <label>Charger rate
          <select v-model.number="defaults.charge_ma">
            <option :value="100">100 mA</option>
            <option :value="500">500 mA</option>
          </select>
        </label>
        <button class="primary" :disabled="busy === 'the defaults'" @click="saveDefaults">Save</button>
      </div>
    </section>

    <section class="card">
      <h2>MQTT</h2>
      <dl>
        <div><dt>Broker</dt><dd>{{ overview.mqtt.configured ? `${overview.mqtt.host}:${overview.mqtt.port}` : 'not set' }}</dd></div>
        <div><dt>Topic filter</dt><dd><code>{{ overview.mqtt.topic }}</code></dd></div>
        <div>
          <dt>Logging</dt>
          <dd v-if="overview.mqtt.mode === 'file'">Off. This server reads a log another process writes.</dd>
          <dd v-else-if="!overview.mqtt.configured">Off. Set <code>MQTT_HOST</code> and restart the container.</dd>
          <dd v-else-if="overview.mqtt.connected">
            <i class="lamp ok" />Connected for {{ ago(now - (overview.mqtt.since ?? now)).replace(' ago', '') }}, {{ overview.mqtt.messages }} messages logged
          </dd>
          <dd v-else><i class="lamp bad" />Not connected. {{ overview.mqtt.error }}</dd>
        </div>
      </dl>
      <p class="lede">The broker login comes from the container's environment variables, so it isn't editable here.</p>
    </section>

    <section class="card">
      <h2>Pictures</h2>
      <p class="lede">
        The product photos belong to their makers and don't ship with this project. The server fetches each one once.
        Where that fails, or with <code>PHOTOS=off</code>, the page uses a drawn diagram.
      </p>
      <ul class="photos">
        <li v-for="(p, key) in overview.photos" :key="key">
          <b>{{ key }}</b>
          <span v-if="p.photo">photo, {{ p.credit }}</span>
          <span v-else>drawn diagram<template v-if="p.error">. Fetch failed: {{ p.error }}</template></span>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.settings { display: flex; flex-direction: column; gap: 16px; max-width: 980px; }
h2 { font-size: 16px; font-weight: 700; }
.lede { font-size: 13px; color: var(--ink-2); margin-top: 4px; max-width: 70ch; }
.empty { margin-top: 12px; font-size: 14px; }
.scroll { overflow-x: auto; margin-top: 12px; }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; white-space: nowrap; }
th, td { text-align: left; padding: 8px 12px 8px 0; border-top: 1px solid var(--grid); }
th { border-top: 0; color: var(--muted); font-weight: 600; font-size: 12px; }
a { color: var(--series-1); font-weight: 600; }
.dim { color: var(--muted); }
input, select {
  font: inherit;
  padding: 5px 8px;
  border: 1px solid var(--axis);
  border-radius: 8px;
  background: var(--white);
  color: var(--ink);
}
input[type='text'] { width: 170px; }
input[type='number'] { width: 86px; font-variant-numeric: tabular-nums; }
.field { display: inline-flex; align-items: center; gap: 6px; color: var(--ink-2); }
.row { display: flex; gap: 18px; align-items: flex-end; flex-wrap: wrap; margin-top: 14px; }
.row label { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px; color: var(--ink-2); }
.primary { background: var(--series-1); color: #fff; border: 1px solid var(--series-1); border-radius: 8px; padding: 6px 14px; font-size: 13.5px; font-weight: 600; cursor: pointer; }
.primary:disabled { opacity: 0.5; }
button:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid var(--series-1); outline-offset: 2px; }
dl { margin: 12px 0 0; display: grid; gap: 8px; }
dl div { display: grid; grid-template-columns: 110px 1fr; gap: 12px; font-size: 13.5px; }
dt { color: var(--muted); }
dd { margin: 0; }
code { font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; font-size: 12.5px; background: #f0efec; border-radius: 4px; padding: 1px 5px; }
.lamp { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 7px; }
.lamp.ok { background: var(--good); }
.lamp.bad { background: var(--critical); transform: rotate(45deg); border-radius: 0; }
.photos { list-style: none; margin: 12px 0 0; padding: 0; display: grid; gap: 6px; font-size: 13.5px; }
.photos b { display: inline-block; width: 110px; font-weight: 600; }
.msg { background: #e9f6e9; border: 1px solid var(--good); border-radius: 10px; padding: 9px 14px; font-size: 13.5px; }
.msg.bad { background: #fbeaea; border-color: var(--critical); }
</style>
