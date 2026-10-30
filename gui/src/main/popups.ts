// The alarm popups (GitHub issue #42): small windows stacked in the bottom-right corner of the
// primary screen, newest at the bottom, drawn by a page of their own instead of Windows'
// notifications, whose look can't be changed. They never take the focus, so a popup that shows
// while the user types elsewhere doesn't take the keys. Each page runs sandboxed with a preload
// that can only read its popup, say it is drawn, and say it was clicked or is done.
//
// A window shows only once its page has drawn the popup (#43): shown earlier, it was an empty box
// until the page caught up. It then fades in as it rises from a little below its place, while the
// popups above glide up to make room, and they glide down again when one closes (#44). The windows
// move as a whole, since anything animated inside a page would run in a box already on screen.

import { BrowserWindow, screen, systemPreferences } from 'electron'
import { join } from 'path'
import { is } from '@electron-toolkit/utils'
import type { Popup } from '../shared/popup'
import { themeRaised } from './theme'

const WIDTH = 360
const HEIGHT = 96
const GAP = 8
const MARGIN = 12 // from the screen's work area, which leaves out the taskbar
const MAX_SHOWN = 4 // a fifth closes the oldest

const MOVE_MS = 240 // rising in, and gliding to a new place in the stack
const RISE = 40 // px below its place a new popup rises from
// Windows wakes timers on its 15.6 ms tick: a 16 ms interval waited two ticks, about 30 frames a
// second, while a shorter one runs on every tick (scratch app, 2026-10-30).
const FRAME_MS = 10
// Shown at opacity 0, the window waits this long before it rises, so the drawn page has reached
// the screen and no earlier frame of it shows.
const PAINT_DELAY_MS = 32
// A page that hasn't drawn its popup by then is closed instead of shown empty.
const DRAW_TIMEOUT_MS = 5000

interface Motion {
  fromY: number
  toY: number
  fromOpacity: number
  start: number // Date.now() ms; it holds still until then
}

interface Shown {
  window: BrowserWindow
  popup: Popup
  key: string | null
  revealed: boolean // drawn and on screen; only these take a place in the stack
  y: number // where the window is now
  opacity: number
  motion: Motion | null
  drawTimer: NodeJS.Timeout | undefined
}

export class PopupStack {
  private shown: Shown[] = [] // revealed oldest first, then the ones still being drawn
  private stopped = false
  private watchingScreen = false
  private ticker: NodeJS.Timeout | undefined

  /** `onOpen` brings up the main window when a popup is clicked. */
  constructor(private readonly onOpen: () => void) {}

  owns(window: BrowserWindow): boolean {
    return this.shown.some((entry) => entry.window === window)
  }

  /** Shows `popup`. A popup with the same `key` still showing is updated instead, with its count
   * raised, so a burst of the same alarm doesn't fill the screen. */
  show(popup: Popup, key: string | null = null): void {
    if (this.stopped) return
    const same = key === null ? undefined : this.shown.find((entry) => entry.key === key)
    if (same) {
      same.popup = { ...popup, count: same.popup.count + popup.count }
      same.window.webContents.send('popup:update', same.popup)
      return
    }
    while (this.shown.length >= MAX_SHOWN) this.dismiss(this.shown[0])
    if (!this.watchingScreen) {
      // Not in the constructor: the screen module can't be used before the app is ready.
      this.watchingScreen = true
      screen.on('display-metrics-changed', () => this.place())
    }

    const window = new BrowserWindow({
      width: WIDTH,
      height: HEIGHT,
      show: false,
      frame: false,
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      focusable: false, // on Windows this also keeps it off the taskbar
      skipTaskbar: true,
      alwaysOnTop: true,
      title: 'QuantDeck 알림',
      backgroundColor: themeRaised(),
      webPreferences: {
        preload: join(__dirname, '../preload/popup.js'),
        sandbox: true,
        backgroundThrottling: false // hidden until drawn, so its page must render while hidden
      }
    })
    const entry: Shown = {
      window,
      popup,
      key,
      revealed: false,
      y: 0,
      opacity: 1,
      motion: null,
      drawTimer: setTimeout(() => this.dismiss(entry), DRAW_TIMEOUT_MS)
    }
    this.shown.push(entry)
    this.place()

    let loaded = false
    let drawn = false
    const revealOnce = (): void => {
      if (loaded && drawn) this.reveal(entry)
    }
    window.once('ready-to-show', () => {
      loaded = true
      revealOnce()
    })
    window.on('closed', () => this.dismiss(entry))
    const contents = window.webContents
    contents.setWindowOpenHandler(() => ({ action: 'deny' }))
    contents.on('will-navigate', (event) => event.preventDefault())
    // A page that can't show its popup would stay as an empty box: close it instead.
    contents.on('did-fail-load', () => this.dismiss(entry))
    contents.on('render-process-gone', () => this.dismiss(entry))
    contents.ipc.handle('popup:get', () => entry.popup)
    contents.ipc.once('popup:ready', () => {
      drawn = true
      revealOnce()
    })
    contents.ipc.on('popup:open', () => {
      this.onOpen()
      this.dismiss(entry)
    })
    contents.ipc.on('popup:close', () => this.dismiss(entry))
    const url = process.env['ELECTRON_RENDERER_URL']
    if (is.dev && url) window.loadURL(`${url}/popup.html`)
    else window.loadFile(join(__dirname, '../renderer/popup.html'))
  }

