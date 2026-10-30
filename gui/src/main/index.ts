import { app, shell, BrowserWindow, ipcMain, nativeTheme, screen } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { EngineClient } from './engine-client'
import { EngineLauncher } from './engine-launcher'
import { isThemeSource, loadTheme, setTheme, themeBackground, themeRaised } from './theme'
import { confirmation, parseCommand } from './commands'
import { ConfirmOverlay } from './confirm-overlay'
import { LogWindow } from './log-window'
import { AlarmCenter } from './alarms'
import { PopupStack } from './popups'
import { AppTray } from './tray'
import { POPUP_SECONDS, parseAlarmSettings, type AlarmSaveResult } from '../shared/alarms'
import type { CommandResult, EngineSnapshot } from '../shared/engine'
import type { LogSnapshot } from '../shared/logs'

// engine/ in this checkout, for the connection file (D-32) and starting the engine (D-34). When
// run from source, the app path is gui/. A packaged app would need another way to find it; that
// is still open.
const ENGINE_DIR = join(app.getAppPath(), '..', 'engine')
const CONNECTION_FILE = join(ENGINE_DIR, 'state', 'connection.json')

// The engine log's window (D-55) gets only the records and the connection, from its own calls.
const logWindow = new LogWindow((unseen) => broadcast('logs:unseen', unseen))

// The alarms (#42) watch what the engine sends and show popups, which get only their own content.
const popups = new PopupStack(showMainWindow)
const alarms = new AlarmCenter(popups, (settings) => {
  broadcast('alarms:settings', settings)
  tray?.update(settings.enabled)
})
let tray: AppTray | null = null

function broadcast(channel: string, value: unknown): void {
  for (const window of BrowserWindow.getAllWindows()) {
    if (!logWindow.owns(window) && !popups.owns(window)) window.webContents.send(channel, value)
  }
}

const launcher = new EngineLauncher(ENGINE_DIR, (launch) => broadcast('engine:launch', launch))

// The first connection attempt decides whether to start the engine (D-34): if none answers, start
// one; if one is running, leave it as it is. Later disconnects never start one by themselves.
let firstAttempt = true
const engine = new EngineClient(CONNECTION_FILE, {
  status: (status) => {
    broadcast('engine:status', status)
    logWindow.status(status)
    alarms.status(status)
    if (status.phase === 'connected') launcher.connected()
    else if (status.phase === 'disconnected' && firstAttempt) launcher.start()
    if (status.phase !== 'connecting') firstAttempt = false
  },
  state: (state) => {
    broadcast('engine:state', state)
    alarms.state(state)
  },
  logs: (update) => {
    logWindow.logs(update)
    alarms.logs(update)
  },
  catalog: (catalog) => broadcast('engine:catalog', catalog),
  trades: (trades) => {
    broadcast('engine:trades', trades)
    alarms.trades(trades)
  },
  candles: (update) => broadcast('engine:candles', update)
})

function snapshot(): EngineSnapshot {
  return { ...engine.snapshot(), launch: launcher.state }
}

// Each window's confirmation page (D-53).
const overlays = new WeakMap<BrowserWindow, ConfirmOverlay>()

// Why a command wasn't sent when the user was never asked about it.
const NOT_ASKED = {
  busy: '다른 확인 창이 열려 있어 명령을 보내지 않았습니다',
  unavailable: '확인 창을 띄우지 못했거나 확인 창이 비정상 종료되어 명령을 보내지 않았습니다'
}

let mainWindow: BrowserWindow | null = null
// Closing the main window hides it in the tray (#42) until the app quits from the tray's menu.
let quitting = false
let trayHintShown = false

/** Brings the main window back from the tray, the taskbar, or behind other windows. */
function showMainWindow(): void {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow()
    return
  }
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
}

function hideToTray(): void {
  mainWindow?.hide()
  if (trayHintShown) return
  trayHintShown = true
  popups.show({
    tone: 'info',
    icon: 'info',
    title: '트레이에서 실행 중',
    body: '창은 트레이 아이콘을 눌러 다시 엽니다\n종료는 트레이 아이콘의 메뉴에서 합니다',
    time: new Date().toISOString(),
    count: 1,
    seconds: 10,
    sound: false
  })
}

