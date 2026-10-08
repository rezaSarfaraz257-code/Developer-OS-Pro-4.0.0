import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = fileURLToPath(new URL('.', import.meta.url))

// Build the IDE route as a real HTML entry as well as supporting SPA rewrites.
// This keeps direct navigation to /ide working even if the CDN serves an
// existing path before evaluating the catch-all rewrite.
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        main: resolve(frontendRoot, 'index.html'),
        ide: resolve(frontendRoot, 'ide/index.html'),
      },
    },
  },
  server: { proxy: { "/api": "http://127.0.0.1:8000", "/media": "http://127.0.0.1:8000" } },
})
