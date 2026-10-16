import type { EngineApi } from '../shared/engine'

declare global {
  interface Window {
    api: { engine: EngineApi }
  }
}
