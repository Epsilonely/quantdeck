// The GUI's color theme and the API the preload script gives the renderer for it. Types only,
// shared by main, preload, and renderer. 'system' follows the Windows app mode.

export const THEME_SOURCES = ['system', 'light', 'dark'] as const
export type ThemeSource = (typeof THEME_SOURCES)[number]

export interface ThemeApi {
  get(): Promise<ThemeSource>
  set(source: ThemeSource): Promise<void>
}
