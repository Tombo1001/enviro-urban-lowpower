<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ soc: number; charging: boolean }>()
const photo = 'img/battery' // the fetched photo, or the drawn cell
const pct = computed(() => Math.max(0, Math.min(100, props.soc)))
// Red under 20%, amber under 50%, green above.
const level = computed(() => (pct.value < 20 ? 'red' : pct.value < 50 ? 'amber' : 'green'))
const word = computed(() => ({ red: 'low', amber: 'fair', green: 'good' })[level.value])
</script>

<template>
  <div class="meter" role="img" :aria-label="`Battery ${pct.toFixed(0)} percent, ${word}${charging ? ', charging' : ''}`">
    <img :src="photo" alt="" />
    <!-- The cell body sits at 4.7% to 97.3% across and 2.7% to 77% down the photo. -->
    <div class="body">
      <div class="fill" :class="[level, { charging }]" :style="{ width: Math.max(2, pct) + '%' }" />
      <div class="edge" :style="{ left: Math.max(2, pct) + '%' }" />
    </div>
    <div class="read">
      <b class="num">{{ pct.toFixed(0) }}%</b>
      <span>{{ charging ? 'charging' : word }}</span>
    </div>
  </div>
</template>

<style scoped>
.meter { position: relative; width: 100%; max-width: 264px; margin: 0 auto; aspect-ratio: 849 / 660; }
img { position: absolute; inset: 0; width: 100%; height: 100%; mix-blend-mode: multiply; }
.body {
  position: absolute;
  left: 4.7%;
  right: 2.7%;
  top: 2.7%;
  bottom: 23%;
  border-radius: 4px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.45);
}
.fill { height: 100%; mix-blend-mode: multiply; transition: width 0.8s ease, background-color 0.4s; }
.fill.red { background: #e2504f; }
.fill.amber { background: #f5b02e; }
.fill.green { background: #35b535; }
.fill.charging { animation: breathe 2.4s ease-in-out infinite; }
@keyframes breathe { 50% { opacity: 0.65; } }
.edge { position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; background: rgba(11, 11, 11, 0.55); }
.read {
  position: absolute;
  right: 6%;
  top: 6%;
  display: flex;
  align-items: baseline;
  gap: 6px;
  background: var(--ink);
  color: #fff;
  border-radius: 999px;
  padding: 3px 11px 4px;
  font-size: 12px;
}
.read b { font-size: 17px; font-weight: 700; }
@media (prefers-reduced-motion: reduce) { .fill.charging { animation: none; } }
</style>
