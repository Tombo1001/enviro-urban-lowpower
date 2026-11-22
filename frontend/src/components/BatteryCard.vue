<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { clock, dateTime, fix, life, lifeRange, span } from '../format'
import { chargeState } from '../charge'
import type { Battery, Summary } from '../types'
import BatteryMeter from './BatteryMeter.vue'
import CogButton from './CogButton.vue'
import SettingsDialog from './SettingsDialog.vue'

const props = defineProps<{
  deviceId: string
  battery: Battery
  cadence: Summary['cadence']
  config: Summary['config']
  settings: Summary['settings']
  saveSettings: (patch: Partial<Summary['settings']>) => Promise<void>
  nextDue: number | null
  onUsb: boolean
  now: number
}>()
const emit = defineEmits<{ sent: [] }>()
const b = computed(() => props.battery)
const measured = computed(() => b.value.method === 'measured')

const headline = computed(() =>
  measured.value ? life(b.value.days.mid) : lifeRange(b.value.days.short, b.value.days.long),
)
const rangeText = computed(() => {
  const r = lifeRange(b.value.days.short, b.value.days.long)
  return `${r.value} ${r.unit}`
})
// The countdown runs from the last battery reading, or from now while on USB.
const from = computed(() => (props.onUsb ? props.now : b.value.v_ts))
const emptyText = computed(() => {
  const at = (d: number) => dateTime(from.value + d * 86400)
  return measured.value
    ? `Reaches ${b.value.empty_v} V around ${at(b.value.days.mid)}`
    : `Reaches ${b.value.empty_v} V between ${at(b.value.days.short)} and ${at(b.value.days.long)}`
})

const rate = computed(() => props.settings.charge_ma)
const charge = computed(() => chargeState(b.value, rate.value, props.now))
const capacityDialog = ref<InstanceType<typeof SettingsDialog>>()
/** The cadence the board uses off charge. While the on-charge one runs, that is the one it returns to. */
const batt = computed(() => (props.settings.auto?.active && props.settings.auto.restore) || props.cadence)
const boosted = computed(() => !!props.settings.auto?.active)
function perDay(intervalMin: number, pmEvery: number) {
  const w = b.value.per_wake
  const n = 1440 / intervalMin
  const nPm = pmEvery ? n / pmEvery : 0
  return (n - nPm) * w.base_mah + nPm * w.pm_mah + w.sleep_mah_day
}
const fullCharge = computed(() => {
  const usable = (1 - b.value.soc_empty / 100) * b.value.capacity_mah
  const day = perDay(batt.value.interval_min, batt.value.pm_every_n)
  const s = b.value.scale
  return measured.value ? life(usable / (day * s.mid)) : lifeRange(usable / (day * s.hi), usable / (day * s.lo))
})

const basis = computed(() => {
  const r = b.value.run
  if (charge.value) {
    return (
      `The board can't measure charge current. This counts ${rate.value} mA in since the voltage stepped up at ` +
      `${clock(charge.value.since)}, from ${fix(charge.value.from, 0)}%. A wrong rate gives a wrong time, and the ` +
      `starting charge is only good to about ±8%.`
    )
  }
  if (b.value.carried) {
    return `Drain rate carried over from the run before the last charge. It re-fits after 2 hours and 20 readings on this one.`
  }
  const data = `${r.n} battery reading${r.n === 1 ? '' : 's'} over ${span(r.hours * 3600)}`
  if (measured.value) {
    return `Fitted to ${data}. Voltage is the only fuel gauge, so the range is never tighter than ±25%.`
  }
  return (
    b.value.fit_note ??
    `Only ${data} so far, too little to fit. The long end is the current model. The short end assumes ` +
      `drain ${fix(b.value.scale.hi, 1)} times the model, the worst seen in early testing. A fit takes over after 2 hours and 20 readings on battery.`
  )
})

