import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    watch: { ignored: ['**/src-tauri/**'] },
    proxy: { '/api': 'http://127.0.0.1:8787', '/health': 'http://127.0.0.1:8787', '/v1': 'http://127.0.0.1:8787' }
  }
})
