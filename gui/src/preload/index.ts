import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'
import type { EngineApi } from '../shared/engine'
import type { ThemeApi } from '../shared/theme'

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
  command: (command) => ipcRenderer.invoke('engine:command', command),
  startEngine: () => ipcRenderer.invoke('engine:start'),
  onStatus: (listener) => subscribe('engine:status', listener),
  onState: (listener) => subscribe('engine:state', listener),
  onLogs: (listener) => subscribe('engine:logs', listener),
  onLaunch: (listener) => subscribe('engine:launch', listener),
  onCatalog: (listener) => subscribe('engine:catalog', listener),
  onTrades: (listener) => subscribe('engine:trades', listener),
  onCandles: (listener) => subscribe('engine:candles', listener)
}

const theme: ThemeApi = {
  get: () => ipcRenderer.invoke('theme:get'),
  set: (source) => ipcRenderer.invoke('theme:set', source)
}

contextBridge.exposeInMainWorld('api', { engine, theme })
