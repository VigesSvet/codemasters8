import { useEffect, useState } from 'react'
import { getRoutes, type RouteSummary } from './route'
import { useStore } from './store'

/** Краткий маршрут всех сигналов (этап, срок, просрочка) для реестра /signals и время загрузки — от него считается
 *  «осталось / просрочено» в колонке «Срок»; null — пока не загружен или нет сервера (ошибка — без баннера: колонка пустая) */
export function useRoutes(): { rows: Map<number, RouteSummary>; at: number } | null {
  const { mode, signals } = useStore()
  const [res, setRes] = useState<{ rows: Map<number, RouteSummary>; at: number } | null>(null)
  useEffect(() => {
    if (mode !== 'api') return
    let alive = true
    getRoutes().then((rs) => alive && setRes({ rows: new Map(rs.map((r) => [r.signalId, r])), at: Date.now() }), () => alive && setRes(null))
    return () => { alive = false }
  }, [mode, signals])
  return res
}
