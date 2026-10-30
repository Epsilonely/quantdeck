import type { PopupApi } from '../shared/popup'

declare global {
  interface Window {
    popupApi: PopupApi // the alarm popups only (#42)
  }
}