  /** Closes every popup and shows no more: the app is quitting. */
  stop(): void {
    this.stopped = true
    for (const entry of [...this.shown]) this.dismiss(entry)
    clearInterval(this.ticker)
  }

  /** Shows a popup whose page has drawn it, as the newest: it takes the bottom place, rising into
   * it, or appears there at once when Windows is set to show fewer animations. */
  private reveal(entry: Shown): void {
    clearTimeout(entry.drawTimer)
    const { window } = entry
    if (window.isDestroyed()) return
    // The newest is the one shown last, whichever was asked for first.
    this.shown.splice(this.shown.indexOf(entry), 1)
    this.shown.splice(this.revealedCount(), 0, entry)
    entry.revealed = true
    const rise = !reducedMotion()
    const place = this.places().get(entry)!
    if (rise) {
      entry.y = place + RISE
      entry.opacity = 0
      window.setOpacity(0)
      this.setY(entry)
    }
    window.showInactive()
    if (rise) {
      // Timed from here: making the first window see-through took about 30 ms (scratch app,
      // 2026-10-30), which would otherwise use up the wait for the page to reach the screen.
      const start = Date.now() + PAINT_DELAY_MS
      entry.motion = { fromY: entry.y, toY: place, fromOpacity: 0, start }
    }
    this.place() // the others make room
  }

  private dismiss(entry: Shown): void {
    const at = this.shown.indexOf(entry)
    if (at < 0) return
    this.shown.splice(at, 1)
    clearTimeout(entry.drawTimer)
    if (!entry.window.isDestroyed()) entry.window.close()
    this.place()
  }

  private revealedCount(): number {
    return this.shown.filter((entry) => entry.revealed).length
  }

  /** Each popup's place: the revealed ones stacked up from the bottom, newest lowest; the ones
   * still being drawn, hidden, at the bottom place. */
  private places(): Map<Shown, number> {
    const area = screen.getPrimaryDisplay().workArea
    const bottom = area.y + area.height - MARGIN - HEIGHT
    const revealed = this.shown.filter((entry) => entry.revealed)
    const places = new Map<Shown, number>()
    revealed.forEach((entry, i) =>
      places.set(entry, bottom - (revealed.length - 1 - i) * (HEIGHT + GAP))
    )
    for (const entry of this.shown) if (!entry.revealed) places.set(entry, bottom)
    return places
  }

  /** Sends every popup to its place: gliding there if it is on screen, at once if it is hidden
   * or Windows is set to show fewer animations. */
  private place(): void {
    const reduced = reducedMotion()
    for (const [entry, place] of this.places()) {
      if (!entry.revealed || reduced) {
        entry.motion = null
        entry.y = place
        if (entry.opacity < 1 && entry.revealed) {
          entry.opacity = 1
          if (!entry.window.isDestroyed()) entry.window.setOpacity(1)
        }
        this.setY(entry)
      } else if ((entry.motion ? entry.motion.toY : entry.y) !== place) {
        entry.motion = { fromY: entry.y, toY: place, fromOpacity: entry.opacity, start: Date.now() }
      }
    }
    if (!this.ticker && this.shown.some((entry) => entry.motion)) {
      this.ticker = setInterval(() => this.step(), FRAME_MS)
    }
  }

  /** One frame of every motion, eased out so it slows into its place. */
  private step(): void {
    const now = Date.now()
    for (const entry of this.shown) {
      const { motion, window } = entry
      if (!motion) continue
      if (window.isDestroyed()) {
        entry.motion = null
        continue
      }
      const t = Math.min(1, Math.max(0, (now - motion.start) / MOVE_MS))
      const eased = 1 - (1 - t) ** 3
      entry.y = Math.round(motion.fromY + (motion.toY - motion.fromY) * eased)
      if (entry.opacity < 1) {
        entry.opacity = motion.fromOpacity + (1 - motion.fromOpacity) * eased
        window.setOpacity(entry.opacity)
      }
      this.setY(entry)
      if (t === 1) entry.motion = null
    }
    if (!this.shown.some((entry) => entry.motion)) {
      clearInterval(this.ticker)
      this.ticker = undefined
    }
  }

  private setY(entry: Shown): void {
    if (entry.window.isDestroyed()) return
    const area = screen.getPrimaryDisplay().workArea
    const x = area.x + area.width - MARGIN - WIDTH
    entry.window.setBounds({ x, y: entry.y, width: WIDTH, height: HEIGHT })
  }
}

function reducedMotion(): boolean {
  return systemPreferences.getAnimationSettings().prefersReducedMotion
}
