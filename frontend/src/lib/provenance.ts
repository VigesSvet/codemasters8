// Происхождение сведений и открытые данные (ТЗ §4, §7): подписи источников, признаки демо, загрузка реестра.
// Компоненты — components/Provenance.tsx и components/OpenData.tsx.
import { useEffect, useState } from 'react'
import { fmtDate } from '../components/ui'
import type { Origin, RegistryList, RegistryObject } from '../data/types'
import { errorText } from './api'
import { fmtStamp } from './audit'
import { useStore, type Mode } from './store'

/** Дата или дата со временем (местное время) — как пришло; нераспознанное — как есть */
export const when = (d?: string | null) => {
  if (!d) return ''
  if (Number.isNaN(new Date(d).getTime())) return d
  return d.length > 10 ? fmtStamp(d) : fmtDate(d)
}

/** Вымышленная сущность сида. ЖК и застройщики бывают только демо (реальные объекты — отдельный слой открытых данных):
 *  без поля origin — тоже демо. Публикации — демо, только если так помечены (добавленная аналитиком — origin 'analyst') */
export const isDemo = (x?: { origin?: Origin | null } | null) => !!x && (x.origin ?? 'demo') === 'demo'
export const isDemoAd = (x?: { origin?: Origin | null } | null) => x?.origin === 'demo'

/** Подпись демо-застройщика: «вымышленная организация (демо)» */
export const DEMO_ORG = 'вымышленная организация (демо)'

/** Плашка у реальных объектов (решение 1 SPEC-E): риск, зоны и сигналы для них не считаются */
export const OPEN_DATA_NOTE = 'Сведения из открытого источника; риск не оценивается: нет зафиксированной рекламы и проверенных данных. Сценарии нарушений — только на вымышленных ЖК.'

/** Названия источников по коду; неизвестный код — как есть */
export const SOURCE_NAME: Record<string, string> = {
  homeportal: 'Homeportal — карточки ЖК', homeportal_section: 'Homeportal — отдельный раздел', ashyq: 'Казреестр — CSV Ashyq Data',
  demo: 'Демонстрационный набор',
}
export const SOURCE_SHORT: Record<string, string> = { homeportal: 'Homeportal', homeportal_section: 'Homeportal, раздел', ashyq: 'Казреестр', demo: 'Демо' }
export const sourceName = (code?: string | null, short?: boolean) => (code ? (short ? SOURCE_SHORT : SOURCE_NAME)[code] ?? code : '—')

/** Способ привлечения средств по коду; исходный текст источника — как есть */
export const FUNDING: Record<string, string> = { guarantee: 'Гарантия ЕО (КЖК)', mio: 'Разрешение МИО', bvu: 'Участие БВУ' }
export const fundingName = (x: string) => FUNDING[x] ?? x

/** Загрузка реестра: mode — в каком режиме получено (с сервера или встроенная копия) */
type RegistryLoad = { mode: Mode; data?: RegistryList; err?: string }

/** Реестр открытых данных: с сервера, а в демо — встроенная копия; после импорта и восстановления связи (revision) —
 *  перечитывается. enabled = false — не загружать (слой выключен). offline — показана встроенная копия */
export function useRegistry(enabled = true) {
  const { mode, registry, revision } = useStore()
  const [load, setLoad] = useState<RegistryLoad | null>(null)
  const [ver, setVer] = useState(0)
  useEffect(() => {
    if (mode === 'loading' || !enabled) return
    let alive = true
    registry().then(
      (data) => { if (alive) setLoad({ mode, data }) },
      (e: unknown) => { if (alive) setLoad((l) => ({ mode, data: l?.mode === mode ? l.data : undefined, err: errorText(e) })) },
    )
    return () => { alive = false }
  }, [mode, enabled, ver, revision]) // eslint-disable-line react-hooks/exhaustive-deps
  const mine = load?.mode === mode ? load : null
  const data = mine?.data
  return {
    data, err: mine?.err, loading: mode === 'loading' || (enabled && !mine), offline: mode === 'demo',
    total: data ? data.objects.length + data.withoutCoords.length : undefined,
    retry: () => setVer((v) => v + 1),
  }
}

/** На карту — только с координатами в черте города: далёкие координаты источника (flags.coordsFarKm) — под вопросом */
export const onMap = (o: RegistryObject) => o.lat != null && o.lng != null && !o.flags?.coordsFarKm
