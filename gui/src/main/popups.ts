// The alarm popups (GitHub issue #42): small windows stacked in the bottom-right corner of the
// primary screen, newest at the bottom, drawn by a page of their own instead of Windows'
// notifications, whose look can't be changed. They never take the focus, so a popup that shows
// while the user types elsewhere doesn't take the keys. Each page runs sandboxed with a preload
// that can only read its popup, say it is drawn, and say it was clicked or is done.
//
// A window shows only once its page has drawn the popup, and then fades in and slides from the
// right as a whole (#43): shown earlier, it was an empty box until the page caught up, and an
// entrance animated inside the page ran in a box already on screen.

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

const ENTER_MS = 200
const ENTER_SLIDE = 24 // px it slides in from
const FRAME_MS = 16
// Shown at opacity 0, the window waits this long before fading in, so the drawn page has reached
// the screen and no earlier frame of it shows.
const PAINT_DELAY_MS = 2 * FRAME_MS
// A page that hasn't drawn its popup by then is closed instead of shown empty.
const DRAW_TIMEOUT_MS = 5000

interface Shown {
  window: BrowserWindow
  popup: Popup
  key: string | null
  offset: number // px right of its place, while it slides in
  timer: NodeJS.Timeout | undefined // waiting for the page, then the entrance
}

export class PopupStack {
  private shown: Shown[] = [] // oldest first
  private stopped = false
  private watchingScreen = false

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
    const entry: Shown = { window, popup, key, offset: 0, timer: undefined }
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
    entry.timer = setTimeout(() => this.dismiss(entry), DRAW_TIMEOUT_MS)
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
  }

  /** Shows a popup whose page has drawn it: faded in and slid from the right, or at once when
   * Windows is set to show fewer animations. */
  private reveal(entry: Shown): void {
    clearTimeout(entry.timer)
    const { window } = entry
    if (window.isDestroyed()) return
    if (systemPreferences.getAnimationSettings().prefersReducedMotion) {
      window.showInactive()
      return
    }
    window.setOpacity(0)
    entry.offset = ENTER_SLIDE
    this.place(entry)
    window.showInactive()
    const start = Date.now() + PAINT_DELAY_MS
    entry.timer = setInterval(() => {
      if (window.isDestroyed()) {
        clearInterval(entry.timer)
        return
      }
      const t = Math.min(1, Math.max(0, (Date.now() - start) / ENTER_MS))
      const eased = 1 - (1 - t) ** 3
      window.setOpacity(eased)
      entry.offset = Math.round(ENTER_SLIDE * (1 - eased))
      this.place(entry)
      if (t === 1) clearInterval(entry.timer)
    }, FRAME_MS)
  }

  private dismiss(entry: Shown): void {
    const at = this.shown.indexOf(entry)
    if (at < 0) return
    this.shown.splice(at, 1)
    clearTimeout(entry.timer)
    if (!entry.window.isDestroyed()) entry.window.close()
    this.place()
  }

  /** Puts every popup, or only `only`, at its place in the stack. */
  private place(only?: Shown): void {
    const area = screen.getPrimaryDisplay().workArea
    const x = area.x + area.width - MARGIN - WIDTH
    let y = area.y + area.height - MARGIN - HEIGHT
    for (let i = this.shown.length - 1; i >= 0; i--) {
      const entry = this.shown[i]
      if ((!only || entry === only) && !entry.window.isDestroyed()) {
        entry.window.setBounds({ x: x + entry.offset, y, width: WIDTH, height: HEIGHT })
      }
      y -= HEIGHT + GAP
    }
  }
}
