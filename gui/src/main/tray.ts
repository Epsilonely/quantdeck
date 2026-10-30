// The GUI's tray icon (GitHub issue #42). Closing the main window hides it there instead of
// quitting, so the alarms keep coming; the tray's menu brings the window back, turns every popup
// on or off, and quits. Quitting the GUI never stops the engine (D-01).

import { Menu, Tray, nativeImage, type NativeImage } from 'electron'

export interface TrayActions {
  open(): void
  setAlarms(enabled: boolean): void
  quit(): void
}

export class AppTray {
  private readonly tray: Tray

  constructor(
    iconPath: string,
    private readonly actions: TrayActions,
    alarmsEnabled: boolean
  ) {
    this.tray = new Tray(trayImage(iconPath))
    this.tray.setToolTip('QuantDeck')
    this.tray.on('click', () => actions.open())
    this.update(alarmsEnabled)
  }

  /** Shows whether alarms are on, after a change from the tray or the settings. */
  update(alarmsEnabled: boolean): void {
    this.tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: 'QuantDeck 열기', click: () => this.actions.open() },
        {
          label: '알림 받기',
          type: 'checkbox',
          checked: alarmsEnabled,
          click: (item) => this.actions.setAlarms(item.checked)
        },
        { type: 'separator' },
        { label: 'QuantDeck 종료 (엔진은 계속 실행)', click: () => this.actions.quit() }
      ])
    )
  }
}

// Windows draws tray icons at 16 px times the display's scale; the app icon is far larger.
function trayImage(path: string): NativeImage {
  const source = nativeImage.createFromPath(path)
  const image = nativeImage.createEmpty()
  for (const scaleFactor of [1, 1.25, 1.5, 2]) {
    const size = Math.round(16 * scaleFactor)
    const buffer = source.resize({ width: size, height: size, quality: 'best' }).toPNG()
    image.addRepresentation({ scaleFactor, buffer })
  }
  return image
}
