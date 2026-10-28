// The engine log in a window of its own (D-55), opened from the main window's header. Its page
// runs sandboxed with a preload that can only read the records and the connection, so nothing in
// it can send a command. While the window is closed or minimized, the WARNING and higher records
// that arrive are counted for the badge on the main window's log button.

import { BrowserWindow, screen } from 'electron'
import { join } from 'path'
import { is } from '@electron-toolkit/utils'
import type { ConnectionStatus, LogsUpdate } from '../shared/engine'
import type { UnseenLogs } from '../shared/logs'
import { themeBackground } from './theme'

const ERROR_LEVELS = new Set(['ERROR', 'CRITICAL'])
const NONE_UNSEEN: UnseenLogs = { warnings: 0, errors: 0 }

export class LogWindow {
  private window: BrowserWindow | null = null
  private unseen = NONE_UNSEEN

  constructor(private readonly onUnseen: (unseen: UnseenLogs) => void) {}

  get counts(): UnseenLogs {
    return this.unseen
  }

  owns(window: BrowserWindow): boolean {
    return window === this.window
  }

  /** Opens the window, or brings it to the front if it is open already. */
  open(): void {
    this.clear()
    if (this.window) {
      if (this.window.isMinimized()) this.window.restore()
      this.window.focus()
      return
    }
    const area = screen.getPrimaryDisplay().workAreaSize
    const window = new BrowserWindow({
      width: Math.min(1000, area.width),
      height: Math.min(640, area.height),
      show: false,
      autoHideMenuBar: true,
      title: 'QuantDeck 엔진 로그',
      backgroundColor: themeBackground(),
      webPreferences: { preload: join(__dirname, '../preload/logs.js'), sandbox: true }
    })
    this.window = window
    window.on('ready-to-show', () => window.show())
    window.on('restore', () => this.clear())
    window.on('closed', () => {
      if (this.window === window) this.window = null
    })
    const contents = window.webContents
    contents.setWindowOpenHandler(() => ({ action: 'deny' }))
    contents.on('will-navigate', (event) => event.preventDefault())
    const url = process.env['ELECTRON_RENDERER_URL']
    if (is.dev && url) window.loadURL(`${url}/logs.html`)
    else window.loadFile(join(__dirname, '../renderer/logs.html'))
  }

  close(): void {
    this.window?.close()
  }

  status(status: ConnectionStatus): void {
    this.send('engine:status', status)
  }

  /** Passes the engine's records on, and counts the ones nobody can see. */
  logs(update: LogsUpdate): void {
    this.send('engine:logs', update)
    // A new connection's backlog repeats records from before it, so only new ones count.
    if (update.reset || (this.window && !this.window.isMinimized())) return
    let { warnings, errors } = this.unseen
    for (const record of update.records) {
      if (ERROR_LEVELS.has(record.level)) errors++
      else if (record.level === 'WARNING') warnings++
    }
    if (warnings === this.unseen.warnings && errors === this.unseen.errors) return
    this.unseen = { warnings, errors }
    this.onUnseen(this.unseen)
  }

  private clear(): void {
    if (this.unseen.warnings === 0 && this.unseen.errors === 0) return
    this.unseen = NONE_UNSEEN
    this.onUnseen(this.unseen)
  }

  private send(channel: string, value: unknown): void {
    if (this.window && !this.window.isDestroyed()) this.window.webContents.send(channel, value)
  }
}
