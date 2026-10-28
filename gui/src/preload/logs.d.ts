import type { LogApi } from '../shared/logs'

declare global {
  interface Window {
    logApi: LogApi // the log window only (D-55)
  }
}
