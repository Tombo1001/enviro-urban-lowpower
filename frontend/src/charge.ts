import type { Battery } from './types'

export interface ChargeState {
  soc: number
  hours: number
  text: { value: string; unit: string }
  at: number
  late: boolean
  since: number
  from: number
  full: boolean
}

/**
 * Charge counted in since the voltage stepped up, at the charger's set rate.
 * The board can't measure charge current, so this is a count, not a reading.
 */
export function chargeState(b: Battery, rateMa: number, now: number): ChargeState | null {
  const c = b.charging
  if (!c) return null
  const net = Math.max(1, rateMa - c.board_ma)
  const elapsed = Math.max(0, (now - c.since_ts) / 3600)
  const counted = c.soc_start + ((net * elapsed) / b.capacity_mah) * 100
  const tail = rateMa >= 500 ? 1 : 0.5
  const soc = c.full ? Math.max(97, Math.min(99, counted)) : Math.min(96, counted)
  const hours = c.full ? tail / 2 : (((100 - soc) / 100) * b.capacity_mah) / net + tail
  const text =
    hours < 1 ? { value: (hours * 60).toFixed(0), unit: 'minutes' } : { value: hours.toFixed(1), unit: 'hours' }
  return { soc, hours, text, at: now + hours * 3600, late: counted > 100 && !c.full, since: c.since_ts, from: c.soc_start, full: c.full }
}
