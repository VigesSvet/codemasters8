import { afterEach, describe, expect, it, vi } from 'vitest'
import { SESSION_KEY, localBox, readSession, safeNext, writeSession, type User } from './session'

const U: User = { id: 'an2', name: 'Омарова Ж.', short: 'ЖО', role: 'Ведущий аналитик · ДЭР ВКО' }

/** Простой storage в памяти — как localStorage */
function memory(init: Record<string, string> = {}) {
  const m = new Map(Object.entries(init))
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => { m.set(k, String(v)) },
    removeItem: (k: string) => { m.delete(k) },
    raw: m,
  }
}

/** storage, который бросает на каждое обращение (приватное окно, запрет cookies) */
const broken = () => ({
  getItem: () => { throw new DOMException('denied', 'SecurityError') },
  setItem: () => { throw new DOMException('quota', 'QuotaExceededError') },
  removeItem: () => { throw new DOMException('denied', 'SecurityError') },
})

afterEach(() => vi.unstubAllGlobals())

describe('сессия входа без пароля', () => {
  it('сохраняет и читает пользователя под ключом saq.analyst', () => {
    const box = memory()
    expect(writeSession(U, box)).toBe(true)
    expect(JSON.parse(box.raw.get(SESSION_KEY)!)).toEqual(U)
    expect(readSession(box)).toEqual(U)
  })

  it('хранит только id, name, short, role', () => {
    const box = memory()
    writeSession({ ...U, fullName: 'Омарова Жанар (демо)' } as User, box)
    expect(Object.keys(JSON.parse(box.raw.get(SESSION_KEY)!)).sort()).toEqual(['id', 'name', 'role', 'short'])
  })

  it('выход удаляет запись', () => {
    const box = memory()
    writeSession(U, box)
    expect(writeSession(null, box)).toBe(true)
    expect(box.raw.has(SESSION_KEY)).toBe(false)
    expect(readSession(box)).toBeNull()
  })

  it('пустой storage — не вошёл', () => {
    expect(readSession(memory())).toBeNull()
  })

  it('испорченный JSON и чужая форма — null, без исключения', () => {
    for (const raw of ['{', 'null', '42', '"Омарова"', '[]', '{"id":"an2"}', '{"id":"an2","name":"","short":"ЖО","role":""}', '{"id":1,"name":"x","short":"x","role":"x"}'])
      expect(readSession(memory({ [SESSION_KEY]: raw }))).toBeNull()
  })

  it('storage бросает — чтение null, запись false, без исключения', () => {
    expect(readSession(broken())).toBeNull()
    expect(writeSession(U, broken())).toBe(false)
    expect(writeSession(null, broken())).toBe(false)
  })

  it('нет storage — вход живёт только в памяти', () => {
    expect(readSession(null)).toBeNull()
    expect(writeSession(U, null)).toBe(false)
  })

  it('доступ к localStorage бросает — localBox() возвращает null', () => {
    const prev = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, get: () => { throw new DOMException('denied', 'SecurityError') } })
    try {
      expect(localBox()).toBeNull()
      expect(readSession()).toBeNull()
      expect(writeSession(U)).toBe(false)
    } finally {
      if (prev) Object.defineProperty(globalThis, 'localStorage', prev)
      else delete (globalThis as { localStorage?: unknown }).localStorage
    }
  })

  it('по умолчанию работает с globalThis.localStorage', () => {
    const box = memory()
    vi.stubGlobal('localStorage', box)
    expect(writeSession(U)).toBe(true)
    expect(readSession()).toEqual(U)
  })
})

describe('safeNext — куда вернуться после входа', () => {
  const O = 'http://localhost:5180'
  it('свой путь — как есть, с параметрами и якорем', () => {
    expect(safeNext('/ads', O)).toBe('/ads')
    expect(safeNext('/complexes/c2?tab=log#top', O)).toBe('/complexes/c2?tab=log#top')
    expect(safeNext('/signals/3/print', O)).toBe('/signals/3/print')
  })

  it('пусто, /login и не путь — на главную', () => {
    for (const v of [null, undefined, '', 'ads', '/login', '/login?next=%2Fads', '/login/', 'https://evil.example/x', 'javascript:alert(1)'])
      expect(safeNext(v, O)).toBe('/')
  })

  it('чужой адрес в любой записи — на главную, а не исключение роутера', () => {
    for (const v of ['//evil.example', '/\\evil.example', '/\\\\evil.example', '/\t/evil.example', '/\n/evil.example', '/\r\n/evil.example',
      '/.//evil.example', '/a/..//evil.example', '/./\\evil.example'])
      expect(safeNext(v, O), JSON.stringify(v)).toBe('/')
  })

  it('из адресной строки: %5C и %09 раскодируются в «\\» и TAB — тоже на главную', () => {
    for (const q of ['next=/%5Cevil.example', 'next=/%09/evil.example', 'next=%2F%2Fevil.example', 'next=/%5C%5Cevil.example'])
      expect(safeNext(new URLSearchParams(q).get('next'), O), q).toBe('/')
  })

  it('результат всегда путь этого же сайта', () => {
    for (const v of ['/ads', '/%2F%2Fevil.example', '/a\\b', '/x?next=//evil.example', '/ads\t'])
      expect(new URL(safeNext(v, O), O).origin).toBe(O)
  })
})
