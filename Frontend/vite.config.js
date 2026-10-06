import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      'monaco-editor/esm/vs/editor/editor.worker.js': fileURLToPath(new URL('./node_modules/monaco-editor/esm/vs/editor/editor.worker.js', import.meta.url)),
      'monaco-editor/esm/vs/language/json/json.worker.js': fileURLToPath(new URL('./node_modules/monaco-editor/esm/vs/language/json/json.worker.js', import.meta.url)),
      'monaco-editor/esm/vs/language/css/css.worker.js': fileURLToPath(new URL('./node_modules/monaco-editor/esm/vs/language/css/css.worker.js', import.meta.url)),
      'monaco-editor/esm/vs/language/html/html.worker.js': fileURLToPath(new URL('./node_modules/monaco-editor/esm/vs/language/html/html.worker.js', import.meta.url)),
      'monaco-editor/esm/vs/language/typescript/ts.worker.js': fileURLToPath(new URL('./node_modules/monaco-editor/esm/vs/language/typescript/ts.worker.js', import.meta.url)),
    },
  },
  server: { proxy: { "/api": "http://127.0.0.1:8000", "/media": "http://127.0.0.1:8000" } },
})
