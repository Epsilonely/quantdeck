import { app, shell, BrowserWindow, dialog, ipcMain } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { EngineClient } from './engine-client'
import { COMMAND_NAMES, type CommandName, type CommandResult } from '../shared/engine'

// engine/state/connection.json in this checkout (D-32). When run from source, the app path is
// gui/. A packaged app would need another way to find it; that is still open.
const CONNECTION_FILE = join(app.getAppPath(), '..', 'engine', 'state', 'connection.json')

const CONFIRMATIONS: Record<CommandName, { message: string; detail: string; button: string }> = {
  emergency_close: {
    message: '긴급 청산을 실행할까요?',
    detail:
      '킬스위치를 발동합니다. 모든 포지션을 시장가 reduceOnly 주문으로 청산하고, 모든 주문을 취소한 뒤, ' +
      '직접 정지를 해제할 때까지 거래를 멈춥니다 (D-27).',
    button: '긴급 청산'
  },
  lift_halt: {
    message: '거래 정지를 해제할까요?',
    detail:
      '킬스위치의 정지를 해제합니다. 오늘 손실이 아직 한도에 있으면 엔진이 거부합니다 (D-33).',
    button: '정지 해제'
  }
}

function broadcast(channel: string, value: unknown): void {
  for (const window of BrowserWindow.getAllWindows()) window.webContents.send(channel, value)
}

const engine = new EngineClient(CONNECTION_FILE, {
  status: (status) => broadcast('engine:status', status),
  state: (state) => broadcast('engine:state', state),
  logs: (update) => broadcast('engine:logs', update)
})

function createWindow(): void {
  const mainWindow = new BrowserWindow({
    width: 1100,
    height: 760,
    show: false,
    autoHideMenuBar: true,
    title: 'QuantDeck',
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false
    }
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  // HMR for renderer base on electron-vite cli.
  // Load the remote URL for development or the local html file for production.
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

app.whenReady().then(() => {
  electronApp.setAppUserModelId('com.electron')

  // Default open or close DevTools by F12 in development
  // and ignore CommandOrControl + R in production.
  // see https://github.com/alex8088/electron-toolkit/tree/master/packages/utils
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  ipcMain.handle('engine:snapshot', () => engine.snapshot())
  ipcMain.handle('engine:command', async (event, name: unknown): Promise<CommandResult | null> => {
    const window = BrowserWindow.fromWebContents(event.sender)
    if (!window || !COMMAND_NAMES.includes(name as CommandName)) {
      return { ok: false, message: `알 수 없는 명령: ${String(name)}` }
    }
    // Confirmed here rather than in the renderer, so nothing the renderer does alone can send one.
    const confirmation = CONFIRMATIONS[name as CommandName]
    const { response } = await dialog.showMessageBox(window, {
      type: 'warning',
      title: 'QuantDeck',
      message: confirmation.message,
      detail: confirmation.detail,
      buttons: [confirmation.button, '취소'],
      defaultId: 1,
      cancelId: 1,
      noLink: true
    })
    if (response !== 0) return null
    return engine.command(name as CommandName)
  })

  engine.start()
  createWindow()

  app.on('activate', function () {
    // On macOS it's common to re-create a window in the app when the
    // dock icon is clicked and there are no other windows open.
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

// Quit when all windows are closed, except on macOS. There, it's common
// for applications and their menu bar to stay active until the user quits
// explicitly with Cmd + Q. Closing the GUI never stops the engine (D-01).
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('will-quit', () => engine.stop())
