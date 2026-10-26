import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'
import type { ConfirmApi, ConfirmRequest } from '../shared/confirm'

// The confirmation page's only calls (D-53). It runs sandboxed, so this script imports nothing
// but electron.
const confirmApi: ConfirmApi = {
  onShow: (listener) => {
    ipcRenderer.on('confirm:show', (_event: IpcRendererEvent, request: ConfirmRequest) =>
      listener(request)
    )
  },
  answer: (id, confirmed) => ipcRenderer.send('confirm:answer', { id, confirmed })
}

contextBridge.exposeInMainWorld('confirmApi', confirmApi)
