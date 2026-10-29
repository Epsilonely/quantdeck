import { useEffect, useLayoutEffect, useRef, useState } from 'react'

// An anchor that shows a tip while the pointer is over it or it has keyboard focus. The tip is
// fixed to the window and kept inside it, so it is never cut off at the window's edge and never
// widens the page. Inside a <label>, a click on the anchor doesn't reach the label's input.

const GAP = 6 // between the anchor and the tip
const MARGIN = 8 // between the tip and the window's edge

interface Place {
  left: number
  top: number
}

interface TipProps {
  tip: React.ReactNode
  className: string // the anchor's look
  children: React.ReactNode // the anchor's content
  role?: string
  'aria-label'?: string
}

export function Tip({ tip: content, className, children, ...aria }: TipProps): React.JSX.Element {
  const anchor = useRef<HTMLSpanElement>(null)
  const tip = useRef<HTMLSpanElement>(null)
  const [open, setOpen] = useState(false)
  const [place, setPlace] = useState<Place | null>(null)

  // Measured before the browser paints, so the tip never shows at the wrong place first.
  useLayoutEffect(() => {
    if (!open || !anchor.current || !tip.current) return
    const box = anchor.current.getBoundingClientRect()
    const size = tip.current.getBoundingClientRect()
    const centered = box.left + box.width / 2 - size.width / 2
    const width = document.documentElement.clientWidth // without the scrollbar
    const left = Math.max(MARGIN, Math.min(centered, width - size.width - MARGIN))
    // Above the anchor, or below it when there's no room above.
    const above = box.top - GAP - size.height
    setPlace({ left, top: above >= MARGIN ? above : box.bottom + GAP })
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
      ref={anchor}
      className={className}
      tabIndex={0}
      {...aria}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      onClick={(event) => event.preventDefault()}
    >
      {children}
      {open && (
        <span
          ref={tip}
          className="help-tip"
          style={place ?? { left: 0, top: 0, visibility: 'hidden' }}
        >
          {content}
        </span>
      )}
    </span>
  )
}

/** A "?" mark that shows `text`. */
function HelpTip({ text }: { text: string }): React.JSX.Element {
  return (
    <Tip tip={text} className="help" role="img" aria-label={text}>
      ?
    </Tip>
  )
}

export default HelpTip
