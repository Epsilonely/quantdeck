// An alarm popup (#42), one per window.

// Bundled with the app, like the main page's fonts (main.tsx).
import 'pretendard/dist/web/variable/pretendardvariable.css'
import '@fontsource-variable/jetbrains-mono'
import './assets/main.css'
import './assets/popup.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import PopupPage from './PopupPage'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <PopupPage />
  </StrictMode>
)
