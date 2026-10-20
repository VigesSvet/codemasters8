import { createReadStream, existsSync, readFileSync, statSync } from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { BlockList, isIP, type Server as NetServer } from 'node:net'
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

// Кто может открыть dev-сервер, когда он слушает не только localhost(docker compose: --host 0.0.0.0). Пускаются эта
// машина и адреса из SAQ_ALLOWED_CLIENTS(IP или подсети через запятую, например устройство Tailscale), остальные
// соединения рвутся сразу - до HTTP, прокси /api и HMR. "Эта машина" - loopback и, в контейнере, шлюз его сети: обращения
// хоста к 127.0.0.1:5180 docker-proxy пересылает от адреса шлюза. Поэтому порт публикуется только на IPv4(docker-compose.yml):
// соединения по IPv6 docker-proxy пересылает от того же шлюза
function containerGateway(): string | null {
  if (!existsSync('/.dockerenv')) return null
  try {
    for (const line of readFileSync('/proc/net/route', 'utf8').split('\n').slice(1)) {
      const [, dest, gw] = line.trim().split(/\s+/)
      // маршрут по умолчанию; адрес шлюза - 8 hex-цифр в обратном порядке байтов
      if (dest === '00000000' && /^[0-9A-F]{8}$/i.test(gw ?? '')) {
        return (gw.match(/../g) ?? []).reverse().map((h) => parseInt(h, 16)).join('.')
      }
    }
  } catch {
    // нет /proc/net/route - шлюз не доверяем
  }
  return null
}

function allowedClients(spec: string, gateway: string | null): BlockList {
  const list = new BlockList()
  list.addSubnet('127.0.0.0', 8, 'ipv4')
  list.addAddress('::1', 'ipv6')
  if (gateway) list.addAddress(gateway, 'ipv4')
  for (const item of spec.split(',').map((s) => s.trim()).filter(Boolean)) {
    const [addr, bits, extra] = item.split('/')
    const v = isIP(addr)
    const max = v === 4 ? 32 : 128
    if (!v || extra !== undefined || (bits !== undefined && !(/^\d{1,3}$/.test(bits) && Number(bits) <= max))) {
      throw new Error(`SAQ_ALLOWED_CLIENTS: "${item}" - не IP-адрес и не подсеть`)
    }
    const type = v === 4 ? 'ipv4' : 'ipv6'
    if (bits === undefined) list.addAddress(addr, type)
    else list.addSubnet(addr, Number(bits), type)
  }
  return list
}

function clientAllowlist(): Plugin {
  const allowed = allowedClients(process.env.SAQ_ALLOWED_CLIENTS ?? '', containerGateway())
  const warned = new Set<string>()
  const guard = (srv: NetServer | null | undefined) => {
    srv?.on('connection', (socket) => {
      const ip = (socket.remoteAddress ?? '').replace(/^::ffff:/, '')
      const v = isIP(ip)
      if (v && allowed.check(ip, v === 4 ? 'ipv4' : 'ipv6')) return
      if (!warned.has(ip)) {
        warned.add(ip)
        console.warn(`Соединение с ${ip || 'неизвестного адреса'} отклонено: адреса нет в SAQ_ALLOWED_CLIENTS`)
      }
      socket.destroy()
    })
  }
  return {
    name: 'saq-client-allowlist',
    configureServer: (server) => guard(server.httpServer),
    configurePreviewServer: (server) => guard(server.httpServer),
  }
}

// Имена, по которым открывают интерфейс кроме localhost и IP(например имя машины в Tailscale MagicDNS), через запятую
const webHosts = (process.env.SAQ_WEB_HOSTS ?? '').split(',').map((s) => s.trim()).filter(Boolean)

export default defineConfig({
  plugins: [react(), snapshotMedia(), clientAllowlist()],
  optimizeDeps: { exclude: ['maplibre-gl'] },
  server: {
    port: 5180,
    allowedHosts: webHosts,
    // Host браузера уходит в API как есть(краткая запись '/api': адрес включила бы changeOrigin и подменила Host на
    // адрес API): по нему API сверяет Origin изменяющих запросов(CrossSiteGuard) и имя сервера(HostAllowlist)
    proxy: { '/api': { target: process.env.API_PROXY ?? 'http://localhost:8000', changeOrigin: false } },
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), shared] },
  },
})
