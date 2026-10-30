import { resolve } from 'path'
import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  main: {},
  // The confirmation page (D-53), the log window (D-55), and the alarm popups (#42) each have a
  // preload and a page of their own. Their preloads run sandboxed, so they must not share a chunk
  // with the main window's.
  preload: {
    build: {
      rollupOptions: {
        input: {
          index: resolve('src/preload/index.ts'),
          confirm: resolve('src/preload/confirm.ts'),
          logs: resolve('src/preload/logs.ts'),
          popup: resolve('src/preload/popup.ts')
        }
      }
    }
  },
  renderer: {
    resolve: {
      alias: {
        '@renderer': resolve('src/renderer/src')
      }
    },
    plugins: [react()],
    build: {
      rollupOptions: {
        input: {
          index: resolve('src/renderer/index.html'),
          confirm: resolve('src/renderer/confirm.html'),
          logs: resolve('src/renderer/logs.html'),
          popup: resolve('src/renderer/popup.html')
        }
      }
    }
  }
})
