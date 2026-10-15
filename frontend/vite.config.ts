import { fileURLToPath } from 'node:url'
import { defineConfig, searchForWorkspaceRoot } from 'vite'
import react from '@vitejs/plugin-react'

// shared/ — единая модель риска и сид (общие с бэкендом), лежит вне frontend/
const shared = fileURLToPath(new URL('../shared', import.meta.url))

export default defineConfig({
  plugins: [react()],
  optimizeDeps: { exclude: ['maplibre-gl'] },
  server: {
    port: 5180,
    proxy: { '/api': process.env.API_PROXY ?? 'http://localhost:8000' },
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), shared] },
  },
})
