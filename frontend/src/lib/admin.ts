// Режим администратора подключений (integration-spec §5.1, §13.2): токен SAQ_ADMIN_TOKEN вводит человек, сервер его проверяет.
// Токен живёт только в sessionStorage этой вкладки (до её закрытия) — не в localStorage, не в коде и не в VITE_*-переменных;
// уходит заголовком X-Admin-Token только в маршруты администратора (adminCall) и нигде не логируется.
// Демо-вход аналитика (lib/session.ts) прав администратора не даёт.
import { ApiError, apiGet, isRouteMissing, type ExtraHeaders } from './api'

export const ADMIN_KEY = 'saq.admin'
export const ADMIN_HEADER = 'X-Admin-Token'

/** Нет токена — действие администратора не отправляем */
export const NEED_ADMIN = 'Нужен режим администратора'
export const NO_ADMIN_ROUTE = 'Сервер не поддерживает режим администратора — обновите бэкенд до версии со сбором публикаций'

type Box = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

/** sessionStorage или null, если он недоступен (нет окна, приватный режим, запрет доступа) */
export function sessionBox(): Box | null {
  try {
    return globalThis.sessionStorage ?? null
  } catch {
    return null
  }
}

// Запас на случай недоступного sessionStorage: токен в памяти страницы до перезагрузки
let memory: string | null = null
const listeners = new Set<(active: boolean) => void>()
const emit = (active: boolean) => listeners.forEach((cb) => cb(active))

/** Сохранённый токен администратора этой вкладки; нет или storage недоступен — null */
export function getAdminToken(box: Box | null = sessionBox()): string | null {
  if (box) {
    try {
      const v = box.getItem(ADMIN_KEY)
      if (v) return v
    } catch {
      // storage бросает — ниже запас в памяти
    }
  }
  return memory
}

export const isAdmin = (box: Box | null = sessionBox()) => !!getAdminToken(box)

/** Что не так с введённым токеном (до запроса): пусто или символы, которых не бывает в токене (заголовок HTTP — только ASCII) */
export function tokenProblem(t: string): string | null {
  const s = t.trim()
  if (!s) return 'Введите токен администратора'
  if (!/^[\x21-\x7e]+$/.test(s)) return 'Токен содержит недопустимые символы — скопируйте значение SAQ_ADMIN_TOKEN целиком'
  return null
}

/** Запомнить токен на время вкладки. false — sessionStorage недоступен: токен только в памяти до перезагрузки */
export function setAdminToken(t: string, box: Box | null = sessionBox()): boolean {
  const s = t.trim()
  if (tokenProblem(s)) return false
  let stored = false
  if (box) {
    try {
      box.setItem(ADMIN_KEY, s)
      stored = true
    } catch {
      stored = false
    }
  }
  memory = stored ? null : s
  emit(true)
  return stored
}

/** Выйти из режима администратора (и при отказе сервера: неверный или сменившийся токен) */
export function clearAdminToken(box: Box | null = sessionBox()): void {
  const had = !!getAdminToken(box)
  memory = null
  if (box) {
    try {
      box.removeItem(ADMIN_KEY)
    } catch {
      // нечего удалять
    }
  }
  if (had) emit(false)
}

/** Заголовок для маршрута администратора: {'X-Admin-Token': t} или {} без токена */
export function adminHeaders(box: Box | null = sessionBox()): ExtraHeaders {
  const t = getAdminToken(box)
  return t ? { [ADMIN_HEADER]: t } : {}
}

/** Вход и выход из режима администратора в этой вкладке; слушатель получает только флаг, не токен. Возвращает отписку */
export function onAdminChange(cb: (active: boolean) => void): () => void {
  listeners.add(cb)
  return () => { listeners.delete(cb) }
}

/** Запрос в маршрут администратора: без токена — ApiError(401, «Нужен режим администратора») без обращения к серверу;
 *  401/403 сервера (неверный или сменившийся токен) — токен забываем (onAdminChange(false)) и пробрасываем текст сервера;
 *  маршрута нет — «обновите бэкенд» */
export async function adminCall<T>(run: (headers: ExtraHeaders) => Promise<T>, box: Box | null = sessionBox()): Promise<T> {
  const headers = adminHeaders(box)
  if (!headers[ADMIN_HEADER]) throw new ApiError(401, NEED_ADMIN)
  try {
    return await run(headers)
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) clearAdminToken(box)
    if (isRouteMissing(e)) throw new ApiError((e as ApiError).status, NO_ADMIN_ROUTE, (e as ApiError).data)
    throw e
  }
}

/** Проверить токен на сервере (GET /api/admin/check), не сохраняя его. Отклоняется ApiError с русским текстом:
 *  403 «Неверный токен администратора», 503 «Администрирование подключений не настроено…», 0 — нет связи */
export async function checkAdmin(token: string): Promise<void> {
  const problem = tokenProblem(token)
  if (problem) throw new ApiError(-1, problem)
  try {
    const got = await apiGet<{ ok?: boolean }>('/admin/check', { [ADMIN_HEADER]: token.trim() })
    if (!got || got.ok !== true) throw new ApiError(-1, 'Сервер не подтвердил токен администратора')
  } catch (e) {
    if (isRouteMissing(e)) throw new ApiError((e as ApiError).status, NO_ADMIN_ROUTE, (e as ApiError).data)
    throw e
  }
}

/** «Войти» в режим администратора: проверка на сервере, затем токен — в sessionStorage этой вкладки */
export async function signInAdmin(token: string, box: Box | null = sessionBox()): Promise<void> {
  await checkAdmin(token)
  setAdminToken(token, box)
}
