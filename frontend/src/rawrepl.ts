/**
 * MicroPython raw REPL over the browser's Web Serial API: run code and copy
 * files to a board. Web Serial needs Chrome or Edge on a desktop, and a page
 * served over HTTPS or from localhost.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
const enc = new TextEncoder()
const dec = new TextDecoder()

export const serialSupported = () => typeof navigator !== 'undefined' && 'serial' in navigator

export class RawRepl {
  private port: any
  private reader: any
  private writer: any
  private buf = ''
  private waiter: (() => void) | null = null
  private closed = false
  private readError: unknown = null

  /** Asks the user to pick the board's serial port. Must run from a click. */
  static async connect(): Promise<RawRepl> {
    const port = await (navigator as any).serial.requestPort({ filters: [{ usbVendorId: 0x2e8a }] })
    await port.open({ baudRate: 115200 })
    const r = new RawRepl()
    r.port = port
    r.reader = port.readable.getReader()
    r.writer = port.writable.getWriter()
    r.pump()
    return r
  }

  private async pump() {
    try {
      for (;;) {
        const { value, done } = await this.reader.read()
        if (done) break
        this.buf += dec.decode(value, { stream: true })
        this.waiter?.()
      }
    } catch (e) {
      this.readError = e
    } finally {
      this.closed = true
      this.waiter?.()
    }
  }

  private async send(text: string) {
    const bytes = enc.encode(text)
    // The raw REPL has no flow control, so feed it in small pieces.
    for (let i = 0; i < bytes.length; i += 128) {
      await this.writer.write(bytes.subarray(i, i + 128))
      await new Promise((r) => setTimeout(r, 6))
    }
  }

  /** Waits for `marker`, returns what came before it and drops both from the buffer. */
  private async until(marker: string, timeoutMs: number, onText?: (t: string) => void): Promise<string> {
    const deadline = Date.now() + timeoutMs
    let shown = 0
    for (;;) {
      const at = this.buf.indexOf(marker)
      const upto = at >= 0 ? at : Math.max(shown, this.buf.length - marker.length + 1)
      if (onText && upto > shown) {
        onText(this.buf.slice(shown, upto))
        shown = upto
      }
      if (at >= 0) {
        const head = this.buf.slice(0, at)
        this.buf = this.buf.slice(at + marker.length)
        return head
      }
      if (this.closed) throw new Error(`The board disconnected. ${this.readError ?? ''}`.trim())
      const left = deadline - Date.now()
      if (left <= 0) throw new Error('The board did not answer in time.')
      await new Promise<void>((resolve) => {
        const t = setTimeout(resolve, Math.min(left, 250))
        this.waiter = () => { clearTimeout(t); resolve() }
      })
      this.waiter = null
    }
  }

  /** Stops whatever is running and switches to the raw REPL. */
  async enter() {
    await this.send('\r\x03\x03')
    await new Promise((r) => setTimeout(r, 300))
    this.buf = ''
    await this.send('\x01')
    await this.until('raw REPL; CTRL-B to exit\r\n>', 4000)
  }

  /** Runs code and returns what it printed. Throws with the traceback if it raised. */
  async exec(code: string, timeoutMs = 10000, onText?: (t: string) => void): Promise<string> {
    await this.send(code)
    await this.send('\x04')
    await this.until('OK', 4000)
    const out = await this.until('\x04', timeoutMs, onText)
    const err = await this.until('\x04', 4000)
    await this.until('>', 4000)
    if (err.trim()) throw new Error(err.trim())
    return out
  }

  async mkdir(path: string) {
    await this.exec(`import os\ntry:\n os.mkdir(${JSON.stringify(path)})\nexcept OSError:\n pass\n`)
  }

  async writeFile(path: string, data: Uint8Array, onProgress?: (done: number, total: number) => void) {
    await this.exec(`import ubinascii\nf=open(${JSON.stringify(path)},'wb')\nw=f.write\n`)
    const step = 192
    for (let i = 0; i < data.length; i += step) {
      let bin = ''
      for (const b of data.subarray(i, i + step)) bin += String.fromCharCode(b)
      await this.exec(`w(ubinascii.a2b_base64('${btoa(bin)}'))\n`)
      onProgress?.(Math.min(i + step, data.length), data.length)
    }
    await this.exec('f.close()\n')
  }

  /** Leaves the raw REPL and restarts the board's program. */
  async reset() {
    await this.send('\x02')
    await new Promise((r) => setTimeout(r, 100))
    await this.send('\x04')
  }

  async close() {
    try { await this.reader.cancel() } catch { /* already gone */ }
    try { this.writer.releaseLock() } catch { /* already gone */ }
    try { await this.port.close() } catch { /* already gone */ }
  }
}
