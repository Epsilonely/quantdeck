import type { ConnectionStatus, LogRecord, LogsUpdate } from './engine'

// The records the main process keeps, and the log window shows.
export const LOG_CAPACITY = 1000

// The engine log's own window (D-55). Its page reads the records and the connection, which says
// why no new records arrive; it has no other call.
export interface LogSnapshot {
  status: ConnectionStatus
  logs: LogRecord[]
}

export interface LogApi {
  snapshot(): Promise<LogSnapshot>
  onStatus(listener: (status: ConnectionStatus) => void): () => void
  onLogs(listener: (update: LogsUpdate) => void): () => void
}

// Records at WARNING and above that arrived while the log window was closed or minimized.
export interface UnseenLogs {
  warnings: number
  errors: number // ERROR and CRITICAL
}

// What the main window has of the log window: its header button opens it and shows the count.
export interface LogWindowApi {
  open(): Promise<void>
  unseen(): Promise<UnseenLogs>
  onUnseen(listener: (unseen: UnseenLogs) => void): () => void
}
