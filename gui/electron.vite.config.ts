import { resolve } from 'path'
import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  main: {},
  // The confirmation page (D-53) has a preload and a page of its own. Its preload runs sandboxed,
  // so it must not share a chunk with the main window's.
  preload: {
    build: {
      rollupOptions: {
        input: {
          index: resolve('src/preload/index.ts'),
          confirm: resolve('src/preload/confirm.ts')
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
          confirm: resolve('src/renderer/confirm.html')
        }
      }
    }
  }
})
