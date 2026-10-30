// A popup in the corner of the screen (GitHub issue #42) and the API its preload script gives its
// page. Types only, shared by main, preload, and renderer. The main process writes every popup;
// the page only shows it, plays its sound, and says when it was clicked or is done.

export type PopupTone = 'info' | 'ok' | 'warn' | 'danger'

export type PopupIcon =
  'entry' | 'exit' | 'stop' | 'forced' | 'halt' | 'disconnect' | 'error' | 'loss' | 'price' | 'info'

export interface Popup {
  tone: PopupTone
  icon: PopupIcon
  title: string
  body: string // one or two short lines, separated by \n
  time: string // ISO 8601 UTC, when it happened
  count: number // how many times it came while shown: repeats update the popup instead of stacking
  seconds: number // how long it stays; 0 until it is closed
  sound: boolean
}

export interface PopupApi {
  popup(): Promise<Popup | null>
  onUpdate(listener: (popup: Popup) => void): () => void
  open(): void // clicked: bring up the main window
  close(): void // dismissed, or its time is up
}
