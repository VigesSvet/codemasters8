import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ads as demoAds, complexes as demoComplexes } from '../data/demo'
import type { Ad, AdStatus, Complex } from '../data/types'

interface Store {
  ads: Ad[]
  complexes: Complex[]
  setAdStatus: (id: string, s: AdStatus, assignee?: string) => void
  addAd: (a: Ad) => void
  toast: string | null
  notify: (title: string) => void
}

const Ctx = createContext<Store | null>(null)

const API = import.meta.env.VITE_API_URL ?? '/api'

export function StoreProvider({ children }: { children: ReactNode }) {
  const [ads, setAds] = useState<Ad[]>(demoAds)
  const [complexes, setComplexes] = useState<Complex[]>(demoComplexes)
  const [toast, setToast] = useState<string | null>(null)

  // Если бэкенд FastAPI запущен — берём данные оттуда, иначе работаем на демо-наборе.
  useEffect(() => {
    fetch(`${API}/complexes`).then((r) => (r.ok ? r.json() : Promise.reject())).then(setComplexes).catch(() => {})
    fetch(`${API}/ads`).then((r) => (r.ok ? r.json() : Promise.reject())).then(setAds).catch(() => {})
  }, [])

  const value = useMemo<Store>(() => ({
    ads, complexes, toast,
    setAdStatus: (id, status, assignee) => {
      setAds((xs) => xs.map((a) => (a.id === id ? { ...a, status, assignee: assignee ?? a.assignee } : a)))
      fetch(`${API}/ads/${id}/status`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status, assignee }) }).catch(() => {})
    },
    addAd: (a) => {
      setAds((xs) => [a, ...xs])
      fetch(`${API}/ads`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(a) }).catch(() => {})
    },
    notify: (title) => { setToast(title); setTimeout(() => setToast(null), 2800) },
  }), [ads, complexes, toast])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useStore() {
  const s = useContext(Ctx)
  if (!s) throw new Error('StoreProvider missing')
  return s
}
