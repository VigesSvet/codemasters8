import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigationType, useParams, useSearchParams } from 'react-router-dom'
import { AuditList } from '../components/Audit'
import Compare from '../components/Compare'
import { FactorList, ObservationList } from '../components/Factors'
import MapView from '../components/MapView'
import { DocSourceLine, Origin } from '../components/Provenance'
import SignalPanel from '../components/SignalPanel'
import { Cell, Gauge, Highlight, Modal, NeedsCheck, PageTitle, PlatformIcon, Seg, StatusTag, TriTag, ZoneTag, fmtDate, fmtDT, plural, zoneShort } from '../components/ui'
import { publishedListMeta } from '../data/demo'
import type { AppealStatus, AuditEvent, BasisDoc, BasisSource, ReadinessValue, RiskResult, Stage, Tri } from '../data/types'
import { errorText } from '../lib/api'
import { AUDIT_GROUPS, REQUEST_TOPICS, inGroup, type AuditGroup, type RequestTopic } from '../lib/audit'
import { from, useFrom } from '../lib/nav'
import { DEMO_ORG, isDemo, isDemoAd } from '../lib/provenance'
import { linkConfirmed, model } from '../lib/risk'
import { useHeadHeight } from '../lib/sticky'
import { useStore } from '../lib/store'
import { IcBuilding, IcCalendar, IcCamera, IcCheck, IcClose, IcCrane, IcDoc, IcHelp, IcMap, IcShield, IcUsers, IcWorker } from '../ui/icons'

const STAGES: Stage[] = ['котлован', 'фундамент', 'каркас', 'кровля', 'отделка', 'введён']
const TABS = ['basis', 'risk', 'ads', 'photos', 'dev', 'log'] as const
type Tab = (typeof TABS)[number]
const COMMENT_MAX = 2000
/** Журнал объекта с сервера: id — чей; при ошибке перечитывания остаются прежние события */
type Log = { id: string; events?: AuditEvent[]; err?: string }
/** Темы запроса, которые подсказывает риск: по ним «Нужна проверка» */
function suggestTopics(r: RiskResult): RequestTopic[] {
  const unknown = (code: string) => [...r.factors, ...r.observations].some((f) => f.code === code && f.state === 'unknown')
  return ([['R1', 'basis'], ['R5', 'readiness'], ['R4', 'appeals'], ['O1', 'list']] as const).filter(([code]) => unknown(code)).map(([, t]) => t)
}
const APPEAL: Record<AppealStatus, [string, string]> = { verified: ['проверено', 'red'], pending: ['на проверке', 'yellow'], rejected: ['отклонено', 'gray'] }
const quarter = (d: string) => { const x = new Date(d); return `${['I', 'II', 'III', 'IV'][Math.floor(x.getMonth() / 3)]} кв. ${x.getFullYear()}` }
const points = (n: number) => `${n} ${plural(n, ['балл', 'балла', 'баллов'])}`
/** Где была страница: «назад» с публикации возвращает к тому же месту (ключ — запись истории) */
const scrolls = new Map<string, number>()

