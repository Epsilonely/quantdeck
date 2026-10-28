// The engine log's own window (D-55).

// Bundled with the app, like the main page's fonts (main.tsx).
import 'pretendard/dist/web/variable/pretendardvariable.css'
import '@fontsource-variable/jetbrains-mono'
import './assets/main.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import LogPage from './LogPage'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LogPage />
  </StrictMode>
)
