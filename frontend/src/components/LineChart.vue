<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { clock, dateTime, day, fix } from '../format'
import type { Col } from '../types'

export interface Series { name: string; color: string; values: Col; dots?: boolean }

const props = defineProps<{
  title: string
  unit: string
  digits: number
  t: number[]
  series: Series[]
  domain: [number, number]
  minSpan?: number
  refLines?: { value: number; label: string }[]
  note?: string
}>()

const H = 176
const M = { l: 46, r: 14, t: 10, b: 24 }
const wrap = ref<HTMLElement>()
const width = ref(480)
let ro: ResizeObserver | undefined
onMounted(() => {
  ro = new ResizeObserver((e) => (width.value = Math.max(240, e[0].contentRect.width)))
  if (wrap.value) ro.observe(wrap.value)
})
onBeforeUnmount(() => ro?.disconnect())

const idx = computed(() => {
  const out: number[] = []
  for (let i = 0; i < props.t.length; i++) {
    if (props.t[i] >= props.domain[0] && props.t[i] <= props.domain[1]) out.push(i)
  }
  return out
})

function niceStep(raw: number) {
  const p = Math.pow(10, Math.floor(Math.log10(raw)))
  const f = raw / p
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p
}

const yScale = computed(() => {
  let lo = Infinity
  let hi = -Infinity
  for (const s of props.series)
    for (const i of idx.value) {
      const v = s.values[i]
      if (v != null) {
        if (v < lo) lo = v
        if (v > hi) hi = v
      }
    }
  if (lo === Infinity) return null
  const min = props.minSpan ?? 0
  if (hi - lo < min) {
    const mid = (hi + lo) / 2
    lo = mid - min / 2
    hi = mid + min / 2
  }
  if (hi === lo) { hi += 1; lo -= 1 }
  const step = niceStep((hi - lo) / 3)
  lo = Math.floor(lo / step + 1e-9) * step
  hi = Math.ceil(hi / step - 1e-9) * step
  const ticks: number[] = []
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(v)
  return { lo, hi, ticks }
})

const x = (ts: number) => {
  const [a, b] = props.domain
  return M.l + ((ts - a) / Math.max(1, b - a)) * (width.value - M.l - M.r)
}
const y = (v: number) => {
  const s = yScale.value!
  return M.t + (1 - (v - s.lo) / (s.hi - s.lo)) * (H - M.t - M.b)
}

const xTicks = computed(() => {
  const [a, b] = props.domain
  const steps = [60, 300, 600, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400, 2 * 86400, 7 * 86400, 14 * 86400]
  const want = Math.max(2, Math.floor((width.value - M.l - M.r) / 90))
  const step = steps.find((s) => (b - a) / s <= want) ?? steps[steps.length - 1]
  const off = new Date(a * 1000).getTimezoneOffset() * 60
  const out: { ts: number; label: string }[] = []
  for (let ts = Math.ceil((a - off) / step) * step + off; ts <= b; ts += step) {
    out.push({ ts, label: step >= 86400 ? day(ts) : clock(ts) })
  }
  return out
})

const drawn = computed(() => {
  if (!yScale.value) return []
  return props.series.map((s) => {
    const pts: { cx: number; cy: number }[] = []
    let d = ''
    let pen = false
    for (const i of idx.value) {
      const v = s.values[i]
      if (v == null) { pen = false; continue }
      const cx = x(props.t[i])
      const cy = y(v)
      pts.push({ cx, cy })
      d += `${pen ? 'L' : 'M'}${cx.toFixed(1)},${cy.toFixed(1)}`
      pen = true
    }
    return { ...s, d: s.dots ? '' : d, pts: s.dots || pts.length <= 40 ? pts : [] }
  })
})

const latest = computed(() =>
  props.series.map((s) => {
    for (let k = idx.value.length - 1; k >= 0; k--) {
      const v = s.values[idx.value[k]]
      if (v != null) return v
    }
    return null
  }),
)

const hover = ref<number | null>(null)
function onMove(e: PointerEvent) {
  const rect = (e.currentTarget as SVGElement).getBoundingClientRect()
  const px = e.clientX - rect.left
  let best: number | null = null
  let dist = Infinity
  for (const i of idx.value) {
    if (!props.series.some((s) => s.values[i] != null)) continue
    const d = Math.abs(x(props.t[i]) - px)
    if (d < dist) { dist = d; best = i }
  }
  hover.value = best
}
const tip = computed(() => {
  const i = hover.value
  if (i == null || !yScale.value) return null
  const cx = x(props.t[i])
  return {
    cx,
    right: cx > width.value / 2,
    time: dateTime(props.t[i]),
    rows: props.series
      .map((s) => ({ name: s.name, color: s.color, v: s.values[i] }))
      .filter((r) => r.v != null),
  }
})
</script>