const intervals = [1, 5, 15, 30, 60]
const pmOptions = [0, 1, 2, 4, 8, 12]
const interval = ref(batt.value.interval_min)
const every = ref(batt.value.pm_every_n)
// Follow the board only when its cadence really changes, not on every refresh.
watch(
  () => [batt.value.interval_min, batt.value.pm_every_n],
  ([i, n]) => { interval.value = i; every.value = n },
)

const pmText = (n: number) => (n === 0 ? 'PM off' : n === 1 ? 'PM every wake' : `PM every ${n} wakes`)
const sending = ref(false)
const sendError = ref<string | null>(null)
/** What this page sent, shown until the log catches up with it. */
const justSent = ref<{ interval_min: number; pm_every_n: number; at: number } | null>(null)

async function send() {
  sending.value = true
  sendError.value = null
  try {
    const body = { interval_min: interval.value, pm_every_n: every.value }
    const r = await fetch(`api/devices/${encodeURIComponent(props.deviceId)}/cadence`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const j = await r.json()
    if (!r.ok) throw new Error(j.error ?? r.statusText)
    // During a charge the server only stores it, so there is nothing to wait for.
    justSent.value = j.deferred ? null : { ...body, at: props.now }
    emit('sent')
  } catch (e) {
    sendError.value = e instanceof Error ? e.message : String(e)
  } finally {
    sending.value = false
  }
}

const pending = computed(() => {
  const c = props.config
  const fromLog = c && c.state === 'pending' ? { ...props.cadence, ...c.wanted, at: c.ts } : null
  // The log is the record. Fall back to this page's own send until the logger has written it.
  const mine = justSent.value && (!c || c.ts < justSent.value.at - 5) ? justSent.value : null
  const p = fromLog ?? mine
  if (!p) return null
  if (boosted.value) return null
  if (p.interval_min === props.cadence.interval_min && p.pm_every_n === props.cadence.pm_every_n) return null
  return p
})
const rejected = computed(() => (props.config?.state === 'rejected' ? props.config : null))

const chargeOptions = [0, 1, 5]
const autoBusy = ref(false)
const autoError = ref<string | null>(null)
async function setOnCharge(min: number) {
  autoBusy.value = true
  autoError.value = null
  try {
    await props.saveSettings({ charge_interval_min: min })
  } catch (e) {
    autoError.value = e instanceof Error ? e.message : String(e)
  } finally {
    autoBusy.value = false
  }
}
const auto = computed(() => props.settings.auto)

const whatIf = computed(() => {
  const w = b.value.per_wake
  const n = 1440 / interval.value
  const nPm = every.value ? n / every.value : 0
  const model = (n - nPm) * w.base_mah + nPm * w.pm_mah + w.sleep_mah_day
  const d = (scale: number) => b.value.remaining_mah / (model * scale)
  const full = (scale: number) => ((1 - b.value.soc_empty / 100) * b.value.capacity_mah) / (model * scale)
  const s = b.value.scale
  return {
    mah: model * s.mid,
    left: measured.value ? life(d(s.mid)) : lifeRange(d(s.hi), d(s.lo)),
    full: measured.value ? life(full(s.mid)) : lifeRange(full(s.hi), full(s.lo)),
    current: interval.value === batt.value.interval_min && every.value === batt.value.pm_every_n,
  }
})
</script>

<template>
  <section class="card battery">
    <template v-if="charge">
      <div class="eyebrow">Charging, time to full</div>
      <div class="hero">
        <span class="about">about</span>
        <span class="big num">{{ charge.text.value }}</span>
        <span class="unit">{{ charge.text.unit }}</span>
      </div>
      <p class="when">
        <template v-if="b.charging?.full">The cell is at {{ fix(b.v, 2) }} V and topping off.</template>
        <template v-else>Full around {{ dateTime(charge.at) }}.</template>
        Then {{ fullCharge.value }} {{ fullCharge.unit }} at every {{ batt.interval_min }} min.
      </p>
      <p v-if="charge.late" class="flag">
        <span class="mark usb" />By the count it should be full, but the cell is only at {{ fix(b.v, 2) }} V. The charger rate is probably lower than set.
      </p>
    </template>
    <template v-else>
    <div class="eyebrow">Battery life left</div>
    <div class="hero">
      <span v-if="measured" class="about">about</span>
      <span class="big num">{{ headline.value }}</span>
      <span class="unit">{{ headline.unit }}</span>
    </div>
    <p class="when">
      {{ emptyText }}<template v-if="measured">, likely range {{ rangeText }}</template>.
    </p>
    <p v-if="onUsb" class="flag">
      <span class="mark usb" />On USB now. The board has no charger, so USB neither drains nor charges the cell. These figures apply once it is unplugged.
    </p>
    <p v-if="b.low" class="flag">
      <span class="mark low" />Low voltage. The cell is under {{ b.low_v }} V.
    </p>
    </template>

    <div class="cell">
      <div class="cellhead">
        <span class="eyebrow">Battery, {{ fix(b.capacity_mah, 0) }} mAh</span>
        <CogButton label="Set battery capacity" @click="capacityDialog?.open()" />
      </div>
      <BatteryMeter :soc="charge ? charge.soc : b.soc" :charging="!!charge" />
      <div class="meterlabels">
        <span>{{ charge ? 'Charge counted in, not measured' : 'Charge read from voltage' }}</span>
        <span class="num">{{ fix(b.v, 3) }} V{{ charge ? ' on charge' : '' }}</span>
        <span v-if="!charge" class="num">{{ fix(b.remaining_mah, 0) }} mAh usable</span>
      </div>
    </div>
    <SettingsDialog
      ref="capacityDialog" title="Battery capacity" unit="mAh" :options="[500, 1200, 2000, 2500, 3000]"
      :value="b.capacity_mah" :custom="{ min: 100, max: 20000 }" :save="(v) => saveSettings({ capacity_mah: v })"
    >
      Capacity sets the mAh figures, the model estimate and the charge time. Once the drain is fitted to
      the voltage drop, days left come out the same whatever the capacity, because the fit works in percent per day.
    </SettingsDialog>

    <dl>
      <div>
        <dt>Drain at this cadence</dt>
        <dd class="num">
          <template v-if="measured">{{ fix(b.mah_day.mid, 0) }}</template>
          <template v-else>{{ fix(b.mah_day.lo, 0) }} to {{ fix(b.mah_day.hi, 0) }}</template>
          <small>mAh/day</small>
        </dd>
      </div>
      <div>
        <dt>Drain against model</dt>
        <dd class="num">
          <template v-if="measured">{{ fix(b.scale.mid, 2) }}×</template>
          <template v-else>not fitted</template>
        </dd>
      </div>
      <div>
        <dt>Fan sag</dt>
        <dd class="num">{{ fix(b.sag_mv_latest, 0) }}<small>mV</small></dd>
      </div>
      <div>
        <dt>Voltage trend</dt>
        <dd class="num">
          <template v-if="b.run.mv_per_h != null && b.run.hours >= 1">{{ fix(b.run.mv_per_h, 1) }}<small>mV/h</small></template>
          <template v-else>too early</template>
        </dd>
      </div>
    </dl>
    <p class="basis">{{ basis }}</p>

    <div class="whatif">
      <div class="eyebrow">Cadence</div>
      <div class="rowhead">
        <span class="lab wide">On battery</span>
        <span class="state" :class="boosted ? 'ready' : 'active'" role="status">
          <i />{{ boosted ? 'Paused while charging' : 'Active' }}
        </span>
      </div>
      <p v-if="pending" class="pending" role="status">
        <span class="spin" />
        <span>
          <b>Waiting for the board.</b> Sent {{ clock(pending.at) }}: every {{ pending.interval_min }} min,
          {{ pmText(pending.pm_every_n) }}. It is asleep and reads this at its next wake<template v-if="nextDue">,
          due about {{ clock(nextDue) }}</template>. Until then it stays on every {{ cadence.interval_min }} min.
        </span>
      </p>
      <p v-if="rejected" class="flag">
        <span class="mark low" />
        <span>The board rejected the change sent at {{ clock(rejected.ts) }}: {{ rejected.error }}</span>
      </p>
      <div class="chips" role="group" aria-label="Wake interval">
        <span class="lab">Wake every</span>
        <button v-for="i in intervals" :key="i" :class="{ on: interval === i }" @click="interval = i">{{ i }}</button>
        <span class="after">min</span>
      </div>
      <div class="chips" role="group" aria-label="Particulate reading frequency">
        <span class="lab">PM every</span>
        <button v-for="n in pmOptions" :key="n" :class="{ on: every === n }" @click="every = n">
          {{ n === 0 ? 'off' : n }}
        </button>
        <span class="after">wakes</span>
      </div>
      <p class="result">
        <template v-if="!charge"><b class="num">{{ whatIf.left.value }} {{ whatIf.left.unit }}</b> from here<span v-if="whatIf.current"> ({{ boosted ? 'what it returns to' : 'what the board runs now' }})</span>,{{ ' ' }}</template>
        <span class="num">{{ whatIf.full.value }} {{ whatIf.full.unit }}</span> on a full charge, at
        <span class="num">{{ fix(whatIf.mah, 0) }} mAh/day</span><template v-if="!measured"> by the model</template>.
      </p>
      <div v-if="!whatIf.current" class="actions">
        <button class="send" :disabled="sending || every === 0" @click="send">
          {{ sending ? 'Sending…' : boosted ? 'Save for after the charge' : 'Send to board' }}
        </button>
        <button class="reset" @click="interval = batt.interval_min; every = batt.pm_every_n">Reset</button>
        <span v-if="every === 0" class="hint">PM off is estimate only. To switch the sensor off for real, set PM_MIN_V = 99 in the board's config.py.</span>
        <span v-else class="hint">Picking a chip only updates the estimate. Nothing changes on the board until you send it.</span>
      </div>
      <p v-if="sendError" class="flag"><span class="mark low" /><span>Not sent. {{ sendError }}</span></p>

      <div class="chips onchg" role="group" aria-label="Wake interval while charging">
        <span class="lab">On charge</span>
        <button
          v-for="m in chargeOptions" :key="m" :class="{ on: settings.charge_interval_min === m }"
          :disabled="autoBusy" @click="setOnCharge(m)"
        >
          {{ m === 0 ? 'off' : m }}
        </button>
        <span class="after">min</span>
        <span class="state" :class="auto?.active ? 'active' : settings.charge_interval_min ? 'ready' : 'off'" role="status">
          <i />{{ auto?.active ? 'Active' : settings.charge_interval_min ? 'Standing by' : 'Off' }}
        </span>
      </div>
      <p class="hint autoline">
        <template v-if="auto?.active">
          Every {{ auto.interval_min }} min since {{ clock(auto.started ?? 0) }}. Returns to every
          {{ auto.restore?.interval_min }} min when the charge ends.
        </template>
        <template v-else-if="settings.charge_interval_min">
          Switches to every {{ settings.charge_interval_min }} min when a charge is detected, then back.
          <template v-if="auto?.why">Last ended {{ clock(auto.ended ?? 0) }}, {{ auto.why }}.</template>
        </template>
        <template v-else>The cadence stays the same on charge.</template>
      </p>
      <p v-if="autoError" class="flag"><span class="mark low" /><span>Not saved. {{ autoError }}</span></p>
    </div>
  </section>
</template>

<style scoped>
.battery { display: flex; flex-direction: column; gap: 10px; }
.hero { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; margin-top: 2px; }
.big { font-size: clamp(38px, 5vw, 52px); font-weight: 700; letter-spacing: -0.03em; line-height: 1; }
.unit { font-size: 18px; font-weight: 600; color: var(--ink-2); }
.about { font-size: 15px; color: var(--ink-2); }
.when { font-size: 13.5px; color: var(--ink-2); }
.flag { font-size: 13px; display: flex; gap: 8px; align-items: flex-start; }
.mark { flex: none; width: 10px; height: 10px; margin-top: 4px; }
.mark.usb { border-radius: 2px; background: var(--series-1); }
.mark.low { background: var(--critical); transform: rotate(45deg); }
.cell { display: flex; flex-direction: column; gap: 6px; margin-top: 4px; }
.cellhead { display: flex; justify-content: space-between; align-items: center; }
.meterlabels { display: flex; justify-content: space-between; font-size: 13px; color: var(--ink-2); gap: 8px; flex-wrap: wrap; }
.meterlabels b { color: var(--ink); font-weight: 650; }
dl { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; margin: 8px 0 0; }
dt { font-size: 12px; color: var(--muted); }
dd { margin: 1px 0 0; font-size: 17px; font-weight: 650; }
dd small { font-size: 12px; font-weight: 500; color: var(--ink-2); margin-left: 3px; }
.basis { font-size: 12.5px; color: var(--ink-2); }
.whatif { border-top: 1px solid var(--grid); padding-top: 14px; margin-top: 4px; display: flex; flex-direction: column; gap: 8px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.lab { font-size: 12.5px; color: var(--ink-2); width: 74px; }
.chips button {
  border: 1px solid var(--border);
  background: var(--white);
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12.5px;
  cursor: pointer;
}
.chips button:hover { border-color: var(--axis); }
.chips button.on { background: var(--ink); color: #fff; border-color: var(--ink); }
.chips button:focus-visible { outline: 2px solid var(--series-1); outline-offset: 2px; }
.result { font-size: 13.5px; color: var(--ink-2); }
.actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.send, .reset { border-radius: 8px; padding: 7px 14px; font-size: 13.5px; font-weight: 600; cursor: pointer; }
.send { background: var(--series-1); color: #fff; border: 1px solid var(--series-1); }
.send:disabled { opacity: 0.45; cursor: not-allowed; }
.reset { background: var(--white); border: 1px solid var(--border); }
.send:focus-visible, .reset:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
.hint { font-size: 12px; color: var(--muted); flex: 1 1 180px; }
.onchg { border-top: 1px solid var(--grid); padding-top: 12px; margin-top: 4px; }
.chips button:disabled { opacity: 0.5; }
.state { margin-left: auto; display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; font-weight: 650; color: var(--ink-2); }
.state i { width: 9px; height: 9px; border-radius: 50%; background: var(--axis); }
.state.ready i { background: none; border: 2px solid var(--muted); }
.state.active { color: var(--good-ink); }
.state.active i { background: var(--good); box-shadow: 0 0 0 3px rgba(12, 163, 12, 0.18); }
.autoline { flex: none; }
.rowhead { display: flex; align-items: center; }
.after { font-size: 12.5px; color: var(--ink-2); }
.whatif .chips button { min-width: 34px; padding: 4px 9px; }
.lab.wide { width: auto; font-weight: 650; color: var(--ink); }
.onchg .lab { font-weight: 650; color: var(--ink); }
.pending {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  font-size: 13px;
  background: #fdf3d7;
  border: 1px solid var(--warning);
  border-radius: 10px;
  padding: 10px 12px;
}
.pending b { display: block; }
.spin {
  flex: none;
  width: 14px;
  height: 14px;
  margin-top: 2px;
  border-radius: 50%;
  border: 2px solid #8a5a00;
  border-top-color: transparent;
  animation: spin 1.2s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spin { animation: none; } }
.result b { color: var(--ink); font-size: 15px; }
</style>

<style scoped>
/* Phones: the row label sits above its chips so every row stays on one line. */
@media (max-width: 480px) {
  .whatif .chips { gap: 5px; }
  .whatif .chips .lab { width: 100%; }
  .whatif .chips button { min-width: 32px; padding: 4px 7px; }
  .onchg .lab { width: auto !important; }
}
</style>
