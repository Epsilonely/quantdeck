import { app, shell, BrowserWindow, dialog, ipcMain, nativeTheme, screen } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { EngineClient } from './engine-client'
import { EngineLauncher } from './engine-launcher'
import { isThemeSource, loadTheme, setTheme, themeBackground } from './theme'
import { confirmation, parseCommand } from './commands'
import type { CommandResult, EngineSnapshot } from '../shared/engine'

// engine/ in this checkout, for the connection file (D-32) and starting the engine (D-34). When
// run from source, the app path is gui/. A packaged app would need another way to find it; that
// is still open.
const ENGINE_DIR = join(app.getAppPath(), '..', 'engine')
const CONNECTION_FILE = join(ENGINE_DIR, 'state', 'connection.json')

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
  logs: (update) => broadcast('engine:logs', update),
  catalog: (catalog) => broadcast('engine:catalog', catalog),
  trades: (trades) => broadcast('engine:trades', trades)
})

function snapshot(): EngineSnapshot {
  return { ...engine.snapshot(), launch: launcher.state }
}

function createWindow(): void {
  // Wide enough for the symbol table beside the trades panel (D-51), within the screen.
  const area = screen.getPrimaryDisplay().workAreaSize
  const mainWindow = new BrowserWindow({
    width: Math.min(1400, area.width),
    height: Math.min(800, area.height),
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
  ipcMain.handle('engine:command', async (event, value: unknown): Promise<CommandResult | null> => {
    const window = BrowserWindow.fromWebContents(event.sender)
    const command = parseCommand(value)
    if (typeof command === 'string') return { ok: false, message: command }
    if (!window) return { ok: false, message: '명령을 보낸 창을 찾을 수 없습니다' }
    // Confirmed here rather than in the renderer, with the copy that is sent, so nothing the
    // renderer does alone can send a command, or show one setting and send another.
    const ask = confirmation(command, engine.snapshot().state)
    const { response } = await dialog.showMessageBox(window, {
      type: 'warning',
      title: 'QuantDeck',
      message: ask.message,
      detail: ask.detail,
      buttons: [ask.button, '취소'],
      defaultId: 1,
      cancelId: 1,
      noLink: true
    })
    if (response !== 0) return null
    return engine.command(command)
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
