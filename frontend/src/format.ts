export const dash = '–'

export function fix(v: number | null | undefined, digits = 1): string {
  return v == null || !Number.isFinite(v) ? dash : v.toFixed(digits).replace('-', '−')
}

export function ago(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s} s ago`
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min ago`
  return `${Math.floor(s / 86400)} d ago`
}

export function span(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s} s`
  if (s < 3600) return `${Math.floor(s / 60)} min`
  if (s < 86400) return `${(s / 3600).toFixed(1)} h`
  return `${(s / 86400).toFixed(1)} days`
}

/** Battery life. Hours under two days, then days. */
export function life(days: number): { value: string; unit: string } {
  if (!Number.isFinite(days)) return { value: dash, unit: '' }
  if (days < 2) return { value: (days * 24).toFixed(days * 24 < 10 ? 1 : 0), unit: 'hours' }
  if (days < 20) return { value: days.toFixed(1), unit: 'days' }
  return { value: days.toFixed(0), unit: 'days' }
}

export function lifeRange(short: number, long: number): { value: string; unit: string } {
  const useHours = long < 2
  const f = (d: number) => {
    const v = useHours ? d * 24 : d
    return v < 10 ? v.toFixed(1) : v.toFixed(0)
  }
  return { value: `${f(short)} to ${f(long)}`, unit: useHours ? 'hours' : 'days' }
}

const timeFmt = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit' })
const secFmt = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
const dayFmt = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' })
const fullFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

export const clock = (ts: number) => timeFmt.format(ts * 1000)
export const clockSec = (ts: number) => secFmt.format(ts * 1000)
export const day = (ts: number) => dayFmt.format(ts * 1000)
export const dateTime = (ts: number) => fullFmt.format(ts * 1000)
