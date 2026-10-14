import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  optimizeDeps: { exclude: ['maplibre-gl'] },
  server: {
    port: 5180,
    proxy: { '/api': process.env.API_PROXY ?? 'http://localhost:8000' },
  },
})
