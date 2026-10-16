import { app, shell, BrowserWindow, dialog, ipcMain, nativeTheme } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { EngineClient } from './engine-client'
import { EngineLauncher } from './engine-launcher'
import { isThemeSource, loadTheme, setTheme, themeBackground } from './theme'
import {
  COMMAND_NAMES,
  type CommandName,
  type CommandResult,
  type EngineSnapshot
} from '../shared/engine'

// engine/ in this checkout, for the connection file (D-32) and starting the engine (D-34). When
// run from source, the app path is gui/. A packaged app would need another way to find it; that
// is still open.
const ENGINE_DIR = join(app.getAppPath(), '..', 'engine')
const CONNECTION_FILE = join(ENGINE_DIR, 'state', 'connection.json')

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
  },
  stop_engine: {
    message: '엔진을 정지할까요?',
    detail:
      '엔진 프로세스를 종료합니다. 정지된 동안에는 일일 손실 확인과 킬스위치가 동작하지 않고, ' +
      '열린 포지션은 거래소에 걸린 손절 주문만 지킵니다. 진행 중인 킬스위치가 있으면 끝난 뒤에 ' +
      '정지합니다 (D-34).',
    button: '엔진 정지'
  }
}

function broadcast(channel: string, value: unknown): void {
  for (const window of BrowserWindow.getAllWindows()) window.webContents.send(channel, value)
}

const launcher = new EngineLauncher(ENGINE_DIR, (launch) => broadcast('engine:launch', launch))

// The first connection attempt decides whether to start the engine (D-34): if none answers, start
// one; if one is running, leave it as it is. Later disconnects never start one by themselves.
let firstAttempt = true
const engine = new EngineClient(CONNECTION_FILE, {
  status: (status) => {
    broadcast('engine:status', status)
    if (status.phase === 'connected') launcher.connected()
    else if (status.phase === 'disconnected' && firstAttempt) launcher.start()
    if (status.phase !== 'connecting') firstAttempt = false
  },
  state: (state) => broadcast('engine:state', state),
  logs: (update) => broadcast('engine:logs', update)
})

function snapshot(): EngineSnapshot {
  return { ...engine.snapshot(), launch: launcher.state }
}

function createWindow(): void {
  const mainWindow = new BrowserWindow({
    width: 1100,
    height: 760,
    show: false,
    autoHideMenuBar: true,
    title: 'QuantDeck',
    backgroundColor: themeBackground(),
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

  ipcMain.handle('engine:snapshot', () => snapshot())
  ipcMain.handle('engine:start', (event) => {
    // Starting needs no confirmation: a running engine is the normal state, and the instance
    // lock refuses a second one (D-31, D-34).
    if (!BrowserWindow.fromWebContents(event.sender)) return
    if (snapshot().status.phase !== 'connected') launcher.start()
  })
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

  loadTheme()
  ipcMain.handle('theme:get', () => nativeTheme.themeSource)
  ipcMain.handle('theme:set', (event, source: unknown) => {
    if (BrowserWindow.fromWebContents(event.sender) && isThemeSource(source)) setTheme(source)
  })
  // Fires for the user's choice and, under 'system', for a change of the Windows app mode.
  nativeTheme.on('updated', () => {
    for (const window of BrowserWindow.getAllWindows()) window.setBackgroundColor(themeBackground())
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
