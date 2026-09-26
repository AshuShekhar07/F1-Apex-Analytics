import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server forwards /api/* to the FastAPI backend, so the browser never
// makes a cross-origin call. Override the target with APEX21_API_URL.
export default defineConfig({
  plugins: [react()],
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