function createWindow(): void {
  // Wide enough for the symbol table beside the trades panel (D-51), within the screen.
  const area = screen.getPrimaryDisplay().workAreaSize
  const window = new BrowserWindow({
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

  mainWindow = window
  overlays.set(window, new ConfirmOverlay(window))

  window.on('ready-to-show', () => {
    window.show()
  })
  window.on('close', (event) => {
    if (quitting) return
    event.preventDefault()
    hideToTray()
  })
  // Windows is shutting down or the user is signing out: let the window close.
  window.on('session-end', () => {
    quitting = true
  })
  // The log window has no use without the main one, and would keep the app from quitting.
  window.on('closed', () => {
    if (mainWindow === window) mainWindow = null
    logWindow.close()
  })

  window.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  // HMR for renderer base on electron-vite cli.
  // Load the remote URL for development or the local html file for production.
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    window.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    window.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// With the tray, a second GUI would only add a second icon and a second set of popups: it hands
// over to the first one's window and quits.
const singleInstance = app.requestSingleInstanceLock()
if (!singleInstance) app.quit()
app.on('second-instance', () => showMainWindow())

app.whenReady().then(() => {
  if (!singleInstance) return
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
  ipcMain.handle('logs:open', (event) => {
    if (BrowserWindow.fromWebContents(event.sender)) logWindow.open()
  })
  ipcMain.handle('logs:unseen', () => logWindow.counts)
  ipcMain.handle('logs:snapshot', (): LogSnapshot => engine.logSnapshot())
  ipcMain.handle('engine:command', async (event, value: unknown): Promise<CommandResult | null> => {
    const window = BrowserWindow.fromWebContents(event.sender)
    const command = parseCommand(value)
    if (typeof command === 'string') return { ok: false, message: command }
    const overlay = window && overlays.get(window)
    if (!overlay) return { ok: false, message: '명령을 보낸 창을 찾을 수 없습니다' }
    // Confirmed on a page the main process owns rather than in the renderer, with the copy that is
    // sent, so nothing the renderer does alone can send a command, or show one setting and send
    // another.
    const answer = await overlay.ask(confirmation(command, engine.snapshot().state))
    if (answer === 'cancelled') return null
    if (answer !== 'confirmed') return { ok: false, message: NOT_ASKED[answer] }
    if (command.name === 'stop_engine') alarms.stopRequested()
    return engine.command(command)
  })

  // The alarm settings (#42) are the GUI's own, like the theme, and send nothing to the engine,
  // so they need no confirmation; the main process still checks what it saves.
  ipcMain.handle('alarms:get', () => alarms.current)
  ipcMain.handle('alarms:set', (event, value: unknown): AlarmSaveResult => {
    if (!BrowserWindow.fromWebContents(event.sender)) {
      return { ok: false, message: '설정을 보낸 창을 찾을 수 없습니다' }
    }
    const settings = parseAlarmSettings(value)
    if (typeof settings === 'string') return { ok: false, message: settings }
    alarms.set(settings)
    return { ok: true }
  })
  ipcMain.handle('alarms:test', (event, value: unknown) => {
    const { sound, seconds } = (value ?? {}) as Record<string, unknown>
    if (
      BrowserWindow.fromWebContents(event.sender) &&
      typeof sound === 'boolean' &&
      (POPUP_SECONDS as readonly unknown[]).includes(seconds)
    ) {
      alarms.test({ sound, seconds: seconds as number })
    }
  })

  loadTheme()
  ipcMain.handle('theme:get', () => nativeTheme.themeSource)
  ipcMain.handle('theme:set', (event, source: unknown) => {
    if (BrowserWindow.fromWebContents(event.sender) && isThemeSource(source)) setTheme(source)
  })
  // Fires for the user's choice and, under 'system', for a change of the Windows app mode.
  nativeTheme.on('updated', () => {
    for (const window of BrowserWindow.getAllWindows()) {
      window.setBackgroundColor(popups.owns(window) ? themeRaised() : themeBackground())
    }
  })

  tray = new AppTray(
    icon,
    {
      open: showMainWindow,
      setAlarms: (enabled) => alarms.setEnabled(enabled),
      quit: () => app.quit()
    },
    alarms.current.enabled
  )

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

// From the tray's menu, or anything else that quits the app: the main window closes for real.
app.on('before-quit', () => {
  quitting = true
  popups.stop()
})

app.on('will-quit', () => engine.stop())
