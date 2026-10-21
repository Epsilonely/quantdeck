// Bundled with the app, so the GUI looks the same on a machine without these fonts installed.
import 'pretendard/dist/web/variable/pretendardvariable.css'
import '@fontsource-variable/jetbrains-mono'
import './assets/main.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>
)
