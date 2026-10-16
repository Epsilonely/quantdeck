import { useEffect, useRef, useState } from 'react'
import { THEME_SOURCES, type ThemeSource } from '../../../shared/theme'

const LABELS: Record<ThemeSource, string> = {
  system: '시스템 설정 따르기',
  light: '라이트',
  dark: '다크'
}

// Line icons on a 24×24 grid, drawn in the button's text color.
const ICONS: Record<ThemeSource, React.JSX.Element> = {
  system: (
    <>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8M12 16v4" />
    </>
  ),
  light: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </>
  ),
  dark: <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
}

// A little longer than the color transitions under .theme-fading in main.css.
const FADE_MS = 400

// The main process applies the choice; the colors follow from prefers-color-scheme in main.css.
function ThemeSwitch(): React.JSX.Element {
  const [source, setSource] = useState<ThemeSource | null>(null)
  const fadeTimer = useRef<number | undefined>(undefined)

  useEffect(() => {
    let active = true
    window.api.theme.get().then((saved) => {
      if (active) setSource(saved)
    })
    return () => {
      active = false
    }
  }, [])

  const choose = (next: ThemeSource): void => {
    if (next === source) return
    // Colors fade while the class is on, instead of snapping to the new theme.
    const root = document.documentElement
    root.classList.add('theme-fading')
    window.clearTimeout(fadeTimer.current)
    fadeTimer.current = window.setTimeout(() => root.classList.remove('theme-fading'), FADE_MS)
    setSource(next)
    void window.api.theme.set(next)
  }

  return (
    <div className="theme-toggle" role="radiogroup" aria-label="화면 테마">
      {/* Mounted once the choice is known, so it doesn't slide in from the first slot. */}
      {source !== null && (
        <span
          className="theme-thumb"
          style={{ transform: `translateX(${THEME_SOURCES.indexOf(source) * 100}%)` }}
        />
      )}
      {THEME_SOURCES.map((option) => (
        <button
          key={option}
          role="radio"
          aria-checked={source === option}
          aria-label={LABELS[option]}
          title={LABELS[option]}
          data-option={option}
          onClick={() => choose(option)}
        >
          <svg
            viewBox="0 0 24 24"
            width="16"
            height="16"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            {ICONS[option]}
          </svg>
        </button>
      ))}
    </div>
  )
}

export default ThemeSwitch
