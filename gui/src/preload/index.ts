import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'
import type { EngineApi } from '../shared/engine'

// Only these calls reach the main process. The renderer gets no general ipcRenderer access, and
// never the engine's token or connection file (D-32).
function subscribe<T>(channel: string, listener: (value: T) => void): () => void {
  const handler = (_event: IpcRendererEvent, value: T): void => listener(value)
  ipcRenderer.on(channel, handler)
  return () => {
    ipcRenderer.removeListener(channel, handler)
  }
}

const engine: EngineApi = {
  snapshot: () => ipcRenderer.invoke('engine:snapshot'),
  command: (name) => ipcRenderer.invoke('engine:command', name),
  onStatus: (listener) => subscribe('engine:status', listener),
  onState: (listener) => subscribe('engine:state', listener),
  onLogs: (listener) => subscribe('engine:logs', listener)
}

contextBridge.exposeInMainWorld('api', { engine })
