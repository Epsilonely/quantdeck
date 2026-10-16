import type { EngineApi } from '../shared/engine'
import type { ThemeApi } from '../shared/theme'

declare global {
  interface Window {
    api: { engine: EngineApi; theme: ThemeApi }
  }
}
