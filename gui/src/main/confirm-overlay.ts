// The confirmation every command goes through (D-48, D-53): a page of its own, laid over the main
// window's whole content area as a WebContentsView. Its answer arrives on that view's own IPC, so
// nothing the main window's renderer does can confirm a command, and the renderer can't draw
// over the page. The page loads with the window and stays hidden between confirmations, so it
// shows at once.

import { WebContentsView, type BrowserWindow } from 'electron'
import { join } from 'path'
import { is } from '@electron-toolkit/utils'
import type { Confirmation, ConfirmRequest } from '../shared/confirm'

/** 'busy': another confirmation is open. 'unavailable': the page couldn't be shown, or crashed or
 * reloaded while it was open. */
export type Answer = 'confirmed' | 'cancelled' | 'busy' | 'unavailable'

// How long the view stays up after an answer. The page clears itself as it answers, and the view
// hides only once that empty frame has painted: a view shown again can show its last painted
// frame first, which would flash the previous confirmation.
const HIDE_DELAY_MS = 100

export class ConfirmOverlay {
  private readonly view = new WebContentsView({
    webPreferences: { preload: join(__dirname, '../preload/confirm.js'), sandbox: true }
  })
  private loaded: Promise<boolean> = Promise.resolve(false)
  private pending: { id: number; shown: boolean; resolve: (answer: Answer) => void } | null = null
  private nextId = 1
  private hideTimer: NodeJS.Timeout | undefined
  private closed = false

  constructor(private readonly window: BrowserWindow) {
    const contents = this.view.webContents
    this.view.setBackgroundColor('#00000000') // the page dims the window behind it
    this.view.setVisible(false)
    window.contentView.addChildView(this.view)
    this.place()
    window.on('resize', () => this.place())
    window.on('closed', () => {
      this.closed = true
      clearTimeout(this.hideTimer)
      this.settle(this.pending?.id, 'cancelled')
      if (!contents.isDestroyed()) contents.close()
    })
    contents.setWindowOpenHandler(() => ({ action: 'deny' }))
    contents.on('will-navigate', (event) => event.preventDefault())
    contents.ipc.on('confirm:answer', (_event, value: unknown) => {
      if (isAnswer(value)) this.settle(value.id, value.confirmed ? 'confirmed' : 'cancelled')
    })
    // A page that crashed can't answer: nothing is sent, and it loads again for the next one.
    contents.on('render-process-gone', () => {
      this.settle(this.pending?.id, 'unavailable')
      if (!this.closed && !contents.isDestroyed()) this.load()
    })
    // A page that reloads (in dev, on every edit of it) has lost what it was showing.
    contents.on('did-start-loading', () => {
      if (this.pending?.shown) this.settle(this.pending.id, 'unavailable')
    })
    this.load()
  }

  /** Shows `confirmation` over the window and waits for the user's answer. One at a time. */
  async ask(confirmation: Confirmation): Promise<Answer> {
    if (this.pending) return 'busy'
    const id = this.nextId++
    let resolve!: (answer: Answer) => void
    const answered = new Promise<Answer>((r) => (resolve = r))
    this.pending = { id, shown: false, resolve }
    let ready = await this.loaded
    if (!ready && !this.closed) {
      this.load()
      ready = await this.loaded
    }
    if (!ready || this.closed) this.settle(id, 'unavailable')
    if (this.pending?.id !== id) return answered // settled while the page loaded
    clearTimeout(this.hideTimer)
    this.place()
    this.view.setVisible(true)
    const contents = this.view.webContents
    contents.focus()
    const request: ConfirmRequest = { id, ...confirmation }
    this.pending.shown = true
    contents.send('confirm:show', request)
    return answered
  }

  private settle(id: number | undefined, answer: Answer): void {
    if (!this.pending || this.pending.id !== id) return
    const { resolve } = this.pending
    this.pending = null
    resolve(answer)
    if (this.closed) return
    this.hideTimer = setTimeout(() => {
      this.view.setVisible(false)
      this.window.webContents.focus() // back to the button that asked
    }, HIDE_DELAY_MS)
  }

  private load(): void {
    const contents = this.view.webContents
    const url = process.env['ELECTRON_RENDERER_URL']
    const loading =
      is.dev && url
        ? contents.loadURL(`${url}/confirm.html`)
        : contents.loadFile(join(__dirname, '../renderer/confirm.html'))
    this.loaded = loading.then(
      () => true,
      (e) => {
        console.error(`[confirm] could not load the confirmation page: ${e}`)
        return false
      }
    )
  }

  private place(): void {
    const [width, height] = this.window.getContentSize()
    this.view.setBounds({ x: 0, y: 0, width, height })
  }
}

function isAnswer(value: unknown): value is { id: number; confirmed: boolean } {
  if (typeof value !== 'object' || value === null) return false
  const { id, confirmed } = value as Record<string, unknown>
  return typeof id === 'number' && typeof confirmed === 'boolean'
}
