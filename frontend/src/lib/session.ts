// Сессия демо-входа: выбранный аналитик в localStorage (общий для вкладок — печатная версия тоже видит вход).
// В приватном окне или при запрете cookies обращение к storage может бросить — тогда вход живёт до перезагрузки.

/** Кто действует: name пишется в журнал автором, short — инициалы на аватаре */
export interface User {
  id: string
  name: string
  short: string
  role: string
}

export const SESSION_KEY = 'saq.analyst'

type Box = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

/** localStorage или null, если он недоступен (нет окна, запрет доступа) */
export function localBox(): Box | null {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

const str = (v: unknown) => typeof v === 'string' && v.trim() !== ''

/** Сохранённый пользователь; испорченная запись или недоступный storage — null */
export function readSession(box: Box | null = localBox()): User | null {
  if (!box) return null
  let raw: string | null
  try {
    raw = box.getItem(SESSION_KEY)
  } catch {
    return null
  }
  if (!raw) return null
  let v: Partial<Record<keyof User, unknown>> | null
  try {
    v = JSON.parse(raw)
  } catch {
    return null
  }
  if (!v || typeof v !== 'object' || !str(v.id) || !str(v.name) || !str(v.short) || typeof v.role !== 'string') return null
  return { id: v.id as string, name: v.name as string, short: v.short as string, role: v.role as string }
}

/** Запомнить пользователя (null — выйти). false — storage недоступен, вход только до перезагрузки */
export function writeSession(u: User | null, box: Box | null = localBox()): boolean {
  if (!box) return false
  try {
    if (u) box.setItem(SESSION_KEY, JSON.stringify({ id: u.id, name: u.name, short: u.short, role: u.role }))
    else box.removeItem(SESSION_KEY)
    return true
  } catch {
    return false
  }
}

/** Куда вернуться после входа: путь этого же сайта, кроме /login; всё остальное — «/».
 *  Проверка разбором URL, а не по префиксу: «/\host», «/<TAB>/host» браузер читает как чужой адрес,
 *  а «/.//host» после нормализации становится «//host» — роутер отказался бы переходить и вход «завис» бы */
export function safeNext(v: string | null | undefined, origin: string = globalThis.location?.origin ?? 'http://localhost'): string {
  if (!v || !v.startsWith('/')) return '/'
  try {
    const base = new URL(origin).origin
    const u = new URL(v, base)
    const path = u.pathname + u.search + u.hash
    if (u.origin !== base || new URL(path, base).origin !== base) return '/'
    return u.pathname === '/login' || u.pathname.startsWith('/login/') ? '/' : path
  } catch {
    return '/'
  }
}

/** Вход/выход в другой вкладке — событие storage; возвращает отписку */
export function onSessionChange(cb: (u: User | null) => void): () => void {
  if (typeof window === 'undefined') return () => {}
  const h = (e: StorageEvent) => { if (e.key === SESSION_KEY || e.key === null) cb(readSession()) }
  window.addEventListener('storage', h)
  return () => window.removeEventListener('storage', h)
}
