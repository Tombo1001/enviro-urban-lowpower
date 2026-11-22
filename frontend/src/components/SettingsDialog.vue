<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  title: string
  unit: string
  options: number[]
  value: number
  custom?: { min: number; max: number }
  save: (v: number) => Promise<void>
}>()

const dlg = ref<HTMLDialogElement>()
const picked = ref(props.value)
const busy = ref(false)
const error = ref<string | null>(null)

function open() {
  picked.value = props.value
  error.value = null
  dlg.value?.showModal()
}
defineExpose({ open })

const valid = () =>
  Number.isFinite(picked.value) &&
  (props.custom ? picked.value >= props.custom.min && picked.value <= props.custom.max : props.options.includes(picked.value))

async function submit() {
  if (!valid()) {
    error.value = props.custom ? `Enter a value from ${props.custom.min} to ${props.custom.max}.` : 'Pick one of the options.'
    return
  }
  busy.value = true
  error.value = null
  try {
    await props.save(picked.value)
    dlg.value?.close()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <dialog ref="dlg" @click="($event.target === dlg) && dlg?.close()">
    <form method="dialog" @submit.prevent="submit">
      <h3>{{ title }}</h3>
      <div class="chips" role="group" :aria-label="title">
        <button v-for="o in options" :key="o" type="button" :class="{ on: picked === o }" @click="picked = o">
          {{ o }} {{ unit }}
        </button>
      </div>
      <label v-if="custom" class="custom">
        Or type a value
        <span><input v-model.number="picked" type="number" :min="custom.min" :max="custom.max" step="10" inputmode="numeric" /> {{ unit }}</span>
      </label>
      <div class="note"><slot /></div>
      <p v-if="error" class="error">{{ error }}</p>
      <div class="actions">
        <button type="button" class="ghost" @click="dlg?.close()">Cancel</button>
        <button type="submit" class="primary" :disabled="busy">{{ busy ? 'Saving…' : 'Save' }}</button>
      </div>
    </form>
  </dialog>
</template>

<style scoped>
dialog {
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 0;
  width: min(380px, calc(100vw - 32px));
  background: var(--surface);
  color: var(--ink);
  box-shadow: 0 18px 50px rgba(11, 11, 11, 0.22);
}
dialog::backdrop { background: rgba(11, 11, 11, 0.35); }
form { padding: 18px; display: flex; flex-direction: column; gap: 12px; }
h3 { font-size: 16px; font-weight: 700; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chips button {
  border: 1px solid var(--border);
  background: var(--white);
  border-radius: 999px;
  padding: 5px 12px;
  font-size: 13px;
  cursor: pointer;
}
.chips button.on { background: var(--ink); color: #fff; border-color: var(--ink); }
.custom { display: flex; justify-content: space-between; align-items: center; gap: 10px; font-size: 13px; color: var(--ink-2); }
input {
  width: 96px;
  font: inherit;
  padding: 5px 8px;
  border: 1px solid var(--axis);
  border-radius: 8px;
  background: var(--white);
  font-variant-numeric: tabular-nums;
}
.note { font-size: 12.5px; color: var(--ink-2); }
.error { font-size: 12.5px; color: var(--critical); }
.actions { display: flex; justify-content: flex-end; gap: 8px; }
.actions button { border-radius: 8px; padding: 7px 14px; font-size: 13.5px; font-weight: 600; cursor: pointer; }
.ghost { background: var(--white); border: 1px solid var(--border); }
.primary { background: var(--series-1); color: #fff; border: 1px solid var(--series-1); }
.primary:disabled { opacity: 0.5; }
button:focus-visible, input:focus-visible { outline: 2px solid var(--series-1); outline-offset: 2px; }
</style>
