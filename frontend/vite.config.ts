import { createReadStream, statSync } from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, searchForWorkspaceRoot, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// shared/ — единая модель риска и стартовый набор (общие с бэкендом), лежит вне frontend/
const shared = fileURLToPath(new URL('../shared', import.meta.url))

// Картинки офлайн-копии стартового набора: GET /snapshot-media/<sha256>.<ext> → shared/snapshot/media/<файл>.
// Имя — только 64 hex-символа и расширение изображения: ни каталогов, ни «..», ни кодированных символов. Статическая сборка
// без этого сервера отдаст 404 — интерфейс покажет заглушку вместо картинки
const SNAPSHOT_MEDIA = /^\/snapshot-media\/([0-9a-f]{64}\.(jpg|png|webp|gif))$/
const IMAGE_TYPES: Record<string, string> = { jpg: 'image/jpeg', png: 'image/png', webp: 'image/webp', gif: 'image/gif' }

/** Размер обычного файла; нет файла (или это каталог) — 0 */
const fileSize = (file: string) => {
  try {
    const st = statSync(file)
    return st.isFile() ? st.size : 0
  } catch {
    return 0
  }
}

function snapshotMedia(): Plugin {
  const dir = join(shared, 'snapshot', 'media')
  const serve = (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    const path = (req.url ?? '').split('?')[0]
    if (!path.startsWith('/snapshot-media/')) return next()
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.statusCode = 405
      res.setHeader('Allow', 'GET, HEAD')
      res.end()
      return
    }
    const m = SNAPSHOT_MEDIA.exec(path)
    const file = m ? join(dir, m[1]) : ''
    const size = file ? fileSize(file) : 0
    if (!m || !size) {
      res.statusCode = 404
      res.setHeader('Content-Type', 'text/plain; charset=utf-8')
      res.end('Нет такого файла в офлайн-копии')
      return
    }
    res.statusCode = 200
    res.setHeader('Content-Type', IMAGE_TYPES[m[2]])
    res.setHeader('Content-Length', String(size))
    res.setHeader('X-Content-Type-Options', 'nosniff')
    // имя — хэш содержимого: файл под ним не меняется
    res.setHeader('Cache-Control', 'public, max-age=31536000, immutable')
    if (req.method === 'HEAD') { res.end(); return }
    createReadStream(file).on('error', () => res.destroy()).pipe(res)
  }
  return {
    name: 'saq-snapshot-media',
    configureServer: (server) => { server.middlewares.use(serve) },
    configurePreviewServer: (server) => { server.middlewares.use(serve) },
  }
}

export default defineConfig({
  plugins: [react(), snapshotMedia()],
  optimizeDeps: { exclude: ['maplibre-gl'] },
  server: {
    port: 5180,
    proxy: { '/api': process.env.API_PROXY ?? 'http://localhost:8000' },
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), shared] },
  },
})