<template>
  <section class="chart card">
    <header>
      <h3>{{ title }}</h3>
      <div v-if="series.length === 1" class="now num">
        {{ fix(latest[0], digits) }}<span class="u">{{ unit }}</span>
      </div>
      <ul v-else class="legend">
        <li v-for="(s, k) in series" :key="s.name">
          <i :style="{ background: s.color }" :class="{ round: s.dots }" />
          {{ s.name }}
          <b class="num">{{ fix(latest[k], digits) }}</b>
        </li>
        <li class="u">{{ unit }}</li>
      </ul>
    </header>
    <div ref="wrap" class="plot">
      <svg v-if="yScale" :width="width" :height="H" @pointermove="onMove" @pointerleave="hover = null">
        <g v-for="v in yScale.ticks" :key="v">
          <line :x1="M.l" :x2="width - M.r" :y1="y(v)" :y2="y(v)" class="grid" />
          <text :x="M.l - 8" :y="y(v) + 4" text-anchor="end" class="tick">{{ v.toFixed(digits) }}</text>
        </g>
        <text v-for="tk in xTicks" :key="tk.ts" :x="x(tk.ts)" :y="H - 6" text-anchor="middle" class="tick">
          {{ tk.label }}
        </text>
        <template v-for="r in refLines ?? []" :key="r.label">
          <g v-if="r.value >= yScale.lo && r.value <= yScale.hi">
            <line :x1="M.l" :x2="width - M.r" :y1="y(r.value)" :y2="y(r.value)" class="ref" />
            <text :x="width - M.r" :y="y(r.value) - 5" text-anchor="end" class="reflabel">{{ r.label }}</text>
          </g>
        </template>
        <g v-for="s in drawn" :key="s.name">
          <path v-if="s.d" :d="s.d" :stroke="s.color" class="line" />
          <circle v-for="(p, k) in s.pts" :key="k" :cx="p.cx" :cy="p.cy" :r="s.dots ? 4 : 3" :fill="s.color" class="pt" />
        </g>
        <g v-if="tip">
          <line :x1="tip.cx" :x2="tip.cx" :y1="M.t" :y2="H - M.b" class="cross" />
          <circle v-for="r in tip.rows" :key="r.name" :cx="tip.cx" :cy="y(r.v as number)" r="4.5" :fill="r.color" class="pt" />
        </g>
      </svg>
      <p v-else class="empty">No readings in this range.</p>
      <div v-if="tip" class="tip" :class="{ right: tip.right }" :style="{ left: tip.cx + 'px' }">
        <div class="when">{{ tip.time }}</div>
        <div v-for="r in tip.rows" :key="r.name" class="row">
          <i :style="{ background: r.color }" />
          <span v-if="series.length > 1">{{ r.name }}</span>
          <b class="num">{{ fix(r.v, digits) }} {{ unit }}</b>
        </div>
      </div>
    </div>
    <p v-if="note" class="note">{{ note }}</p>
  </section>
</template>

<style scoped>
.chart { padding: 14px 14px 10px; min-width: 0; }
header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
h3 { font-size: 13px; font-weight: 650; }
.now { font-size: 20px; font-weight: 650; letter-spacing: -0.01em; }
.u { font-size: 12px; font-weight: 500; color: var(--ink-2); margin-left: 3px; }
.legend {
  display: flex;
  gap: 12px;
  list-style: none;
  margin: 0;
  padding: 0;
  font-size: 12px;
  color: var(--ink-2);
  align-items: center;
  flex-wrap: wrap;
}
.legend i, .tip i {
  display: inline-block;
  width: 10px;
  height: 3px;
  border-radius: 2px;
  margin-right: 5px;
  vertical-align: middle;
}
.legend i.round { width: 8px; height: 8px; border-radius: 50%; }
.legend b { color: var(--ink); margin-left: 3px; font-weight: 650; }
.plot { position: relative; min-height: 176px; }
svg { display: block; touch-action: pan-y; }
.grid { stroke: var(--grid); stroke-width: 1; }
.tick { font-size: 11px; fill: var(--muted); font-variant-numeric: tabular-nums; }
.line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.pt { stroke: var(--surface); stroke-width: 2; }
.cross { stroke: var(--axis); stroke-width: 1; }
.ref { stroke: var(--critical); stroke-width: 1; stroke-dasharray: 4 4; }
.reflabel { font-size: 11px; fill: var(--ink-2); }
.empty {
  height: 176px;
  display: grid;
  place-items: center;
  color: var(--muted);
  font-size: 13px;
}
.tip {
  position: absolute;
  top: 4px;
  transform: translateX(12px);
  background: var(--ink);
  color: #fff;
  border-radius: 8px;
  padding: 7px 10px;
  font-size: 12px;
  pointer-events: none;
  white-space: nowrap;
  z-index: 2;
}
.tip.right { transform: translateX(calc(-100% - 12px)); }
.tip .when { color: #c3c2b7; margin-bottom: 3px; }
.tip .row { display: flex; align-items: center; gap: 4px; }
.tip .row b { margin-left: auto; padding-left: 10px; font-weight: 650; }
.note { font-size: 12px; color: var(--muted); margin-top: 2px; }
</style>
