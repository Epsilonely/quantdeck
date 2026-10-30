import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'
import type { Popup, PopupApi } from '../shared/popup'

// An alarm popup's only calls (#42): it reads the popup it shows and says when it was clicked or
// is done. It runs sandboxed, so this script imports nothing but electron.
const popupApi: PopupApi = {
  popup: () => ipcRenderer.invoke('popup:get'),
  onUpdate: (listener) => {
    const handler = (_event: IpcRendererEvent, popup: Popup): void => listener(popup)
    ipcRenderer.on('popup:update', handler)
    return () => {
      ipcRenderer.removeListener('popup:update', handler)
    }
  },
  open: () => ipcRenderer.send('popup:open'),
  close: () => ipcRenderer.send('popup:close')
}

contextBridge.exposeInMainWorld('popupApi', popupApi)
