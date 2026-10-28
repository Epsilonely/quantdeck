import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'
import type { LogApi } from '../shared/logs'

// The log window's only calls (D-55): it reads the engine's records and the connection, and can
// send nothing. It runs sandboxed, so this script imports nothing but electron.
function subscribe<T>(channel: string, listener: (value: T) => void): () => void {
  const handler = (_event: IpcRendererEvent, value: T): void => listener(value)
  ipcRenderer.on(channel, handler)
  return () => {
    ipcRenderer.removeListener(channel, handler)
  }
}

const logApi: LogApi = {
  snapshot: () => ipcRenderer.invoke('logs:snapshot'),
  onStatus: (listener) => subscribe('engine:status', listener),
  onLogs: (listener) => subscribe('engine:logs', listener)
}

contextBridge.exposeInMainWorld('logApi', logApi)
