// The confirmation page (D-53). The main process lays it over the main window, sends it what to
// ask, and sends the command only on its answer. The page has no other call.

// Bundled with the app, like the main page's fonts (main.tsx).
import 'pretendard/dist/web/variable/pretendardvariable.css'
import './assets/main.css'
import './assets/confirm.css'

import type { ConfirmRequest } from '../../shared/confirm'

// Clicks this soon after a confirmation shows are ignored, so the second click of a double click
// on the button that asked can't land on the confirm button.
const CLICK_GUARD_MS = 400

const root = document.getElementById('root')!
let shown: { id: number; at: number; buttons: HTMLButtonElement[] } | null = null

function show(request: ConfirmRequest): void {
  const message = element('h2', 'confirm-message', request.message)
  message.id = 'confirm-message'
  const detail = element('p', 'confirm-detail', request.detail)
  detail.id = 'confirm-detail'
  const confirm = element('button', request.danger ? 'danger' : 'primary', request.button)
  const cancel = element('button', '', '취소')
  confirm.addEventListener('click', (event) => onClick(event, true))
  cancel.addEventListener('click', (event) => onClick(event, false))
  const buttons = element('div', 'confirm-buttons')
  buttons.append(confirm, cancel)
  const card = element('div', 'confirm-card')
  card.setAttribute('role', 'alertdialog')
  card.setAttribute('aria-modal', 'true')
  card.setAttribute('aria-labelledby', message.id)
  card.setAttribute('aria-describedby', detail.id)
  card.append(message, detail, buttons)
  const backdrop = element('div', 'confirm-backdrop')
  backdrop.append(card)
  root.replaceChildren(backdrop)
  shown = { id: request.id, at: performance.now(), buttons: [confirm, cancel] }
  cancel.focus() // the default, as in the native dialog this replaced
}

function onClick(event: MouseEvent, confirmed: boolean): void {
  // A click made with Enter or Space has detail 0 and isn't guarded.
  if (!shown || (event.detail > 0 && performance.now() - shown.at < CLICK_GUARD_MS)) return
  answer(confirmed)
}

function answer(confirmed: boolean): void {
  if (!shown) return
  const { id } = shown
  shown = null
  // The main process hides the page once this empty frame has painted.
  root.replaceChildren()
  window.confirmApi.answer(id, confirmed)
}

document.addEventListener('keydown', (event) => {
  if (!shown) return
  if (event.repeat) {
    // An Enter held down on the button that asked must not answer this.
    event.preventDefault()
  } else if (event.key === 'Escape') {
    event.preventDefault()
    answer(false)
  } else if (['Tab', 'ArrowLeft', 'ArrowRight'].includes(event.key)) {
    // Focus moves between the two buttons only.
    event.preventDefault()
    const { buttons } = shown
    const back = event.key === 'ArrowLeft' || (event.key === 'Tab' && event.shiftKey)
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement)
    const next =
      at < 0 ? buttons.length - 1 : (at + (back ? buttons.length - 1 : 1)) % buttons.length
    buttons[next].focus()
  }
})

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className: string,
  text?: string
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

window.confirmApi.onShow(show)
