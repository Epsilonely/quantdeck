import type { ConfirmApi } from '../shared/confirm'

declare global {
  interface Window {
    confirmApi: ConfirmApi // the confirmation page only (D-53)
  }
}
