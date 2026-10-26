// What the confirmation page shows and the API its preload script gives it (D-53). Types only,
// shared by main, preload, and renderer. The main process writes every confirmation (D-48); the
// page only shows it and answers.

export interface Confirmation {
  message: string
  detail: string // lines separated by \n; an empty line separates paragraphs
  button: string // the confirm button's label; the other button is always 취소
  danger: boolean // the command closes positions
}

export interface ConfirmRequest extends Confirmation {
  id: number // answered with the same id; the main process ignores any other
}

export interface ConfirmApi {
  onShow(listener: (request: ConfirmRequest) => void): void
  answer(id: number, confirmed: boolean): void
}
