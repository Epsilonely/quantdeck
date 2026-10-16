// The user's theme choice, saved in the app's userData folder and applied through
// nativeTheme.themeSource. That sets the renderer's prefers-color-scheme, which main.css picks its
// colors from, and the window frame's colors.

import { app, nativeTheme } from 'electron'
import { readFileSync, writeFileSync } from 'fs'
import { join } from 'path'
import { THEME_SOURCES, type ThemeSource } from '../shared/theme'

// The window's background before the page paints; matches --bg in main.css.
const BACKGROUND = { dark: '#15171c', light: '#f4f5f7' }

function themeFile(): string {
  return join(app.getPath('userData'), 'theme.json')
}

export function isThemeSource(value: unknown): value is ThemeSource {
  return THEME_SOURCES.includes(value as ThemeSource)
}

/** Applies the saved choice, or 'system' when none is saved or the file can't be read. */
export function loadTheme(): void {
  let source: ThemeSource = 'system'
  try {
    const saved: unknown = JSON.parse(readFileSync(themeFile(), 'utf-8')).source
    if (isThemeSource(saved)) source = saved
  } catch {
    // No choice saved yet.
  }
  nativeTheme.themeSource = source
}

export function setTheme(source: ThemeSource): void {
  nativeTheme.themeSource = source
  try {
    writeFileSync(themeFile(), JSON.stringify({ source }))
  } catch (e) {
    // The theme still applies until the GUI closes.
    console.error(`[theme] could not save the theme: ${e}`)
  }
}

export function themeBackground(): string {
  return nativeTheme.shouldUseDarkColors ? BACKGROUND.dark : BACKGROUND.light
}
