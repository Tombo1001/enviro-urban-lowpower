<script setup lang="ts">
import { computed } from 'vue'

export interface Callout {
  id: string
  tag: string
  /** Hotspot position on the photo, percent. */
  x: number
  y: number
  /** Label centre down the figure, percent. */
  ly: number
  value: string
  unit?: string
  sub?: string
  status?: 'warning' | 'critical'
  /** Which side this label sits on. Defaults to the figure's `labels` side. */
  side?: 'left' | 'right'
}

const props = withDefaults(
  defineProps<{
    src: string
    alt: string
    caption: string
    labels: 'left' | 'right'
    callouts: Callout[]
    pulse: number
    /** Left edge and width of the photo, percent of the figure. */
    imgLeft?: number
    imgWidth?: number
    /** Width over height, of the photo and of the whole figure. */
    imgAspect?: number
    aspect?: number
    dim?: boolean
  }>(),
  { imgWidth: 56, imgAspect: 700 / 1170, aspect: 1.068, dim: false },
)

const GAP = 5
const box = computed(() => {
  const left = props.imgLeft ?? (props.labels === 'left' ? 100 - props.imgWidth : 0)
  const height = Math.min(100, (props.imgWidth * props.aspect) / props.imgAspect)
  return { left, width: props.imgWidth, height, top: (100 - height) / 2 }
})

const placed = computed(() =>
  props.callouts.map((c) => {
    const side = c.side ?? props.labels
    const b = box.value
    const hx = b.left + (c.x / 100) * b.width
    const hy = b.top + (c.y / 100) * b.height
    const width = side === 'left' ? b.left - GAP : 100 - b.left - b.width - GAP
    const edge = side === 'left' ? width + 1 : 100 - width - 1
    const knee = side === 'left' ? width + 4 : 100 - width - 4
    return { c, side, hx, hy, width, points: `${edge},${c.ly} ${knee},${c.ly} ${hx},${hy}` }
  }),
)
</script>

<template>
  <figure class="board" :class="{ dim }">
    <div class="stage" :style="{ aspectRatio: String(aspect) }">
      <img
        :src="src" :alt="alt"
        :style="{ left: box.left + '%', width: box.width + '%', top: box.top + '%', height: box.height + '%' }"
      />
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <polyline v-for="p in placed" :key="p.c.id + 'c'" :points="p.points" class="casing" />
        <polyline v-for="p in placed" :key="p.c.id" :points="p.points" class="lead" />
      </svg>
      <span
        v-for="p in placed"
        :key="p.c.id + pulse"
        class="dot"
        :style="{ left: p.hx + '%', top: p.hy + '%' }"
      />
      <div
        v-for="p in placed"
        :key="p.c.id"
        class="label"
        :class="[p.c.status, p.side]"
        :style="{ top: p.c.ly + '%', width: p.width + '%' }"
      >
        <span class="pill">{{ p.c.tag }}</span>
        <div class="value">
          {{ p.c.value }}<span v-if="p.c.unit" class="unit">{{ p.c.unit }}</span>
        </div>
        <div v-if="p.c.sub" class="sub">{{ p.c.sub }}</div>
      </div>
    </div>
    <figcaption :style="{ marginLeft: box.left + '%', width: box.width + '%' }">{{ caption }}</figcaption>
  </figure>
</template>

<style scoped>
.board { margin: 0; width: 100%; }
.stage { position: relative; width: 100%; }
img { position: absolute; object-fit: contain; transition: filter 0.4s, opacity 0.4s; }
svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
}
polyline {
  fill: none;
  vector-effect: non-scaling-stroke;
  stroke-linejoin: round;
  stroke-linecap: round;
}
.casing { stroke: #fff; stroke-width: 4.5; opacity: 0.9; }
.lead { stroke: var(--ink); stroke-width: 1.5; }
.dot {
  position: absolute;
  width: 11px;
  height: 11px;
  margin: -5.5px 0 0 -5.5px;
  border-radius: 50%;
  background: var(--series-1);
  box-shadow: 0 0 0 2px #fff;
}
.dot::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 2px solid var(--series-1);
  animation: ping 1.8s ease-out 1;
  opacity: 0;
}
@keyframes ping {
  0% { transform: scale(1); opacity: 0.9; }
  100% { transform: scale(4); opacity: 0; }
}
.label {
  position: absolute;
  transform: translateY(-50%);
  line-height: 1.2;
}
.label.left { left: 0; text-align: right; }
.label.right { right: 0; text-align: left; }
.value {
  margin-top: 4px;
  font-size: clamp(16px, 2.1vw, 23px);
  font-weight: 650;
  letter-spacing: -0.01em;
  font-variant-numeric: tabular-nums;
}
.unit {
  margin-left: 3px;
  font-size: 0.62em;
  font-weight: 500;
  color: var(--ink-2);
}
.sub {
  margin-top: 1px;
  font-size: clamp(10.5px, 1.1vw, 12.5px);
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
  white-space: pre-line;
}
.label.warning .pill { background: #8a5a00; }
.label.critical .pill { background: var(--critical); }
figcaption {
  margin-top: 8px;
  text-align: center;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}
/* Idle: the photo and its markers go grey, the text stays readable. */
.dim img { filter: grayscale(1); opacity: 0.4; }
.dim .dot { background: var(--muted); }
.dim .dot::after { display: none; }
.dim .lead { stroke: var(--axis); }
.dim .pill { background: var(--muted); }
.dim .value { color: var(--muted); }
@media (prefers-reduced-motion: reduce) {
  .dot::after { animation: none; }
}
</style>