/** Карточка ЖК по образцу страницы новостройки на realty.yandex.ru */
export default function ComplexCard() {
  const { id } = useParams()
  const [sp, setSp] = useSearchParams()
  const loc = useLocation()
  const navType = useNavigationType()
  const back = useFrom()
  useHeadHeight()
  const { mode, complexes, ads, developers, appeals, notify, riskOf, getAudit, requestInfo, user, revision } = useStore()
  const tab: Tab = TABS.find((t) => t === sp.get('tab')) ?? 'basis'
  const tabsRef = useRef<HTMLDivElement>(null)
  const arrived = useRef<string>(undefined)
  const [ph, setPh] = useState(0)
  const [log, setLog] = useState<Log | null>(null)
  const [ver, setVer] = useState(0)
  const [group, setGroup] = useState<AuditGroup>('all')
  const [req, setReq] = useState<{ topics: RequestTopic[]; comment: string; busy?: boolean; err?: string } | null>(null)
  const live = mode === 'api'
  const exists = complexes.some((x) => x.id === id)
  // вкладка — в адресе (replace): «назад» со страницы публикации открывает её же; «откуда пришли» (state) не теряется
  const setTab = (t: Tab) => setSp((p) => { const n = new URLSearchParams(p); if (t === 'basis') n.delete('tab'); else n.set('tab', t); return n }, { replace: true, state: loc.state })
  /** Переключить вкладку и прокрутить к ней — для ссылок вне вкладок (правая колонка, галерея) */
  const goTab = (t: Tab) => { setTab(t); requestAnimationFrame(() => tabsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })) }

  // журнал объекта: данные и их источники, публикации, сигналы, запросы — с сервера; несуществующий ЖК не запрашиваем.
  // revision — после сохранённых изменений и восстановления связи перечитываем
  useEffect(() => {
    if (!live || !id || !exists) return
    let alive = true
    getAudit({ complexId: id }).then(
      (events) => { if (alive) setLog({ id, events }) },
      (e: unknown) => { if (alive) setLog((l) => ({ id, events: l?.id === id ? l.events : undefined, err: errorText(e) })) },
    )
    return () => { alive = false }
  }, [id, live, exists, ver, revision]) // eslint-disable-line react-hooks/exhaustive-deps

  // Вход на страницу: «назад»/«вперёд» — туда, где она была; ссылка на вкладку (?tab=risk) — к вкладкам.
  // Таймер — после оболочки: она прокручивает новую страницу наверх
  useEffect(() => {
    if (!exists || arrived.current === id) return
    const y = navType === 'POP' ? scrolls.get(loc.key) : undefined
    if (y === undefined && tab === 'basis') { arrived.current = id; return }
    const t = setTimeout(() => { arrived.current = id; if (y !== undefined) window.scrollTo(0, y); else tabsRef.current?.scrollIntoView({ block: 'start' }) }, 0)
    return () => clearTimeout(t)
  }, [id, exists]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const save = () => scrolls.set(loc.key, window.scrollY)
    window.addEventListener('scroll', save, { passive: true })
    return () => window.removeEventListener('scroll', save)
  }, [loc.key])

  const c = complexes.find((x) => x.id === id)
  if (!c) return (
    <>
      <PageTitle crumbs={back ? [[`← ${back.label}`, back.to], ['Объект']] : [['Карта', '/'], ['Новостройки', '/complexes'], ['Объект']]}
        title={mode === 'loading' ? 'Карточка ЖК' : 'Объект не найден'} />
      <div className="empty">{mode === 'loading' ? 'Загружаем данные…' : (
        <>ЖК с кодом «{id}» нет {mode === 'demo' ? 'в демо-наборе' : 'на сервере'} — возможно, ссылка устарела или объект удалён.<br />
          <Link to="/complexes" className="btn d m" style={{ marginTop: 16 }}>К списку новостроек</Link></>
      )}</div>
    </>
  )
  const r = riskOf(c)
  const hitF = r.factors.filter((f) => f.state === 'hit')
  /** Ссылки на публикации передают, откуда пришли: разбор публикации покажет «← ЖК «…»» и вернёт на ту же вкладку */
  const here = (t: Tab) => ({ label: `ЖК «${c.name}»`, to: `/complexes/${c.id}?tab=${t}` })
  const dev = developers.find((d) => d.id === c.developerId)
  const linked = ads.filter((a) => a.complexId === c.id)
  const own = appeals.filter((a) => a.complexId === c.id).sort((a, b) => b.date.localeCompare(a.date))
  const fac = (...codes: string[]) => r.factors.filter((f) => codes.includes(f.code))
  const unknown = r.factors.filter((f) => f.state === 'unknown')
  const o1 = r.observations.find((o) => o.code === 'O1')
  const b = c.basis
  const demoCx = isDemo(c)
  // контрольный сценарий ТЗ §5 на этом объекте (A — c14, B — c2, C — c10)
  const kase = model.cases.find((k) => k.complexId === c.id)
  /** Где и когда сверен документ: своя запись; у установленного статуса без своей — общая сверка; иначе «не проверялся» */
  const docProv = (k: BasisDoc, v: Tri) => {
    const own = b.sources?.[k]
    const general: BasisSource | null = !own && v !== 'unknown' && b.source ? { source: b.source, checkedAt: b.checkedAt ?? '', url: null } : null
    return <DocSourceLine src={own ?? general} general={!!general} />
  }
  const listSrc: BasisSource = { source: publishedListMeta.source, checkedAt: publishedListMeta.updated, url: publishedListMeta.url ?? null }
  const rd = c.readiness ?? { declared: null, confirmed: null }
  // округление как в модели (п.п. до сотых, половина — вверх)
  const gap = rd.declared && rd.confirmed ? Math.floor(Math.abs(rd.declared.value - rd.confirmed.value) * 100 + 0.5) / 100 : null
  // только снимки этого объекта: общие кадры города выдавали бы чужую площадку за эту (ТЗ §7 «Достоверность»)
  const photos = [c.cover, ...c.photos.map((p) => p.src)].filter((v, i, a) => v && a.indexOf(v) === i).slice(0, 5)
  const stageIdx = STAGES.indexOf(c.stage)
  const events = live && log?.id === c.id ? log.events ?? [] : []
  const logReady = live && log?.id === c.id && !!log.events
  // новые сверху
  const shownLog = events.filter((e) => inGroup(e, group)).reverse()
  const count = (g: AuditGroup) => events.filter((e) => inGroup(e, g)).length

  const openReq = () => setReq({ topics: suggestTopics(r), comment: '' })
  const toggleTopic = (t: RequestTopic) => setReq((q) => q && { ...q, err: undefined, topics: q.topics.includes(t) ? q.topics.filter((x) => x !== t) : [...q.topics, t] })
  const reqValid = !!req && req.topics.length > 0 && (!req.topics.includes('other') || !!req.comment.trim())
  const sendReq = () => {
    if (!req || !reqValid || req.busy) return
    setReq({ ...req, busy: true, err: undefined })
    // топики — в порядке списка, как их прочтёт журнал
    const topics = REQUEST_TOPICS.map(([t]) => t).filter((t) => req.topics.includes(t))
    requestInfo(c.id, topics, req.comment).then(() => {
      setReq(null)
      notify('Запрос записан в журнал объекта (во внешние органы не отправляется)')
      setGroup('all')
      goTab('log') // журнал перечитается сам: запрос сохранён — revision выросла
    }, (e: unknown) => setReq((q) => q && { ...q, busy: false, err: errorText(e) }))
  }

  const basis = (title: string, v: Tri, doc?: string, note?: string, tag?: string, prov?: React.ReactNode) => (
    <div className="promo-tile">
      <div className="row between">
        <span className={`zone-pic ${v === 'yes' ? 'green' : v === 'no' ? 'red' : 'gray'}`} style={{ width: 32, height: 32 }}>{v === 'yes' ? <IcCheck size={16} /> : v === 'no' ? <IcClose size={16} /> : <IcHelp size={16} />}</span>
        <div className="row" style={{ gap: 6 }}>{tag && <span className="tag outline">{tag}</span>}<TriTag v={v} /></div>
      </div>
      <div className="h" style={{ marginTop: 6 }}>{title}</div>
      <div className="d">{doc ?? (v === 'unknown' ? 'Сведения не получены — запросить у уполномоченного органа' : 'Документ не найден')}</div>
      {note && <div className="d" style={{ marginTop: 'auto', color: 'var(--text-3)' }}>{note}</div>}
      {prov && <div style={{ marginTop: note ? 6 : 'auto', paddingTop: 4 }}>{prov}</div>}
    </div>
  )
  const ready = (title: string, v: ReadinessValue | null) => (
    <div className={`promo-tile ${v ? '' : 'empty-v'}`}>
      <div className="d">{title}</div>
      <span className="big">{v ? `${v.value}%` : '—'}</span>
      <div className="d">{v ? <>{v.source || 'источник не указан'} · {v.date ? fmtDate(v.date) : 'дата не указана'}</> : 'нет данных'}</div>
    </div>
  )

  return (
    <>
      <PageTitle crumbs={back ? [[`← ${back.label}`, back.to], [`ЖК «${c.name}»`]] : [['Карта', '/'], ['Новостройки', '/complexes'], [`ЖК «${c.name}»`]]}
        title={<span className="row wrap-f" style={{ gap: 14 }}>ЖК «{c.name}» <ZoneTag zone={r.zone} />{demoCx && <Origin kind="demo" label="Демо-сценарий (вымышленный ЖК)" />}</span>}
        sub={<>{c.address}, Усть-Каменогорск · <span className="row" style={{ display: 'inline-flex', gap: 4 }}><IcMap size={14} />{c.district}</span></>} />
      {kase && (
        <div className="case-banner">
          <span className="tag dark">Сценарий {kase.id}</span>
          <span className="grow"><b>Демо-сценарий {kase.id} из ТЗ §5:</b> {kase.title} · ожидание: {zoneShort[kase.zone].toLowerCase()} зона, {points(kase.score)}</span>
          {r.score === kase.score && r.zone === kase.zone
            ? <span className="tag green"><IcCheck size={12} strokeWidth={3} />сейчас совпадает</span>
            : <span className="tag yellow" title="Данные объекта изменились после демо-набора">сейчас {points(r.score)}</span>}
          <Link to="/method#cases" className="link t-sm bold">Сценарии в методике</Link>
        </div>
      )}

      <div className="cols-r">
        <div>
          <div className="gallery">
            <div className="ph main-ph">
              <img src={photos[ph]} alt="" />
              <div className="tl"><span className="tag white">{c.stage === 'введён' ? 'сдан' : 'строится'}</span>{r.needsCheck && <span className="tag white">нужна проверка</span>}{c.idleDays >= 30 && <span className="tag white">стоит {c.idleDays} дн.</span>}</div>
            </div>
            <div className="thumbs">
              {photos.map((p, i) => (
                <button key={p} className={ph === i ? 'on' : ''} onClick={() => setPh(i)}>
                  <img src={p} alt="" />
                  {i === photos.length - 1 && c.photos.length > 1 && <span className="more" onClick={(e) => { e.stopPropagation(); goTab('photos') }}>Снимки · {c.photos.length}</span>}
                </button>
              ))}
            </div>
          </div>

          <h2 className="t-h2" style={{ margin: '40px 0 16px' }}>Ход строительства</h2>
          <div className="steps">
            {STAGES.map((s, i) => <div key={s} className={i < stageIdx ? 'done' : i === stageIdx ? 'cur' : ''} style={i === stageIdx ? ({ '--p': `${Math.max(15, c.progress)}%` } as React.CSSProperties) : undefined}>{s[0].toUpperCase() + s.slice(1)}</div>)}
          </div>

          <div className="subtabs" ref={tabsRef} style={{ marginTop: 40, scrollMarginTop: 'calc(var(--yhead, 113px) + 19px)' }}>
            {([['basis', 'Основания'], ['risk', 'Риск'], ['ads', 'Реклама', linked.length], ['photos', 'Снимки', c.photos.length], ['dev', 'Застройщик'], ['log', 'Журнал', logReady ? events.length : undefined]] as const).map(([k, l, n]) => (
              <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}{n !== undefined && <span className="n">{n}</span>}</button>
            ))}
          </div>

          {tab === 'basis' && (
            <div className="fade">
              <h2 className="t-h2" style={{ marginBottom: 16 }}>Основание привлечения средств</h2>
              <div className="promo-tiles" style={{ gridTemplateColumns: 'repeat(2, 1fr)' }}>
                {basis('Гарантия ЕО (КЖК)', c.basis.guarantee, c.basis.guaranteeRef, undefined, undefined, docProv('guarantee', c.basis.guarantee))}
                {basis('Разрешение МИО', c.basis.mio, c.basis.mioRef, c.basis.guarantee === 'yes' && c.basis.mio === 'no' ? 'При действующей гарантии ЕО отсутствие отдельного разрешения МИО — не R1' : undefined, undefined, docProv('mio', c.basis.mio))}
                {basis('Банк второго уровня', c.basis.bvu, c.basis.bank, undefined, undefined, docProv('bvu', c.basis.bvu))}
                {basis('Перечень ГАСК', c.inPublishedList, c.inPublishedList === 'unknown' ? o1?.text : c.inPublishedList === 'yes' ? 'Объект есть в перечне' : 'Объекта нет в перечне',
                  o1?.state === 'flag' ? o1.text : undefined, 'вне балла · наблюдение O1',
                  c.inPublishedList === 'unknown' ? <DocSourceLine src={null} none="Не сверялся с перечнем" /> : <DocSourceLine src={listSrc} />)}
              </div>
              <div className={`src-line ${b.source && b.checkedAt ? '' : 'none'}`}>
                <IcDoc size={16} />
                <span>{b.source
                  ? <>Общая сверка оснований: {b.source} · {b.checkedAt ? `сверено ${fmtDate(b.checkedAt)}` : 'дата сверки не указана'}. Источник и дата по каждому документу — на плитках</>
                  : 'Источник сверки не указан — без него отсутствие основания не считается установленным'}</span>
              </div>
              <div style={{ marginTop: 8 }}><FactorList factors={fac('R1')} items={false} /></div>

              <h2 className="t-h2" style={{ margin: '40px 0 16px' }}>Готовность</h2>
              <div className="promo-tiles">
                {ready('Заявлено застройщиком', rd.declared)}
                {ready('Подтверждено', rd.confirmed)}
                <div className={`promo-tile ${gap === null ? 'empty-v' : ''}`}>
                  <div className="d">Разница</div>
                  <span className="big">{gap === null ? '—' : `${gap} п.п.`}</span>
                  <div className="d">от {model.thresholds.readinessGap} п.п. — фактор R5{gap === null && '; без пары значений сравнить нельзя'}</div>
                </div>
              </div>
              <div style={{ marginTop: 8 }}><FactorList factors={fac('R5')} items={false} /></div>

              <h2 className="t-h2" style={{ margin: '40px 0 8px' }}>Обращения{own.length > 0 && <span className="c3"> · {own.length}</span>}</h2>
              {own.length === 0 && <div className="c2" style={{ padding: '8px 0 12px' }}>Обращений нет</div>}
              {own.map((a) => (
                <Cell key={a.id} inset icon={<span className="ico"><IcUsers size={18} /></span>} title={a.subject}
                  sub={[a.collective ? 'Коллективное' : 'Индивидуальное', `заявителей: ${a.applicants}`, a.source, fmtDate(a.date), a.status !== 'pending' && a.verifiedAt ? `решение ${fmtDate(a.verifiedAt)}` : ''].filter(Boolean).join(' · ')}
                  end={<span className={`tag ${APPEAL[a.status][1]}`}>{APPEAL[a.status][0]}</span>} />
              ))}
              <div style={{ marginTop: 8 }}><FactorList factors={fac('R4')} items={false} /></div>
              <h2 className="t-h2" style={{ margin: '40px 0 4px' }}>Параметры ЖК</h2>
              <div className="t-sm c2" style={{ marginBottom: 20 }}>ЖК «{c.name}»</div>
              <div className="params">
                <div className="param"><IcCalendar /><div><div className="l">Срок сдачи</div><div className="v">{quarter(c.plannedEnd)}</div></div></div>
                <div className="param"><IcCrane /><div><div className="l">Начало строительства</div><div className="v">{fmtDate(c.start)}</div></div></div>
                <div className="param"><IcBuilding /><div><div className="l">Этажность</div><div className="v">{c.floors} этажей · {c.flats} квартир</div></div></div>
                <div className="param"><IcWorker /><div><div className="l">Готовность</div><div className="v">{c.stage}, {c.progress}%</div></div></div>
              </div>
            </div>
          )}
          {tab === 'risk' && (
            <div className="fade">
              <h2 className="t-h2" style={{ marginBottom: 8 }}>Из чего сложился балл</h2>
              <p className="c2" style={{ marginBottom: 16 }}>{r.summary}</p>
              {r.needsCheck && (
                <div className="box warn" style={{ marginBottom: 16 }}>
                  <div className="row between">
                    <span className="row bold" style={{ gap: 8, color: 'var(--amber-ink)' }}><IcHelp size={18} />Нужна проверка</span>
                    <span className="t-sm" style={{ color: 'var(--amber-ink)' }}>{unknown.length} из {r.factors.length} факторов</span>
                  </div>
                  <p className="t-sm" style={{ margin: '6px 0 4px' }}>Сведений нет или они не подтверждены. Пока это 0 баллов: после проверки фактор подтвердится или будет снят.</p>
                  {unknown.map((f) => (
                    <div key={f.code} className="wf"><span className="code">{f.code}</span><div><div className="t-sm bold">{f.title}</div><div className="t-xs c2" style={{ marginTop: 2 }}>{f.evidence}</div></div></div>
                  ))}
                </div>
              )}
              <FactorList factors={r.factors} from={here('risk')} />
              <h3 className="t-h3" style={{ margin: '32px 0 2px' }}>Вне балла</h3>
              <p className="t-sm c2" style={{ marginBottom: 4 }}>Наблюдения — повод присмотреться, на балл и зону не влияют</p>
              <ObservationList items={r.observations} />
              <div className="row t-sm" style={{ marginTop: 16, gap: 12 }}><Link to="/method" className="link">Как считается риск</Link><span className="c3">Модель {r.modelVersion}</span></div>
            </div>
          )}
          {tab === 'ads' && (
            <div className="fade">
              <div style={{ marginBottom: 16 }}><FactorList factors={fac('R2', 'R3')} items={false} /></div>
              {linked.length === 0 && <div className="empty">Публикаций по объекту не найдено</div>}
              {linked.map((a) => (
                <Link key={a.id} to={`/ads/${a.id}`} state={from(here('ads').label, here('ads').to)} className="offer" style={{ gridTemplateColumns: a.image ? '200px minmax(0,1fr)' : '1fr' }}>
                  {a.image && <div className="ph"><img src={a.image} alt="" /></div>}
                  <div className="body">
                    <div className="row between">
                      <div className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={20} /><span className="bold">{a.account}</span><span className="t-sm c2">{fmtDT(a.date)}</span></div>
                      <div className="row" style={{ gap: 6 }}>
                        {isDemoAd(a) && <Origin kind="demo" label="демо" title="Демонстрационная публикация: вымышленный текст и аккаунт" />}
                        {!linkConfirmed(a) && <span className="tag yellow" title="Гипотеза ИИ: аналитик ещё не подтвердил, что публикация про этот ЖК, — в балл не входит">привязка — гипотеза ИИ {Math.round((a.matchConfidence || 0) * 100)}%</span>}
                        <StatusTag s={a.status} />
                      </div>
                    </div>
                    <p className="txt" style={{ marginTop: 6 }}><Highlight text={a.text} /></p>
                  </div>
                </Link>
              ))}
            </div>
          )}
          {tab === 'photos' && (
            <div className="fade">
              <div style={{ marginBottom: 16 }}><FactorList factors={fac('R6')} items={false} /></div>
              {c.photos.length >= 2 ? <Compare before={c.photos[0]} after={c.photos[c.photos.length - 1]} /> : c.photos.length === 0 ? <div className="drop"><div><IcCamera /><p className="c2" style={{ marginTop: 8 }}>Снимков по объекту нет — простой по снимкам не оценить. Загрузка снимков — после пилота</p></div></div> : null}
              <div className="gcards" style={{ marginTop: 16, gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 16 }}>
                {c.photos.map((p, i) => (
                  <div key={i}>
                    <div className="ph" style={{ aspectRatio: '4/3', borderRadius: 12 }}><img src={p.src} alt="" /></div>
                    <div className="bold t-sm" style={{ marginTop: 8 }}>{fmtDate(p.date)}</div>
                    <div className="t-xs c2">{p.source} · активность: {p.activity}</div>
                    <div className="row wrap-f" style={{ gap: 4, marginTop: 6 }}>{p.fence && <TriTag v={p.fence} label="Забор" />}{p.bins && <TriTag v={p.bins} label="Контейнер" />}{p.wheelWash && <TriTag v={p.wheelWash} label="Мойка" />}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {tab === 'log' && (
            <div className="fade">
              <div className="row between wrap-f" style={{ marginBottom: 8, gap: 12 }}>
                <Seg value={group} onChange={setGroup} items={AUDIT_GROUPS.map(([g, l]): [AuditGroup, React.ReactNode] => [g, <>{l}{logReady && <span className="c3 num">{count(g)}</span>}</>])} />
                {live && <button className="link t-sm" onClick={() => setVer((v) => v + 1)}>Обновить</button>}
              </div>
              <p className="t-sm c2" style={{ marginBottom: 4 }}>Кто, что и когда сделал по объекту: сверка оснований и готовности с источником и временем фиксации, публикации и решения по ним, сигналы, внутренние запросы сведений. Новые — сверху.</p>
              {mode === 'demo' ? (
                <div className="empty">Журнал объекта хранится на сервере, а сейчас открыт демо-набор без бэкенда.<br />Запустите <code>./dev.sh</code> — и здесь появятся события с автором и временем.</div>
              ) : !logReady && !(log?.id === c.id && log.err) ? (
                <div className="empty">{mode === 'loading' ? 'Подключаемся к серверу…' : 'Загружаем журнал…'}</div>
              ) : (
                <>
                  {log?.id === c.id && log.err && <div className="alog-err t-sm">Журнал не загрузился: {log.err} <button className="link" onClick={() => setVer((v) => v + 1)}>Повторить</button></div>}
                  {/* без загруженных событий — только ошибка: пустой журнал и незагруженный — не одно и то же */}
                  {logReady && <AuditList events={shownLog} links={['ad', 'signal']} from={here('log')} empty={group === 'all' ? 'Событий по объекту пока нет' : 'Событий этого вида нет'} />}
                </>
              )}
            </div>
          )}
          {tab === 'dev' && dev && (
            <div className="fade">
              <div className="kv" style={{ maxWidth: 560 }}>
                {[['Наименование', isDemo(dev) ? <>{dev.name}<span className="c2"> · {DEMO_ORG}</span></> : dev.name], ['БИН', dev.bin], ['Руководитель', dev.director], ['Дата регистрации', fmtDate(dev.registered)], ['Объектов всего', dev.objects], ['Незавершённых', dev.unfinished], ['Сайт', dev.site ?? '—'], ['Instagram', dev.instagram ?? '—']].flatMap(([k, v]) => [<span key={`${k}k`}>{k}</span>, <span key={`${k}v`}>{v}</span>])}
              </div>
              <div style={{ marginTop: 24, maxWidth: 560 }}><ObservationList items={r.observations.filter((o) => o.code === 'O2')} /></div>
            </div>
          )}

          {/* карта — в обычном потоке, не в липкой колонке: так та короче окна и внизу страницы действия не уходят под шапку */}
          <div className="row between" style={{ margin: '40px 0 16px', alignItems: 'flex-end' }}>
            <div><h2 className="t-h2">Расположение</h2><div className="t-sm c2" style={{ marginTop: 4 }}>{c.address} · {c.district}</div></div>
            <Link to={`/?sel=${c.id}`} className="link t-sm bold">На общей карте</Link>
          </div>
          <div className="ph" style={{ height: 240, borderRadius: 16 }}>
            <MapView items={[{ c, zone: r.zone, score: r.score }]} center={[c.lng, c.lat]} zoom={14.5} interactive={false} labels />
          </div>
        </div>

        {/* липкая колонка — только сводка и действия, короче окна: внизу страницы она уходит вверх вместе с концом контента */}
        <aside className="stack sticky">
          <div style={{ padding: '4px 0' }}>
            <span className="t-h3">Об объекте</span>
            <div className="kv" style={{ marginTop: 14, gridTemplateColumns: '130px 1fr' }}>
              <span>Стадия</span><span>{c.stage}, {c.progress}%</span>
              <span>Квартир</span><span>{c.flats} · {c.floors} эт.</span>
              <span>Срок сдачи</span><span>{quarter(c.plannedEnd)}</span>
              <span>Реклама</span><span>{linked.length ? `${linked.length} ${plural(linked.length, ['публикация', 'публикации', 'публикаций'])}` : 'не найдена'}</span>
              <span>Риск-скоринг</span><span className="bold" style={{ color: `var(--${r.zone === 'yellow' ? 'amber' : r.zone})` }}>{r.score} из 100</span>
            </div>
            <button type="button" className="why" onClick={() => goTab('risk')} title="Вкладка «Риск»: факторы, пояснения и доказательства">
              <Gauge value={r.score} />
              <div className="why-f">
                {hitF.length > 0
                  ? hitF.map((f) => <div key={f.code}><span className="code">{f.code}</span><span>{f.title}</span><b>+{f.points}</b></div>)
                  : <span className="c2">Подтверждённых факторов нет</span>}
              </div>
              <span className="why-l">Почему {points(r.score)} →</span>
            </button>
            {r.needsCheck && <div style={{ marginBottom: 12 }}><NeedsCheck /></div>}
            <div className="stack" style={{ gap: 8 }}>
              <SignalPanel complex={c} />
              <button className="btn g full" disabled={!live} onClick={openReq}>Запросить сведения</button>
              {mode === 'demo' && <div className="t-xs c2">Запрос записывается в журнал объекта на сервере — в демо-режиме без бэкенда недоступен</div>}
            </div>
            <div className="row" style={{ marginTop: 18, alignItems: 'flex-start', gap: 12 }}>
              <div style={{ flex: 1 }}>
                <button type="button" className="link" onClick={() => goTab('dev')} style={{ textAlign: 'left' }}>Застройщик {dev?.name}</button>
                <div className="t-xs c3" style={{ marginTop: 2 }}>{(dev?.objects ?? 0) - (dev?.unfinished ?? 0)} сдано · {dev?.unfinished} строится{isDemo(dev) && ` · ${DEMO_ORG}`}</div>
              </div>
              <span className="tag dark" style={{ height: 28 }}><IcUsers size={12} />{dev?.name.replace(/ТОО «|»/g, '').split(' ')[0]}</span>
            </div>
            <p className="t-xs c2 row" style={{ marginTop: 16, gap: 6, alignItems: 'flex-start' }}><IcShield size={14} />ИИ даёт рекомендацию. Решение о нарушении принимает аналитик.</p>
          </div>
        </aside>
      </div>

      {req && (
        <Modal title="Запросить сведения" onClose={() => setReq(null)}
          footer={<>
            <button className="btn g" onClick={() => setReq(null)}>Отмена</button>
            <button className="btn y" disabled={!reqValid || req.busy} onClick={sendReq}>{req.busy ? 'Записываем…' : 'Записать запрос'}</button>
          </>}>
          <Cell icon={<span className="ico"><IcBuilding size={20} /></span>} title={`ЖК «${c.name}»`} sub={dev ? `${dev.name} · БИН ${dev.bin}` : c.address} />
          <Cell icon={<span className="ava">{user?.short}</span>} title={user?.name} sub={`${user?.role ?? ''} · автор запроса, дата и время попадут в журнал`} />
          <div className="t-sm c2" style={{ padding: '14px 24px 2px' }}>Что запросить{suggestTopics(r).length > 0 && ' · отмечены темы, по которым риск «Нужна проверка»'}</div>
          <div role="group" aria-label="Темы запроса">
            {REQUEST_TOPICS.map(([t, label]) => {
              const on = req.topics.includes(t)
              return (
                <button key={t} type="button" role="checkbox" aria-checked={on} className="cell chk-row" onClick={() => toggleTopic(t)}>
                  <span className={`chk ${on ? 'on' : ''}`}>{on && <IcCheck size={14} strokeWidth={3} />}</span>
                  <div className="main"><div className="ttl">{label[0].toUpperCase() + label.slice(1)}</div></div>
                </button>
              )
            })}
          </div>
          <div style={{ padding: '12px 24px 4px' }}>
            <label className="t-sm c2" htmlFor="reqc">Комментарий{req.topics.includes('other') ? ' — обязательно для темы «другое»' : ' — необязательно'}</label>
            <textarea id="reqc" className="input" style={{ marginTop: 6, minHeight: 96 }} maxLength={COMMENT_MAX} value={req.comment}
              placeholder="Например: в реестре МИО отказ, по гарантии ЕО ответа нет"
              onChange={(e) => setReq({ ...req, comment: e.target.value, err: undefined })} />
            {req.err && <div className="alog-err t-sm" style={{ marginTop: 8 }}>Не записано: {req.err}</div>}
            <p className="t-xs c2" style={{ marginTop: 8, lineHeight: '17px' }}>Внутренний запрос: фиксируется в журнале объекта с автором и временем, во внешние органы не отправляется.</p>
          </div>
        </Modal>
      )}
    </>
  )
}
