import { useEffect, useRef, useState } from 'react'
import type { Popup, PopupTone } from '../../shared/popup'
import AlarmIcon from './components/AlarmIcon'
import { clock } from './format'

// An alarm popup (#42). The main process writes it and places the window; the page shows it,
// plays its sound, and closes once its time is up, which stops while the pointer is over it. A
// click anywhere but the close button opens the main window.

// The same popup coming again within this long chimes once.
const SOUND_GAP_MS = 1500

// The bundled fonts the popup draws with. They load with font-display: swap, and each popup is a
// page of its own that loads them again, so the window waits for them: shown earlier, its text
// changed font, and moved, once on screen (#45). A font that doesn't load holds it back this long
// at most, and the popup shows in the fallback font.
const FONTS = [
  '600 14px "Pretendard Variable"',
  '400 13px "Pretendard Variable"',
  '12px "JetBrains Mono Variable"'
]
const FONT_WAIT_MS = 1500

/** Resolves once the popup's fonts have loaded and a frame has been painted with them. */
function whenDrawn(): Promise<void> {
  const fonts = Promise.all(FONTS.map((font) => document.fonts.load(font))).then(
    () => document.fonts.ready
  )
  const capped = Promise.race([fonts, new Promise((resolve) => setTimeout(resolve, FONT_WAIT_MS))])
  return capped.then(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
        // In case frames don't run while the window is hidden.
        setTimeout(resolve, 100)
      })
  )
}

// Each tone's notes, in Hz: rising for news, level for a warning, falling and rising for danger.
const CHIMES: Record<PopupTone, number[]> = {
  info: [660, 880],
  ok: [660, 990],
  warn: [587, 587],
  danger: [880, 660, 880]
}

// Made with Web Audio, so the app carries no sound file.
function chime(tone: PopupTone): void {
  const context = new AudioContext()
  const notes = CHIMES[tone]
  const step = 0.14
  notes.forEach((frequency, i) => {
    const start = context.currentTime + i * step
    const oscillator = context.createOscillator()
    const gain = context.createGain()
    oscillator.type = 'sine'
    oscillator.frequency.value = frequency
    gain.gain.setValueAtTime(0.0001, start)
    gain.gain.exponentialRampToValueAtTime(0.2, start + 0.015)
    gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.35)
    oscillator.connect(gain).connect(context.destination)
    oscillator.start(start)
    oscillator.stop(start + 0.4)
  })
  window.setTimeout(() => void context.close(), (notes.length * step + 0.5) * 1000)
}

function PopupPage(): React.JSX.Element | null {
  const [popup, setPopup] = useState<Popup | null>(null)
  const [hovered, setHovered] = useState(false)
  const [onScreen, setOnScreen] = useState(false) // the main process has been told to show it
  const remaining = useRef(0) // ms left before it closes, while the pointer is away
  const lastSound = useRef(-Infinity)
  const told = useRef(false)

  useEffect(() => {
    const { popupApi } = window
    // Subscribe before asking, as the other pages do; whichever answer comes last is the newest.
    const stop = popupApi.onUpdate(setPopup)
    let active = true
    popupApi.popup().then((shown) => {
      if (active && shown) setPopup(shown)
    })
    return () => {
      active = false
      stop()
    }
  }, [])

  // The window stays hidden until the popup is drawn in its own fonts, so it never shows empty
  // (#43) or changes font on screen (#45).
  useEffect(() => {
    if (!popup || told.current) return
    told.current = true
    void whenDrawn().then(() => {
      window.popupApi.ready()
      setOnScreen(true)
    })
  }, [popup])

  // A new or updated popup on screen gets its whole time again, and its sound.
  useEffect(() => {
    if (!popup || !onScreen) return
    remaining.current = popup.seconds * 1000
    const now = performance.now()
    if (popup.sound && now - lastSound.current >= SOUND_GAP_MS) {
      lastSound.current = now
      chime(popup.tone)
    }
  }, [popup, onScreen])

  useEffect(() => {
    if (!popup || !onScreen || popup.seconds === 0 || hovered) return
    const started = performance.now()
    const timer = window.setTimeout(() => window.popupApi.close(), remaining.current)
    return () => {
      window.clearTimeout(timer)
      remaining.current -= performance.now() - started
    }
  }, [popup, onScreen, hovered])

  if (!popup) return null
  const lines = popup.body.split('\n').filter((line) => line !== '')
  return (
    <div
      className={`popup tone-${popup.tone}`}
      role="status"
      title="눌러서 QuantDeck 창 열기"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={() => window.popupApi.open()}
    >
      <div className="popup-icon">
        <AlarmIcon icon={popup.icon} size={18} />
      </div>
      <div className="popup-main">
        <div className="popup-head">
          <span className="popup-title">{popup.title}</span>
          {popup.count > 1 && <span className="popup-count">×{popup.count}</span>}
          <span className="spacer" />
          <span className="popup-time">{clock(popup.time)}</span>
        </div>
        {/* A body of one line, such as a halt's reason or an error, may take both lines. */}
        {lines.slice(0, 2).map((line, i) => (
          <div className={`popup-line${lines.length === 1 ? ' wrap' : ''}`} key={i}>
            {line}
          </div>
        ))}
      </div>
      <button
        className="popup-close"
        aria-label="닫기"
        title="닫기"
        onClick={(event) => {
          event.stopPropagation()
          window.popupApi.close()
        }}
      >
        <svg
          viewBox="0 0 24 24"
          width="14"
          height="14"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          aria-hidden="true"
        >
          <path d="M18 6 6 18M6 6l12 12" />
        </svg>
      </button>
      {popup.seconds > 0 && onScreen && (
        // Restarted by each update, as the time is; it only shows the time left.
        <div
          key={`${popup.count}:${popup.time}`}
          className="popup-progress"
          style={{
            animationDuration: `${popup.seconds}s`,
            animationPlayState: hovered ? 'paused' : 'running'
          }}
        />
      )}
    </div>
  )
}

export default PopupPage
