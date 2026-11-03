import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backend = process.env.LOCAL_MODEL_ROUTER_BACKEND_URL || 'http://127.0.0.1:8787'

export default defineConfig({
  plugins: [react()],
  server: {
    watch: { ignored: ['**/src-tauri/**'] },
    proxy: { '/api': backend, '/health': backend, '/v1': backend }
  }
})
