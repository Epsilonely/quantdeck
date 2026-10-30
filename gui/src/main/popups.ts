// The alarm popups (GitHub issue #42): small windows stacked in the bottom-right corner of the
// primary screen, newest at the bottom, drawn by a page of their own instead of Windows'
// notifications, whose look can't be changed. They never take the focus, so a popup that shows
// while the user types elsewhere doesn't take the keys. Each page runs sandboxed with a preload
// that can only read its popup and say it was clicked or is done.

import { BrowserWindow, screen } from 'electron'
import { join } from 'path'
import { is } from '@electron-toolkit/utils'
import type { Popup } from '../shared/popup'
import { themeRaised } from './theme'

const WIDTH = 360
const HEIGHT = 96
const GAP = 8
const MARGIN = 12 // from the screen's work area, which leaves out the taskbar
const MAX_SHOWN = 4 // a fifth closes the oldest

interface Shown {
  window: BrowserWindow
  popup: Popup
  key: string | null
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
      webPreferences: { preload: join(__dirname, '../preload/popup.js'), sandbox: true }
    })
    const entry: Shown = { window, popup, key }
    this.shown.push(entry)
    this.place()

    window.once('ready-to-show', () => window.showInactive())
    window.on('closed', () => this.dismiss(entry))
    const contents = window.webContents
    contents.setWindowOpenHandler(() => ({ action: 'deny' }))
    contents.on('will-navigate', (event) => event.preventDefault())
    // A page that can't show its popup would stay as an empty box: close it instead.
    contents.on('did-fail-load', () => this.dismiss(entry))
    contents.on('render-process-gone', () => this.dismiss(entry))
    contents.ipc.handle('popup:get', () => entry.popup)
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

  private dismiss(entry: Shown): void {
    const at = this.shown.indexOf(entry)
    if (at < 0) return
    this.shown.splice(at, 1)
    if (!entry.window.isDestroyed()) entry.window.close()
    this.place()
  }

  private place(): void {
    const area = screen.getPrimaryDisplay().workArea
    const x = area.x + area.width - MARGIN - WIDTH
    let y = area.y + area.height - MARGIN - HEIGHT
    for (let i = this.shown.length - 1; i >= 0; i--) {
      const { window } = this.shown[i]
      if (!window.isDestroyed()) window.setBounds({ x, y, width: WIDTH, height: HEIGHT })
      y -= HEIGHT + GAP
    }
  }
}
