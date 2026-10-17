import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// Every build gets an id: the page knows its own, and /version.json says which one the server has
// now, so an open page notices a new deployment and switches to it (src/updates.ts).
const BUILD_ID = process.env.APEX21_BUILD_ID || Date.now().toString(36)

function versionFile(): Plugin {
  return {
    name: 'apex21-version-file',
    apply: 'build',
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'version.json', source: JSON.stringify({ build: BUILD_ID }) + '\n' })
    },
  }
}

// The dev server forwards /api/* to the FastAPI backend, so the browser never
// makes a cross-origin call. Override the target with APEX21_API_URL.
export default defineConfig({
  plugins: [react(), versionFile()],
  define: { __BUILD_ID__: JSON.stringify(BUILD_ID) },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.APEX21_API_URL ?? 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
