// Происхождение сведений и открытые данные (ТЗ §4, §7): подписи источников, пометка строк прежнего демо-набора, загрузка реестра.
// Компоненты — components/Provenance.tsx и components/OpenData.tsx.
import { useEffect, useState } from 'react'
import { fmtDate } from '../components/ui'
import type { Complex, Developer, Origin, RegistryList, RegistryObject } from '../data/types'
import { errorText } from './api'
import { fmtStamp } from './audit'
import { useStore, type Mode } from './store'

/** Дата или дата со временем (местное время) — как пришло; нераспознанное — как есть */
export const when = (d?: string | null) => {
  if (!d) return ''
  if (Number.isNaN(new Date(d).getTime())) return d
  return d.length > 10 ? fmtStamp(d) : fmtDate(d)
}

/** Вымышленная сущность прежнего демо-набора — остаётся только в старых БД до `python -m app.demo purge`. ЖК и застройщики без
 *  поля origin (сервер старой версии) — тоже демо. Публикации — демо, только если так помечены */
export const isDemo = (x?: { origin?: Origin | null } | null) => !!x && (x.origin ?? 'demo') === 'demo'
export const isDemoAd = (x?: { origin?: Origin | null } | null) => x?.origin === 'demo'
/** Карточка ЖК или застройщик, созданные по открытому реестру: сведения источника, аналитиком не проверены */
export const isRegistry = (x?: { origin?: Origin | null } | null) => x?.origin === 'registry'
/** ЖК или застройщик, известные только по рекламе: в открытом реестре их нет, сведения рекламы не проверены */
export const isAdvertised = (x?: { origin?: Origin | null } | null) => x?.origin === 'advertised'
/** Застройщик-заглушка у ЖК по рекламе, когда застройщик не установлен */
export const UNKNOWN_DEV = 'ad-unknown'

/** Подпись демо-застройщика: «вымышленная организация (демо)» */
export const DEMO_ORG = 'вымышленная организация (демо)'
/** Перечень ГАСК прежнего демо-набора (shared/testdata/demo_seed.json) — источник отметки «в перечне» у демо-ЖК старых БД */
export const DEMO_PUBLISHED_LIST = { source: 'Instagram ГАСК ВКО (перечень)', updated: '2026-10-01', url: null }
/** Подписи карточек из открытого реестра */
export const REGISTRY_LABEL = 'Открытый реестр'
export const REGISTRY_NOTE = 'сведения из открытого реестра, аналитиком не проверены'
/** Карточка ЖК по рекламе */
export const ADVERTISED_LABEL = 'По рекламе'
export const ADVERTISED_NOTE = 'Нет в открытом реестре разрешений — карточка создана по рекламе, данные не проверены'
/** «нет сведений» — пустое поле карточки из открытого реестра */
export const NO_DATA = 'нет сведений'

/** Ключ объекта открытых данных у карточки ЖК из реестра (id «r-<ключ>») — ссылка на записи источников; иначе null */
export const registryKeyOf = (c?: Pick<Complex, 'id' | 'origin'> | null) =>
  (c && c.origin === 'registry' && c.id.startsWith('r-') ? c.id.slice(2) : null)
/** Стадия для подписей: «сдан», стадия или «стадия не известна» (у карточки из реестра) */
export const stageText = (c: Pick<Complex, 'stage'>) => (c.stage === 'введён' ? 'сдан' : c.stage || 'стадия не известна')
/** «каркас, 58%» или «нет сведений» — готовность у карточки без стадии; «введён» без готовности (отметка аналитика «Объект
 *  сдан» у карточки реестра или рекламы) — без «0%» */
export const progressText = (c: Pick<Complex, 'stage' | 'progress'>) =>
  (!c.stage ? NO_DATA : c.stage === 'введён' && !c.progress ? c.stage : `${c.stage}, ${c.progress}%`)
/** Адрес и район через разделитель; пустой район (карточка из реестра) не печатаем */
export const placeText = (c: Pick<Complex, 'address' | 'district'>, sep = ' · ') => [c.address, c.district].filter(Boolean).join(sep)
/** Портфель застройщика: «4 сдано · 2 строится»; у застройщика из реестра портфель не известен */
export const portfolioText = (d: Pick<Developer, 'objects' | 'unfinished' | 'origin'>, inRegistry?: number) =>
  (d.origin === 'registry' || d.origin === 'advertised'
    ? `портфель не проверен${inRegistry ? ` · объектов ${d.origin === 'registry' ? 'в реестре' : 'на контроле'}: ${inRegistry}` : ''}`
    : `${d.objects - d.unfinished} сдано · ${d.unfinished} строится`)

/** Плашка у реальных объектов (решение 1 SPEC-E): риск, зоны и сигналы для записей источников не считаются */
export const OPEN_DATA_NOTE = 'Сведения из открытого источника; риск не оценивается: нет зафиксированной рекламы и проверенных данных. Оценка появляется в карточке ЖК, когда по нему есть проверенные сведения.'

/** Названия источников по коду; неизвестный код — как есть */
export const SOURCE_NAME: Record<string, string> = {
  homeportal: 'Homeportal — карточки ЖК', homeportal_section: 'Homeportal — отдельный раздел', ashyq: 'Казреестр — CSV Ashyq Data',
  demo: 'Демонстрационный набор', snapshot: 'Стартовый набор (офлайн-копия)',
}
export const SOURCE_SHORT: Record<string, string> = {
  homeportal: 'Homeportal', homeportal_section: 'Homeportal, раздел', ashyq: 'Казреестр', demo: 'Демо', snapshot: 'Офлайн-копия',
}
export const sourceName = (code?: string | null, short?: boolean) => (code ? (short ? SOURCE_SHORT : SOURCE_NAME)[code] ?? code : '—')

/** Способ привлечения средств по коду; исходный текст источника — как есть */
export const FUNDING: Record<string, string> = { guarantee: 'Гарантия ЕО (КЖК)', mio: 'Разрешение МИО', bvu: 'Участие БВУ' }
export const fundingName = (x: string) => FUNDING[x] ?? x

/** Загрузка реестра: mode — в каком режиме получено (с сервера или встроенная копия) */
type RegistryLoad = { mode: Mode; data?: RegistryList; err?: string }

/** Реестр открытых данных: с сервера, а в офлайн-копии — встроенная копия; после импорта и восстановления связи (revision) —
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
    data, err: mine?.err, loading: mode === 'loading' || (enabled && !mine), offline: mode === 'snapshot',
    total: data ? data.objects.length + data.withoutCoords.length : undefined,
    retry: () => setVer((v) => v + 1),
  }
}

/** На карту — только с координатами в черте города: далёкие координаты источника (flags.coordsFarKm) — под вопросом */
export const onMap = (o: RegistryObject) => o.lat != null && o.lng != null && !o.flags?.coordsFarKm
