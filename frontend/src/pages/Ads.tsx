import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Origin } from '../components/Provenance'
import { FilterPop, Highlight, PageTitle, PlatformIcon, SafeImg, Score, StatusTag, adKey, fmtDT, platformMeta, plural, statusMeta } from '../components/ui'
import type { Ad, AdStatus, Platform, Zone } from '../data/types'
import { useSearchText } from '../lib/find'
import { adImage } from '../lib/media'
import { from } from '../lib/nav'
import { platformName } from '../lib/platform'
import { isDemoAd } from '../lib/provenance'
import { linkConfirmed } from '../lib/risk'
import { matches, norm } from '../lib/search'
import { useStore } from '../lib/store'
import { IcClose, IcPlus } from '../ui/icons'

const ORDER: AdStatus[] = ['new', 'needs_check', 'in_review', 'confirmed', 'dismissed']
const ZONES: Zone[] = ['red', 'yellow', 'green']
type Sort = 'risk' | 'date' | 'reach'

/** Собрана подключённым источником или прислана жителем через бота (не ручной ввод) */
const collected = (a: Ad) => a.origin === 'collected'
/** Метка происхождения на фото: собрано сборщиком, прислано через бота, добавлено аналитиком */
const originLabel = (a: Ad) => (collected(a) ? (a.source === 'bot' ? 'прислано через бота' : 'собрано автоматически') : 'добавлено аналитиком')
/** Состояние анализа собранной публикации в строке ленты; анализ готов или его нет — без метки.
 *  Не выполнен — «анализ не выполнен», а не «риска нет»: оценка тогда посчитана только по тексту */
const ANALYSIS_TAG: Record<'queued' | 'running' | 'failed', [string, string, string]> = {
  queued: ['gray', 'анализ в очереди', 'Анализ публикации на сервере ещё не начался — оценка пока только по тексту'],
  running: ['blue', 'идёт анализ', 'Сервер распознаёт файлы публикации — оценка пока только по тексту'],
  failed: ['yellow', 'анализ не выполнен', 'Анализ публикации не выполнен — оценка только по тексту, проверьте вручную'],
}

