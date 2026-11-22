<script setup lang="ts">
defineProps<{ label: string }>()
</script>

<template>
  <button class="cog" type="button" :aria-label="label" :title="label">
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <g fill="none" stroke="currentColor">
        <circle cx="12" cy="12" r="8.6" stroke-width="3.2" stroke-dasharray="3.38 3.38" />
        <circle cx="12" cy="12" r="6" stroke-width="2.2" />
        <circle cx="12" cy="12" r="2.4" stroke-width="1.8" />
      </g>
    </svg>
  </button>
</template>

<style scoped>
.cog {
  display: inline-grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--white);
  color: var(--ink-2);
  cursor: pointer;
  padding: 0;
}
.cog:hover { color: var(--ink); border-color: var(--axis); }
.cog:focus-visible { outline: 2px solid var(--series-1); outline-offset: 2px; }
</style>
