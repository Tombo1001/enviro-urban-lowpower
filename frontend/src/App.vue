<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import DeviceView from './views/DeviceView.vue'
import ProvisionView from './views/ProvisionView.vue'
import SettingsView from './views/SettingsView.vue'
import type { BoardRow, Overview } from './types'

const overview = ref<Overview | null>(null)
const error = ref<string | null>(null)
const route = ref(location.hash.replace(/^#\/?/, ''))
const now = ref(Date.now() / 1000)
let skew = 0
let poll: number | undefined
let tick: number | undefined

async function load() {
  try {
    const r = await fetch('api/overview', { cache: 'no-store' })
    const j = await r.json()
    if (!r.ok) throw new Error(j.error ?? r.statusText)
    skew = j.now - Date.now() / 1000
    overview.value = j
    error.value = null
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}
const onHash = () => (route.value = location.hash.replace(/^#\/?/, ''))
onMounted(() => {
  load()
  poll = window.setInterval(load, 10_000)
  tick = window.setInterval(() => (now.value = Date.now() / 1000 + skew), 5000)
  window.addEventListener('hashchange', onHash)
})
onBeforeUnmount(() => {
  clearInterval(poll)
  clearInterval(tick)
  window.removeEventListener('hashchange', onHash)
})

const boards = computed(() => overview.value?.devices ?? [])
/** settings, provision, or a board id. With no hash, the first board, or Provision if there are none. */
const current = computed(() => {
  if (route.value === 'settings' || route.value === 'provision') return route.value
  const id = route.value.startsWith('board/') ? decodeURIComponent(route.value.slice(6)) : null
  if (id && boards.value.some((b) => b.id === id)) return id
  if (!overview.value) return null
  return boards.value[0]?.id ?? 'provision'
})
const boardHref = (id: string) => `#/board/${encodeURIComponent(id)}`

function state(b: BoardRow) {
  if (b.last_ts == null) return 'new'
  return now.value - b.last_ts > 1.5 * b.interval_min * 60 + 45 ? 'late' : 'ok'
}
const stateWord = { new: 'no wakes yet', late: 'overdue', ok: 'reporting' }

const credits = computed(() => {
  const p = overview.value?.photos
  if (!p) return ''
  const names = [...new Set(Object.values(p).filter((x) => x.photo).map((x) => x.credit))]
  return names.length ? `Photos: ${names.join(', ')}.` : ''
})
</script>

<template>
  <main>
    <header class="masthead">
      <h1>Enviro Urban</h1>
      <nav aria-label="Sections">
        <a href="#/settings" class="tab util" :class="{ on: current === 'settings' }" :aria-current="current === 'settings' ? 'page' : undefined">
          <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
            <g fill="none" stroke="currentColor">
              <circle cx="12" cy="12" r="8.6" stroke-width="3.2" stroke-dasharray="3.38 3.38" />
              <circle cx="12" cy="12" r="6" stroke-width="2.2" />
              <circle cx="12" cy="12" r="2.4" stroke-width="1.8" />
            </g>
          </svg>
          Settings
        </a>
        <span class="sep" />
        <a
          v-for="b in boards" :key="b.id" :href="boardHref(b.id)" class="tab"
          :class="{ on: current === b.id }" :aria-current="current === b.id ? 'page' : undefined"
          :title="`${b.id}, ${stateWord[state(b)]}${b.charging ? ', charging' : ''}`"
        >
          <i class="lamp" :class="state(b)" />
          {{ b.name || b.id }}
          <span v-if="b.charging" class="chg" aria-label="charging">charging</span>
        </a>
        <span v-if="overview && !boards.length" class="none">No boards seen yet</span>
        <span class="sep" />
        <a href="#/provision" class="tab util" :class="{ on: current === 'provision' }" :aria-current="current === 'provision' ? 'page' : undefined">
          <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
            <path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" />
          </svg>
          Provision
        </a>
      </nav>
    </header>

    <p v-if="error" class="banner">Can't reach the dashboard server. {{ error }}</p>

    <template v-if="overview">
      <SettingsView v-if="current === 'settings'" :overview="overview" :now="now" @changed="load" />
      <ProvisionView v-else-if="current === 'provision'" :overview="overview" />
      <DeviceView v-else-if="current" :id="current" :key="current" :credits="credits" />
    </template>
  </main>
</template>

<style scoped>
main { max-width: 1280px; margin: 0 auto; padding: 18px 16px 48px; display: flex; flex-direction: column; gap: 16px; }
.masthead { display: flex; align-items: center; gap: 22px; flex-wrap: wrap; border-bottom: 1px solid var(--grid); padding-bottom: 12px; }
h1 { font-size: 20px; font-weight: 800; letter-spacing: -0.02em; white-space: nowrap; }
nav { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; flex: 1; }
.tab {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 7px 13px;
  border-radius: 999px;
  border: 1px solid transparent;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink-2);
  text-decoration: none;
  white-space: nowrap;
}
.tab:hover { background: #efeee9; color: var(--ink); }
.tab.on { background: var(--ink); color: #fff; }
.tab.util { font-weight: 500; }
.tab:focus-visible { outline: 2px solid var(--series-1); outline-offset: 2px; }
.sep { width: 1px; height: 20px; background: var(--axis); margin: 0 6px; }
.lamp { width: 9px; height: 9px; border-radius: 50%; background: var(--axis); flex: none; }
.lamp.ok { background: var(--good); }
.lamp.late { background: var(--warning); border-radius: 2px; }
.chg { font-size: 11px; font-weight: 600; padding: 1px 7px; border-radius: 999px; background: rgba(12, 163, 12, 0.16); color: var(--good-ink); }
.tab.on .chg { background: rgba(255, 255, 255, 0.2); color: #fff; }
.none { font-size: 13px; color: var(--muted); padding: 0 8px; }
.banner { background: #fbeaea; border: 1px solid var(--critical); border-radius: 10px; padding: 10px 14px; font-size: 13.5px; }
@media (max-width: 720px) {
  .masthead { gap: 10px; }
  nav { flex-basis: 100%; overflow-x: auto; flex-wrap: nowrap; padding-bottom: 4px; }
}
</style>