/** Лента рекламы: фильтры сверху, строки с фото */
export default function Ads() {
  const { ads, complexes, adScoreOf } = useStore()
  // поиск, фильтры и сортировка — в адресе: «назад» из публикации возвращает в ту же выборку
  const [sp, setSp] = useSearchParams()
  const put = (patch: Record<string, string | string[]>) => setSp((p) => {
    const n = new URLSearchParams(p)
    for (const [k, v] of Object.entries(patch)) { const x = Array.isArray(v) ? v.join(',') : v; if (x) n.set(k, x); else n.delete(k) }
    return n
  }, { replace: true })
  const list = (k: string) => sp.get(k)?.split(',').filter(Boolean) ?? []
  const [q, setQ] = useSearchText()
  const plat = list('plat').filter((p): p is Platform => p in platformMeta)
  const status = list('status').filter((x): x is AdStatus => ORDER.includes(x as AdStatus))
  const zone = list('zone').filter((z): z is Zone => ZONES.includes(z as Zone))
  const chip = sp.get('chip') ?? ''
  const sort: Sort = sp.get('sort') === 'date' || sp.get('sort') === 'reach' ? sp.get('sort') as Sort : 'risk'
  const setPlat = (v: Platform[]) => put({ plat: v })
  const setStatus = (v: AdStatus[]) => put({ status: v })
  const setZone = (v: Zone[]) => put({ zone: v })
  const setChip = (v: string) => put({ chip: v })
  const setSort = (v: Sort) => put({ sort: v === 'risk' ? '' : v })

  const all = useMemo(() => ads.map((a) => { const c = complexes.find((x) => x.id === a.complexId); return { a, c, s: adScoreOf(a) } }), [ads, complexes, adScoreOf])
  type R = (typeof all)[number]
  const chips: [string, string, (x: R) => boolean][] = [
    ['queue', 'Ждут решения', (x) => x.a.status === 'new' || x.a.status === 'needs_check'],
    ['nocx', 'ЖК не определён', (x) => !x.c],
    ['hyp', 'Привязка — гипотеза ИИ', (x) => !!x.c && !linkConfirmed(x.a)],
    ['ocr', 'Есть текст на фото', (x) => !!x.a.ocr],
    ['manual', 'Добавлены аналитиком', (x) => !isDemoAd(x.a) && (x.a.origin === 'analyst' || x.a.source === 'manual')],
    ['collected', 'Собраны автоматически', (x) => collected(x.a)],
    ['noai', 'Анализ не выполнен', (x) => x.a.processing?.status === 'failed'],
  ]
  // «Анализ не выполнен» — только когда такие есть (или фильтр уже выбран в адресе)
  const shownChips = chips.filter(([k, , fn]) => k !== 'noai' || chip === k || all.some(fn))
  const anyCollected = ads.some(collected)
  // публикации прежнего демо-набора — только в старых БД (до python -m app.demo purge)
  const anyDemo = ads.some(isDemoAd)
  const match = (x: R, f: { plat?: Platform[]; status?: AdStatus[]; zone?: Zone[] } = {}) => {
    const p = f.plat ?? plat, st = f.status ?? status, z = f.zone ?? zone
    return (!q || matches(q, adKey(x.a.id), x.a.account, x.c?.name) || norm(x.a.text).includes(norm(q)))
      && (!p.length || p.includes(x.a.platform)) && (!st.length || st.includes(x.a.status)) && (!z.length || z.includes(x.s.zone))
      && (!chip || chips.find((c) => c[0] === chip)![2](x))
  }
  const rows = all.filter((x) => match(x)).sort((x, y) => sort === 'risk' ? y.s.score - x.s.score : sort === 'date' ? y.a.date.localeCompare(x.a.date) : (y.a.reach ?? 0) - (x.a.reach ?? 0))
  const plats = (Object.keys(platformMeta) as Platform[]).filter((p) => ads.some((a) => a.platform === p))
  // строку поиска очищает её крестик; «Сбросить» — когда выбран хоть один фильтр
  const back = from('Реклама', `/ads${sp.toString() ? `?${sp}` : ''}`)
  const dirty = !!(plat.length || status.length || zone.length || chip)

  return (
    <>
      <div className="fbar" style={{ marginTop: 12 }}>
        {dirty && <><button className="fbtn gray" onClick={() => setQ('', { plat: '', status: '', zone: '', chip: '' })}><IcClose />Сбросить</button><span className="vsep" /></>}
        <FilterPop label="Площадка" title="Где опубликовано" multi value={plat} onChange={setPlat} count={(v) => all.filter((x) => match(x, { plat: v })).length}
          options={plats.map((p) => [p, <span key={p} className="row" style={{ gap: 8 }}><PlatformIcon p={p} size={20} />{platformMeta[p].name}</span>] as [Platform, React.ReactNode])} />
        <FilterPop label="Статус" title="Статус проверки" multi value={status} onChange={setStatus} count={(v) => all.filter((x) => match(x, { status: v })).length}
          options={ORDER.map((s) => [s, statusMeta[s].name] as [AdStatus, string])} />
        <FilterPop label="Оценка" title="Оценка публикации" multi value={zone} onChange={setZone} count={(v) => all.filter((x) => match(x, { zone: v })).length}
          options={[['red', 'Красная'], ['yellow', 'Жёлтая'], ['green', 'Зелёная']]} />
        <label className="fsearch">
          <input placeholder="Текст объявления, аккаунт или ЖК" value={q} onChange={(e) => setQ(e.target.value)} />
          {q && <button className="c3" onClick={() => setQ('')}><IcClose size={16} /></button>}
        </label>
      </div>

      <PageTitle title="Реклама новостроек в Усть-Каменогорске"
        sub={<>Подсветка: <mark>продажа</mark>, <mark className="fund">предложение денег (R2)</mark>, <mark className="safe">признаки законности</mark></>}
        actions={<Link to="/ads/new" className="btn y m"><IcPlus />Добавить вручную</Link>} />
      <p className="t-sm c2 row wrap-f" style={{ gap: 8, marginTop: -12, marginBottom: 16 }}>{anyDemo && <Origin kind="demo" label="демо" />}
        <span>{anyDemo && 'Публикации прежнего демо-набора вымышлены. '}{anyCollected ? 'Публичная реклама, собранная подключёнными источниками, помечена «собрано автоматически»: привязку к ЖК предлагает ИИ, решает аналитик. Оценка — предварительная, не вывод о нарушении.'
          : <>Автоматический сбор пока не дал публикаций — подключения и их состояние на странице <Link className="link" to="/sources">«Источники»</Link>.</>}</span></p>
      <div className="chips" style={{ marginTop: -8, marginBottom: 24 }}>
        {shownChips.map(([k, l, fn]) => <button key={k} className={`chip ${chip === k ? 'on' : ''}`} onClick={() => setChip(chip === k ? '' : k)}>{l}<span className="n">{all.filter(fn).length}</span></button>)}
      </div>

      <div className="sorter">
        <b>{rows.length} {plural(rows.length, ['публикация', 'публикации', 'публикаций'])}:</b>
        {([['risk', 'по риску'], ['date', 'по дате'], ['reach', 'по охвату']] as const).map(([k, l]) => <button key={k} className={sort === k ? 'on' : ''} onClick={() => setSort(k)}>{l}</button>)}
      </div>

      {rows.length === 0 && <div className="empty">Ничего не найдено</div>}
      <div className="fade">
        {rows.map(({ a, c, s }) => {
          const img = adImage(a)
          const st = a.processing?.status
          const at = st === 'queued' || st === 'running' || st === 'failed' ? ANALYSIS_TAG[st] : null
          return (
            <Link key={a.id} to={`/ads/${a.id}`} state={back} className="offer">
              <div className="ph">
                <SafeImg src={img} alt="" fallback={<div className="noimg"><PlatformIcon p={a.platform} size={48} /></div>} />
                <div className="tl"><span className="tag white">{platformName(a.platform)}</span>{isDemoAd(a) ? <Origin kind="demo" label="демо" onPhoto title="Демонстрационная публикация: текст и аккаунт вымышлены" />
                  : <span className="tag white" title={collected(a) && a.collectedAt ? `Получено сервером ${fmtDT(a.collectedAt)}` : undefined}>{originLabel(a)}</span>}</div>
              </div>
              <div className="body">
                <div className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={20} /><span className="bold">{a.account}</span><span className="c2 t-sm">{fmtDT(a.date)}</span></div>
                <div className="nm">{c ? `ЖК «${c.name}»` : 'ЖК не определён'}</div>
                {c && !linkConfirmed(a) && <span className="tag yellow" style={{ alignSelf: 'flex-start' }} title="ЖК предложил ИИ, аналитик привязку не подтвердил — в балл объекта не входит">привязка — гипотеза ИИ {Math.round((a.matchConfidence || 0) * 100)}%</span>}
                <p className="txt clamp3"><Highlight text={a.text} /></p>
                <div className="row wrap-f" style={{ gap: 6, marginTop: 'auto' }}>
                  <StatusTag s={a.status} />
                  {a.price && <span className="tag gray">{a.price}</span>}
                  {a.ocr && <span className="tag gray">текст на фото</span>}
                  {at && <span className={`tag ${at[0]}`} title={at[2]}>{at[1]}</span>}
                </div>
              </div>
              <div className="side">
                <div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}><span className="c2 t-sm">оценка</span><Score value={s.score} zone={s.zone} /></div>
                <div className="t-sm c2" style={{ textAlign: 'right' }}>{a.reach ? `охват ~${(a.reach / 1000).toFixed(1)} тыс.` : 'охват неизвестен'}</div>
                <div className="t-xs c3" style={{ textAlign: 'right' }}>{adKey(a.id)}</div>
                <span className="btn g m" style={{ marginTop: 'auto' }}>Разобрать</span>
              </div>
            </Link>
          )
        })}
      </div>
    </>
  )
}
