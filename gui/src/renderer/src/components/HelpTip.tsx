import { useEffect, useLayoutEffect, useRef, useState } from 'react'

// A "?" mark that shows `text` while the pointer is over it or it has keyboard focus. The tip is
// fixed to the window and kept inside it, so it is never cut off at the window's edge and never
// widens the page. Inside a <label>, a click on the mark doesn't reach the label's input.

const GAP = 6 // between the mark and the tip
const MARGIN = 8 // between the tip and the window's edge

interface Place {
  left: number
  top: number
}

function HelpTip({ text }: { text: string }): React.JSX.Element {
  const mark = useRef<HTMLSpanElement>(null)
  const tip = useRef<HTMLSpanElement>(null)
  const [open, setOpen] = useState(false)
  const [place, setPlace] = useState<Place | null>(null)

  // Measured before the browser paints, so the tip never shows at the wrong place first.
  useLayoutEffect(() => {
    if (!open || !mark.current || !tip.current) return
    const anchor = mark.current.getBoundingClientRect()
    const box = tip.current.getBoundingClientRect()
    const centered = anchor.left + anchor.width / 2 - box.width / 2
    const width = document.documentElement.clientWidth // without the scrollbar
    const left = Math.max(MARGIN, Math.min(centered, width - box.width - MARGIN))
    // Above the mark, or below it when there's no room above.
    const above = anchor.top - GAP - box.height
    setPlace({ left, top: above >= MARGIN ? above : anchor.bottom + GAP })
  }, [open])

  const show = (): void => setOpen(true)
  const hide = (): void => {
    setOpen(false)
    setPlace(null)
  }

  // The page scrolling would leave the fixed tip behind.
  useEffect(() => {
    if (!open) return
    const close = (): void => {
      setOpen(false)
      setPlace(null)
    }
    window.addEventListener('scroll', close, true)
    return () => window.removeEventListener('scroll', close, true)
  }, [open])

  return (
    <span
      ref={mark}
      className="help"
      tabIndex={0}
      role="img"
      aria-label={text}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      onClick={(event) => event.preventDefault()}
    >
      ?
      {open && (
        <span
          ref={tip}
          className="help-tip"
          style={place ?? { left: 0, top: 0, visibility: 'hidden' }}
        >
          {text}
        </span>
      )}
    </span>
  )
}

export default HelpTip
