import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API and WebSocket both live on the FastAPI server in development; Vite
// proxies them so the browser only ever talks to one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
