export interface Triple { mid: number; lo: number; hi: number }

export interface Battery {
  capacity_mah: number
  empty_v: number
  low_v: number
  soc_empty: number
  v: number
  v_last: number
  v_ts: number
  soc: number
  remaining_mah: number
  low: boolean
  sag_mv_latest: number | null
  sag_mv_median: number | null
  run: { start_ts: number; hours: number; n: number; v_start: number; mv_per_h: number | null; after_charge: boolean }
  charging: { since_ts: number; soc_start: number; v_start: number; full: boolean; board_ma: number } | null
  carried: boolean
  method: 'model' | 'measured'
  confidence: 'none' | 'low' | 'medium' | 'good'
  fit_note: string | null
  scale: Triple
  per_wake: { base_mah: number; pm_mah: number; sleep_mah_day: number }
  mah_day: Triple
  days: { mid: number; short: number; long: number }
}

export interface Latest {
  ts: number
  temperature?: number
  humidity?: number
  pressure?: number
  noise?: number
  rssi?: number
  battery_v?: number
  battery_v_load?: number
  usb?: number
  awake_ms?: number
  wake?: string
  reset_cause?: string
  fw?: string
}

export interface LatestPm { ts: number; pm1?: number; pm2_5?: number; pm10?: number }

export interface EventItem {
  ts: number
  severity: 'critical' | 'serious' | 'warning' | 'notice'
  kind: string
  text: string
  detail: string
}

export type Col = (number | null)[]

export interface Summary {
  now: number
  device: { id: string | null; fw: string | null }
  cadence: { interval_min: number; pm_every_n: number; source: 'reported' | 'assumed' }
  config: {
    ts: number
    wanted: { interval_min?: number; pm_every_n?: number }
    state: 'pending' | 'applied' | 'rejected'
    error: string | null
    wakes_since: number
  } | null
  settings: {
    name: string | null
    capacity_mah: number
    charge_ma: number
    /** Wake interval to switch to while a charge is detected. 0 is off. */
    charge_interval_min: number
    auto: {
      active: boolean
      started?: number
      ended?: number
      why?: string
      interval_min?: number
      restore?: { interval_min: number; pm_every_n: number }
    } | null
  }
  latest: Latest | null
  latest_pm: LatestPm | null
  battery: Battery | null
  events: EventItem[]
  stats: { wakes: number; battery_wakes?: number; pm_wakes?: number; missed_gaps?: number; first_ts?: number; span_h?: number }
  series: {
    t: number[]; usb: number[]; battery_v: Col; battery_v_load: Col; temperature: Col
    humidity: Col; pressure: Col; noise: Col; rssi: Col; awake_s: Col
  } | null
  pm_series: { t: number[]; pm1: Col; pm2_5: Col; pm10: Col } | null
  recent: Latest[]
}

export interface BoardRow {
  id: string
  name: string | null
  fw: string | null
  last_ts: number | null
  interval_min: number
  pm_every_n: number
  usb: boolean
  battery_v: number | null
  soc: number | null
  charging: boolean
  wakes: number
  capacity_mah: number
  charge_ma: number
}

export interface Overview {
  now: number
  devices: BoardRow[]
  defaults: { capacity_mah: number; charge_ma: number }
  mqtt: {
    configured: boolean
    topic: string
    mode: 'subscribe' | 'file'
    host?: string
    port?: number
    connected?: boolean
    since?: number | null
    error?: string | null
    messages?: number
  }
  photos: Record<string, { photo: boolean; credit: string; error: string | null }>
  firmware: {
    available: boolean
    version: string | null
    support: string[]
    main: string
    template: string
    micropython_url: string
    flash_nuke_url: string
  }
}
