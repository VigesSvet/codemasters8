import { Fragment, useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigationType, useParams, useSearchParams } from 'react-router-dom'
import { AuditList } from '../components/Audit'
import Compare from '../components/Compare'
import { ObjectGallery } from '../components/Gallery'
import { DecisionPanel } from '../components/Decision'
import { FactorList, LawNote, ObservationList } from '../components/Factors'
import GeoCheck from '../components/GeoCheck'
import MapView from '../components/MapView'
import { RequestLetterButton } from '../components/RequestLetter'
import { DocSourceLine, Origin } from '../components/Provenance'
import { DevScreeningBlock, NoScreening, ScreeningBlock, ScreeningTag } from '../components/Screening'
import SatHistory from '../components/SatHistory'
import SignalPanel from '../components/SignalPanel'
import { AddButton, CheckedBy, TileActions, VerifyDialogs, VerifyProgress } from '../components/Verification'
import { Cell, Gauge, Highlight, Modal, NeedsCheck, PageTitle, PlatformIcon, RiskZone, SafeImg, Seg, StatusTag, TriTag, fmtDate, fmtDT, plural, zoneShort } from '../components/ui'
import type { Ad, AppealStatus, AuditEvent, BasisDoc, BasisSource, ReadinessValue, RiskResult, Stage, Tri } from '../data/types'
import { errorText, mediaSrc } from '../lib/api'
import { AUDIT_GROUPS, REQUEST_TOPICS, inGroup, type AuditGroup, type RequestTopic } from '../lib/audit'
import { from, useFrom } from '../lib/nav'
import { ADVERTISED_NOTE, DEMO_ORG, DEMO_PUBLISHED_LIST, NO_DATA, REGISTRY_NOTE, UNKNOWN_DEV, isAdvertised, isDemo, isDemoAd, isRegistry, placeText, portfolioText, progressText, registryKeyOf } from '../lib/provenance'
import { NOT_ASSESSED, isAssessed, linkConfirmed, model } from '../lib/risk'
import { adReasons, anyScreening, hasPoint, statusOf } from '../lib/screening'
import { useHeadHeight } from '../lib/sticky'
import { useStore } from '../lib/store'
import { appealLines, dmy, enteredLine, isChecked, newAppeal, newAppealsCheck, newPhoto, newReadiness, tileId, type Dlg, type VerifyTarget } from '../lib/verification'
import { IcBuilding, IcCalendar, IcCamera, IcCheck, IcClose, IcCrane, IcDatabase, IcDoc, IcHelp, IcMap, IcMapPin, IcMegaphone, IcShield, IcUsers, IcWorker } from '../ui/icons'

const STAGES: Stage[] = ['котлован', 'фундамент', 'каркас', 'кровля', 'отделка', 'введён']
const TABS = ['basis', 'risk', 'ads', 'photos', 'sat', 'dev', 'log'] as const
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
const quarter = (d: string) => { if (!d) return NO_DATA; const x = new Date(d); return `${['I', 'II', 'III', 'IV'][Math.floor(x.getMonth() / 3)]} кв. ${x.getFullYear()}` }
const day = (d?: string | null) => (d ? fmtDate(d) : NO_DATA)
const points = (n: number) => `${n} ${plural(n, ['балл', 'балла', 'баллов'])}`
/** Где была страница: «назад» с публикации возвращает к тому же месту (ключ — запись истории) */
const scrolls = new Map<string, number>()

/** Карточка ЖК: шапка объекта и вкладки разделов(основания, риск, реклама, снимки, спутник, застройщик, журнал) */
export default function ComplexCard() {
  const { id } = useParams()
  const [sp, setSp] = useSearchParams()
  const loc = useLocation()
  const navType = useNavigationType()
  const back = useFrom()
  useHeadHeight()
  const { mode, complexes, ads, developers, appeals, notify, riskOf, getAudit, requestInfo, refreshComplex, user, revision } = useStore()
  const tab: Tab = TABS.find((t) => t === sp.get('tab')) ?? 'basis'
  const tabsRef = useRef<HTMLDivElement>(null)
  const arrived = useRef<string>(undefined)
  const [log, setLog] = useState<Log | null>(null)
  const [ver, setVer] = useState(0)
  const [group, setGroup] = useState<AuditGroup>('all')
  const [req, setReq] = useState<{ topics: RequestTopic[]; comment: string; busy?: boolean; err?: string } | null>(null)
  // окно сверки сведений аналитиком (основание, перечень ГАСК, готовность, обращения, снимки)
  const [dlg, setDlg] = useState<Dlg | null>(null)
  const [flash, setFlash] = useState<VerifyTarget | null>(null)
  const live = mode === 'api'
  const exists = complexes.some((x) => x.id === id)
  // вкладка — в адресе (replace): «назад» со страницы публикации открывает её же; «откуда пришли» (state) не теряется
  const setTab = (t: Tab) => setSp((p) => { const n = new URLSearchParams(p); if (t === 'basis') n.delete('tab'); else n.set('tab', t); return n }, { replace: true, state: loc.state })
  /** Переключить вкладку и прокрутить к ней — для ссылок вне вкладок (правая колонка, галерея) */
  const goTab = (t: Tab) => { setTab(t); requestAnimationFrame(() => tabsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })) }
  /** К плитке основания, которую ещё не сверили: вкладка «Основания», прокрутка и подсветка */
  const goVerify = (t: VerifyTarget) => {
    setTab('basis')
    setFlash(t)
    setTimeout(() => document.getElementById(tileId(t))?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 0)
    setTimeout(() => setFlash((x) => (x === t ? null : x)), 2400)
  }

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
        <>ЖК с кодом «{id}» нет {mode === 'snapshot' ? 'в офлайн-копии стартового набора' : 'на сервере'} — возможно, ссылка устарела или объект удалён.<br />
          <Link to="/complexes" className="btn d m" style={{ marginTop: 16 }}>К списку новостроек</Link></>
      )}</div>
    </>
  )
  const r = riskOf(c)
  /** Координаты перенесены из 2ГИС: точка сразу меняется в общем списке ЖК (карточка, общая карта, список новостроек),
   *  точные значения — ЖК перечитывается с сервера */
  const applied = (p: { lat: number; lng: number }) => { void refreshComplex(c.id, { lat: p.lat, lng: p.lng }) }
  const hitF = r.factors.filter((f) => f.state === 'hit')
  /** Ссылки на публикации передают, откуда пришли: разбор публикации покажет «← ЖК «…»» и вернёт на ту же вкладку */
  const here = (t: Tab) => ({ label: `ЖК «${c.name}»`, to: `/complexes/${c.id}?tab=${t}` })
  const dev = developers.find((d) => d.id === c.developerId)
  const linked = ads.filter((a) => a.complexId === c.id)
  // «Проверка по рекламе» относит к ЖК и публикации по упоминанию названия (гипотезы): те, что в её причинах, — во вкладке «Реклама»
  const mentioned = [...new Set(adReasons(c.screening).map((x) => x.adId).filter((x): x is string => !!x))]
    .map((aid) => ads.find((a) => a.id === aid)).filter((a): a is Ad => !!a && a.complexId !== c.id)
  const own = appeals.filter((a) => a.complexId === c.id).sort((a, b) => b.date.localeCompare(a.date))
  const fac = (...codes: string[]) => r.factors.filter((f) => codes.includes(f.code))
  const unknown = r.factors.filter((f) => f.state === 'unknown')
  const o1 = r.observations.find((o) => o.code === 'O1')
  const b = c.basis
  const demoCx = isDemo(c)
  // карточка из открытого реестра: сведения источника, риск — только по проверенным данным
  const regCx = isRegistry(c), regKey = registryKeyOf(c)
  // ЖК по рекламе: в открытом реестре его нет, карточку создал аналитик или импорт — сведения рекламы не проверены
  const advCx = isAdvertised(c)
  const scored = isAssessed(r)
  const point = hasPoint(c) ? { lat: c.lat, lng: c.lng } : null
  // «Проверка по рекламе» считается на сервере (в офлайн-копии — как он её посчитал); у сервера старой версии её нет
  const screenOn = anyScreening(complexes)
  // контрольный сценарий ТЗ §5 на этом объекте (A — c14, B — c2, C — c10): такие ЖК есть только в тестовом наборе и старых БД
  const kase = model.cases.find((k) => k.complexId === c.id)
  /** Где и когда сверен документ: своя запись; у установленного статуса без своей — общая сверка; иначе «не проверялся» */
  const docProv = (k: BasisDoc, v: Tri) => {
    const own = v !== 'unknown' ? b.sources?.[k] : undefined
    if (own?.checkedBy) return <CheckedBy rec={own} />
    const general: BasisSource | null = !own && v !== 'unknown' && b.source ? { source: b.source, checkedAt: b.checkedAt ?? '', url: null } : null
    return <DocSourceLine src={own ?? general} general={!!general} />
  }
  // перечень ГАСК: сверка аналитика; у демо-ЖК старой БД — сведения демо-перечня
  const listSrc: BasisSource = { source: DEMO_PUBLISHED_LIST.source, checkedAt: DEMO_PUBLISHED_LIST.updated, url: DEMO_PUBLISHED_LIST.url }
  const listProv = c.publishedListCheck?.checkedBy ? <CheckedBy rec={c.publishedListCheck} />
    : c.inPublishedList === 'unknown' || !demoCx ? <DocSourceLine src={null} none="Не сверялся с перечнем" /> : <DocSourceLine src={listSrc} />
  const analystChecked = (['guarantee', 'mio', 'bvu'] as const).some((k) => b.sources?.[k]?.checkedBy)
  const rd = c.readiness ?? { declared: null, confirmed: null }
  // округление как в модели (п.п. до сотых, половина — вверх)
  const gap = rd.declared && rd.confirmed ? Math.floor(Math.abs(rd.declared.value - rd.confirmed.value) * 100 + 0.5) / 100 : null
  const stageIdx = c.stage ? STAGES.indexOf(c.stage) : -1
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

  /** Плитка основания: значение, документ, провенанс и действие сверки внутри (подтвердить, сверить, изменить) */
  const basis = (target: VerifyTarget, title: string, v: Tri, doc?: string, note?: string, tag?: string, prov?: React.ReactNode) => {
    const checked = isChecked(c, target)
    return (
      <div id={tileId(target)} className={`promo-tile vf-tile ${checked ? (v === 'yes' ? 'ok' : v === 'no' ? 'bad' : '') : 'open'} ${flash === target ? 'flash' : ''}`}>
        <div className="row between">
          <span className={`zone-pic ${v === 'yes' ? 'green' : v === 'no' ? 'red' : 'gray'}`} style={{ width: 32, height: 32 }}>{v === 'yes' ? <IcCheck size={16} /> : v === 'no' ? <IcClose size={16} /> : <IcHelp size={16} />}</span>
          <div className="row" style={{ gap: 6 }}>{tag && <span className="tag outline">{tag}</span>}<TriTag v={v} /></div>
        </div>
        <div className="h" style={{ marginTop: 6 }}>{title}</div>
        <div className="d">{doc || (v === 'unknown' ? 'Сведения не получены — сверьте с источником или запросите у уполномоченного органа' : v === 'yes' ? 'Номер документа не указан' : 'Документ не найден')}</div>
        {note && <div className="d" style={{ marginTop: 'auto', color: 'var(--text-3)' }}>{note}</div>}
        {prov && <div style={{ marginTop: note ? 6 : 'auto', paddingTop: 4 }}>{prov}</div>}
        <TileActions c={c} target={target} checked={checked} live={live} open={setDlg} />
      </div>
    )
  }
  const ready = (which: 'declared' | 'confirmed', title: string, v: ReadinessValue | null) => (
    <div className={`promo-tile ${v ? '' : 'empty-v'}`}>
      <div className="d">{title}</div>
      <span className="big">{v ? `${v.value}%` : '—'}</span>
      <div className="d">{v ? <>{v.source || 'источник не указан'} · {v.date ? fmtDate(v.date) : 'дата не указана'}</> : 'нет данных'}</div>
      {enteredLine(v) && <div className="t-xs c2">{enteredLine(v)}</div>}
      <div style={{ marginTop: 'auto', paddingTop: 4 }}><AddButton live={live} onClick={() => setDlg(newReadiness(c, which))}>{v ? 'Обновить готовность' : 'Внести готовность'}</AddButton></div>
    </div>
  )

  return (
    <>
      <PageTitle crumbs={back ? [[`← ${back.label}`, back.to], [`ЖК «${c.name}»`]] : [['Карта', '/'], ['Новостройки', '/complexes'], [`ЖК «${c.name}»`]]}
        title={<span className="row wrap-f" style={{ gap: 14 }}>ЖК «{c.name}» {c.screening && <ScreeningTag s={c.screening} big />}<RiskZone r={r} />{demoCx && <Origin kind="demo" label="Демо-сценарий (вымышленный ЖК)" />}{regCx && <Origin kind="registry" label="Открытый реестр · не проверено" />}{advCx && <Origin kind="advertised" label="По рекламе · не проверено" />}</span>}
        sub={<>{c.address || 'адрес не указан'}{/усть-каменогорск/i.test(c.address) ? '' : ', Усть-Каменогорск'}{c.district && <> · <span className="row" style={{ display: 'inline-flex', gap: 4 }}><IcMap size={14} />{c.district}</span></>}</>} />
      {advCx && (
        <div className="box warn od-note t-sm" style={{ marginBottom: 16, background: 'var(--amber-bg)', color: 'var(--amber-ink)' }}>
          <IcMegaphone size={18} />
          <div><b>{ADVERTISED_NOTE}.</b> Название, адрес{point ? ', координаты' : ''} и застройщик — по рекламе и решению аналитика.
            Основание привлечения средств, стадия и готовность — «{NO_DATA}», пока их не сверят.{!scored && <> <b>{NOT_ASSESSED}.</b></>}
            {c.advert && c.advert.discoveredIds.length > 0 && <div style={{ marginTop: 6 }}><Link className="link" to="/discovered?tab=linked" state={from(`ЖК «${c.name}»`, `/complexes/${c.id}`)}>Объекты из рекламы по этому ЖК</Link></div>}</div>
        </div>
      )}
      {c.screening ? (
        <div style={{ marginBottom: 20 }}>
          <ScreeningBlock s={c.screening} permit={c.permit} advert={c.advert} state={from(`ЖК «${c.name}»`, `/complexes/${c.id}`)}
            extra={(linked.length > 0 || mentioned.length > 0) && <button className="link t-sm" style={{ alignSelf: 'flex-start' }} onClick={() => goTab('ads')}>Публикации на вкладке «Реклама» →</button>} />
          {/* решение аналитика: «Объект сдан», «Проверено — замечаний нет», отмена; снятое новой рекламой — уведомление */}
          <DecisionPanel c={c} live={live} state={from(`ЖК «${c.name}»`, `/complexes/${c.id}`)} onSaved={notify} />
        </div>
      ) : !screenOn && mode !== 'loading' && <div style={{ marginBottom: 20 }}><NoScreening> — сервер старой версии: показан только формальный риск.</NoScreening></div>}
      {regCx && (
        <div className="box od-note t-sm" style={{ marginBottom: 20 }}>
          <IcDatabase size={18} />
          <div><b>Карточка создана по открытому реестру</b> — {REGISTRY_NOTE}: название, адрес, координаты, застройщик{c.plannedEnd ? ', плановое завершение' : ''} и номера документов основания (справкой).
            Стадия, готовность и основание привлечения средств — «{NO_DATA}», пока их не сверит аналитик.{!scored && <> <b>{NOT_ASSESSED}.</b></>}
            {regKey && <div style={{ marginTop: 6 }}><Link className="link" to={`/registry/${encodeURIComponent(regKey)}`} state={from(`ЖК «${c.name}»`, `/complexes/${c.id}`)}>Записи источников по объекту</Link></div>}</div>
        </div>
      )}
      <VerifyProgress c={c} assessed={scored} onGo={goVerify} style={{ marginBottom: 20 }} />
      {kase && (
        <div className="case-banner">
          <span className="tag dark">Сценарий {kase.id}</span>
          <span className="grow"><b>Контрольный сценарий {kase.id} из ТЗ §5:</b> {kase.title} · ожидание: {zoneShort[kase.zone].toLowerCase()} зона, {points(kase.score)}</span>
          {r.score === kase.score && r.zone === kase.zone
            ? <span className="tag green"><IcCheck size={12} strokeWidth={3} />сейчас совпадает</span>
            : <span className="tag yellow" title="Данные объекта изменились после эталона сценария">сейчас {points(r.score)}</span>}
          <Link to="/method#cases" className="link t-sm bold">Сценарии в методике</Link>
        </div>
      )}

      <div className="cols-r">
        <div>
          {/* фото объекта: Homeportal, фото-мониторинг, реклама, фото оператора — с подписью источника (ТЗ §7 «Достоверность») */}
          <ObjectGallery key={`gallery-${c.id}`} c={c} tags={<>{c.stage && <span className="tag white">{c.stage === 'введён' ? 'сдан' : 'строится'}</span>}{r.needsCheck && <span className="tag white">нужна проверка</span>}{c.idleDays >= 30 && <span className="tag white">стоит {c.idleDays} дн.</span>}</>} />

          <h2 className="t-h2" style={{ margin: '40px 0 16px' }}>Ход строительства</h2>
          {c.stage ? (
            <div className="steps">
              {STAGES.map((s, i) => <div key={s} className={i < stageIdx ? 'done' : i === stageIdx ? 'cur' : ''} style={i === stageIdx ? ({ '--p': `${c.stage === 'введён' ? 100 : Math.max(15, c.progress)}%` } as React.CSSProperties) : undefined}>{s[0].toUpperCase() + s.slice(1)}</div>)}
            </div>
          ) : <p className="c2">Стадия строительства не известна: «В строительстве» или «Завершён» у источника — фильтр реестра, а не стадия работ; акта ввода в записях нет.</p>}

          <div className="subtabs" ref={tabsRef} style={{ marginTop: 40, scrollMarginTop: 'calc(var(--yhead, 113px) + 19px)' }}>
            {([['basis', 'Основания'], ['risk', 'Риск'], ['ads', 'Реклама', linked.length], ['photos', 'Снимки', c.photos.length], ['sat', 'Спутник'], ['dev', 'Застройщик'], ['log', 'Журнал', logReady ? events.length : undefined]] as const).map(([k, l, n]) => (
              <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}{n !== undefined && <span className="n">{n}</span>}</button>
            ))}
          </div>

          {tab === 'basis' && (
            <div className="fade">
              <h2 className="t-h2" style={{ marginBottom: 16 }}>Основание привлечения средств</h2>
              <VerifyProgress c={c} assessed={scored} onGo={goVerify} style={{ marginBottom: 16 }} />
              <div className="promo-tiles" style={{ gridTemplateColumns: 'repeat(2, 1fr)' }}>
                {basis('guarantee', 'Гарантия ЕО (КЖК)', c.basis.guarantee, c.basis.guaranteeRef, undefined, undefined, docProv('guarantee', c.basis.guarantee))}
                {basis('mio', 'Разрешение МИО', c.basis.mio, c.basis.mioRef, c.basis.guarantee === 'yes' && c.basis.mio === 'no' ? 'При действующей гарантии ЕО отсутствие отдельного разрешения МИО — не R1' : undefined, undefined, docProv('mio', c.basis.mio))}
                {basis('bvu', 'Банк второго уровня', c.basis.bvu, c.basis.bank, undefined, undefined, docProv('bvu', c.basis.bvu))}
                {basis('list', 'Перечень ГАСК', c.inPublishedList, c.inPublishedList === 'unknown' ? o1?.text : c.inPublishedList === 'yes' ? 'Объект есть в перечне' : 'Объекта нет в перечне',
                  o1?.state === 'flag' ? o1.text : undefined, 'вне балла · наблюдение O1', listProv)}
              </div>
              <div className={`src-line ${b.source && b.checkedAt ? '' : 'none'}`}>
                <IcDoc size={16} />
                <span>{b.source
                  ? analystChecked
                    ? <>Последняя сверка оснований: {b.source} · {b.checkedAt ? `сверено ${fmtDate(b.checkedAt)}` : 'дата сверки не указана'}. Кто и где сверил каждый документ — на плитках</>
                    : <>Общая сверка оснований: {b.source} · {b.checkedAt ? `сверено ${fmtDate(b.checkedAt)}` : 'дата сверки не указана'}. Источник и дата по каждому документу — на плитках</>
                  : 'Источник сверки не указан — без него отсутствие основания не считается установленным'}</span>
              </div>
              <div style={{ marginTop: 8 }}><FactorList factors={fac('R1')} items={false} /></div>

              <h2 className="t-h2" style={{ margin: '40px 0 16px' }}>Готовность</h2>
              <div className="promo-tiles">
                {ready('declared', 'Заявлено застройщиком', rd.declared)}
                {ready('confirmed', 'Подтверждено', rd.confirmed)}
                <div className={`promo-tile ${gap === null ? 'empty-v' : ''}`}>
                  <div className="d">Разница</div>
                  <span className="big">{gap === null ? '—' : `${gap} п.п.`}</span>
                  <div className="d">от {model.thresholds.readinessGap} п.п. — фактор R5{gap === null && '; без пары значений сравнить нельзя'}</div>
                </div>
              </div>
              <div style={{ marginTop: 8 }}><FactorList factors={fac('R5')} items={false} /></div>

              <div className="row between wrap-f" style={{ margin: '40px 0 8px', gap: 12 }}>
                <h2 className="t-h2">Обращения{own.length > 0 && <span className="c3"> · {own.length}</span>}</h2>
                <div className="row wrap-f" style={{ gap: 8 }}>
                  <AddButton live={live} onClick={() => setDlg(newAppeal())}>Добавить обращение</AddButton>
                  <AddButton live={live} onClick={() => setDlg(newAppealsCheck(c))}>{own.length ? 'Других обращений нет — сверено' : 'Обращений нет — сверено'}</AddButton>
                </div>
              </div>
              {c.appealsCheck && <div style={{ padding: '4px 0 8px' }}><CheckedBy rec={c.appealsCheck} /><span className="t-xs c2"> · обращений нет{c.appealsCheck.comment ? ` · ${c.appealsCheck.comment}` : ''}</span></div>}
              {own.length === 0 && <div className="c2" style={{ padding: '8px 0 12px' }}>{demoCx || c.appealsCheck ? 'Обращений нет' : 'Обращений в системе нет — это ещё не значит, что их нет: сверьте с ДЭР, ГАСК или eOtinish и отметьте «Обращений нет — сверено»'}</div>}
              {own.map((a) => (
                <Cell key={a.id} inset icon={<span className="ico"><IcUsers size={18} /></span>} title={a.subject}
                  sub={<>{[a.collective ? 'Коллективное' : 'Индивидуальное', `заявителей: ${a.applicants}`, a.source, fmtDate(a.date), a.status !== 'pending' && a.verifiedAt ? `решение ${fmtDate(a.verifiedAt)}` : ''].filter(Boolean).join(' · ')}
                    {appealLines(a).map((l) => <span key={l} className="vf-who">{l}</span>)}</>}
                  end={<span className="row" style={{ gap: 6 }}>
                    {a.status === 'pending' && live && <>
                      <button type="button" className="btn y s" onClick={() => setDlg({ kind: 'decide', appeal: a, action: 'verify', comment: '' })}>Подтвердить</button>
                      <button type="button" className="btn g s" onClick={() => setDlg({ kind: 'decide', appeal: a, action: 'reject', comment: '' })}>Отклонить</button>
                    </>}
                    {a.status !== 'pending' && live && <button type="button" className="link t-xs" title="Изменить решение по обращению" onClick={() => setDlg({ kind: 'decide', appeal: a, action: a.status === 'verified' ? 'reject' : 'verify', comment: '' })}>{a.status === 'verified' ? 'отклонить' : 'подтвердить'}</button>}
                    <span className={`tag ${APPEAL[a.status][1]}`}>{APPEAL[a.status][0]}</span>
                  </span>} />
              ))}
              <div style={{ marginTop: 8 }}><FactorList factors={fac('R4')} items={false} /></div>
              <h2 className="t-h2" style={{ margin: '40px 0 4px' }}>Параметры ЖК</h2>
              <div className="t-sm c2" style={{ marginBottom: 20 }}>ЖК «{c.name}»</div>
              <div className="params">
                <div className="param"><IcCalendar /><div><div className="l">Срок сдачи</div><div className="v">{quarter(c.plannedEnd)}</div></div></div>
                <div className="param"><IcCrane /><div><div className="l">Начало строительства</div><div className="v">{day(c.start)}</div></div></div>
                <div className="param"><IcBuilding /><div><div className="l">Этажность</div><div className="v">{c.floors ? `${c.floors} этажей · ${c.flats} квартир` : NO_DATA}</div></div></div>
                <div className="param"><IcWorker /><div><div className="l">Готовность</div><div className="v">{progressText(c)}</div></div></div>
              </div>
            </div>
          )}
          {tab === 'risk' && (
            <div className="fade">
              {!scored && (
                <div className="box" style={{ marginBottom: 16 }}>
                  <div className="row bold" style={{ gap: 8 }}><IcHelp size={18} />{NOT_ASSESSED}</div>
                  <p className="t-sm" style={{ margin: '6px 0 0' }}>{r.summary}</p>
                  <p className="t-sm c2" style={{ margin: '6px 0 0' }}>{model.notAssessed.why}</p>
                </div>
              )}
              <h2 className="t-h2" style={{ marginBottom: 8 }}>{scored ? 'Из чего сложился балл' : 'Что известно по факторам — справочно, балл не начисляется'}</h2>
              {scored && <p className="c2" style={{ marginBottom: 16 }}>{r.summary}</p>}
              {scored && r.needsCheck && (
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
              <LawNote />
              <h3 className="t-h3" style={{ margin: '32px 0 2px' }}>Вне балла</h3>
              <p className="t-sm c2" style={{ marginBottom: 4 }}>Наблюдения — повод присмотреться, на балл и зону не влияют</p>
              <ObservationList items={r.observations} />
              <div className="row t-sm" style={{ marginTop: 16, gap: 12 }}><Link to="/method" className="link">Как считается риск</Link><span className="c3">Модель {r.modelVersion}</span></div>
            </div>
          )}
          {tab === 'ads' && (
            <div className="fade">
              <div style={{ marginBottom: 16 }}><FactorList factors={fac('R2', 'R3')} items={false} /></div>
              {linked.length === 0 && mentioned.length === 0 && <div className="empty">Публикаций по объекту не найдено</div>}
              {[...linked, ...mentioned].map((a, i) => {
                // файл публикации с сервера (/api/media/…) — через mediaSrc: учитывает VITE_API_URL
                const img = mediaSrc(a.image)
                const byMention = a.complexId !== c.id
                return (<Fragment key={a.id}>
                  {byMention && i === linked.length && (
                    <div className="ai-note" style={{ margin: '16px 0 4px' }}>
                      <b>По упоминаниям в рекламе — гипотезы.</b> «Проверка по рекламе» относит к ЖК {c.screening?.counts.ads ?? mentioned.length} публ. по названию из текста (анализ ИИ, названия из рекламы).
                      Ниже — публикации из причин оценки: к ЖК они не привязаны, привязку подтверждает аналитик на странице публикации.
                    </div>
                  )}
                  <Link to={`/ads/${a.id}`} state={from(here('ads').label, here('ads').to)} className="offer" style={{ gridTemplateColumns: img ? '200px minmax(0,1fr)' : '1fr' }}>
                    {img && <div className="ph"><SafeImg src={img} alt="" fallback={<span className="noimg"><PlatformIcon p={a.platform} size={40} /></span>} /></div>}
                    <div className="body">
                      <div className="row between">
                        <div className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={20} /><span className="bold">{a.account}</span><span className="t-sm c2">{fmtDT(a.date)}</span></div>
                        <div className="row" style={{ gap: 6 }}>
                          {isDemoAd(a) && <Origin kind="demo" label="демо" title="Демонстрационная публикация: вымышленный текст и аккаунт" />}
                          {byMention ? <span className="tag yellow" title="Публикация называет этот ЖК — гипотеза; к ЖК не привязана, в балл не входит">упоминание — гипотеза</span>
                            : !linkConfirmed(a) && <span className="tag yellow" title="Гипотеза ИИ: аналитик ещё не подтвердил, что публикация про этот ЖК, — в балл не входит">привязка — гипотеза ИИ {Math.round((a.matchConfidence || 0) * 100)}%</span>}
                          <StatusTag s={a.status} />
                        </div>
                      </div>
                      <p className="txt" style={{ marginTop: 6 }}><Highlight text={a.text} /></p>
                    </div>
                  </Link>
                </Fragment>)
              })}
            </div>
          )}
          {tab === 'photos' && (
            <div className="fade">
              <div className="row between wrap-f" style={{ marginBottom: 16, gap: 12, alignItems: 'flex-start' }}>
                <div style={{ flex: 1, minWidth: 260 }}><FactorList factors={fac('R6')} items={false} /></div>
                <AddButton live={live} cls="y" onClick={() => setDlg(newPhoto())}>Добавить снимок</AddButton>
              </div>
              {c.photos.length >= 2 ? <Compare before={c.photos[0]} after={c.photos[c.photos.length - 1]} /> : c.photos.length === 0 ? <div className="drop"><div><IcCamera /><p className="c2" style={{ marginTop: 8 }}>Снимков по объекту нет — простой по снимкам не оценить. Добавьте датированный снимок площадки: дата, источник и активность на площадке</p></div></div> : null}
              <div className="gcards" style={{ marginTop: 16, gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 16 }}>
                {c.photos.map((p, i) => (
                  <div key={i}>
                    <div className="ph" style={{ aspectRatio: '4/3', borderRadius: 12 }}><img src={mediaSrc(p.src) ?? p.src} alt="" /></div>
                    <div className="bold t-sm" style={{ marginTop: 8 }}>{fmtDate(p.date)}</div>
                    <div className="t-xs c2">{p.source} · активность: {p.activity}</div>
                    {p.note && <div className="t-xs" style={{ marginTop: 2 }}>{p.note}</div>}
                    {p.by && <div className="t-xs c2 vf-who">Загрузил: {p.by}{p.at ? `, ${dmy(p.at)}` : ''}</div>}
                    <div className="row wrap-f" style={{ gap: 4, marginTop: 6 }}>{p.fence && <TriTag v={p.fence} label="Забор" />}{p.bins && <TriTag v={p.bins} label="Контейнер" />}{p.wheelWash && <TriTag v={p.wheelWash} label="Мойка" />}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {tab === 'sat' && (
            <div className="fade">
              {hasPoint(c) ? <SatHistory c={c} />
                : <div className="ai-note">У ЖК нет координат — архив спутниковых снимков открывается по точке объекта. Сверьте адрес в 2ГИС на вкладке «Основания».</div>}
            </div>
          )}
          {tab === 'log' && (
            <div className="fade">
              <div className="row between wrap-f" style={{ marginBottom: 8, gap: 12 }}>
                <Seg value={group} onChange={setGroup} items={AUDIT_GROUPS.map(([g, l]): [AuditGroup, React.ReactNode] => [g, <>{l}{logReady && <span className="c3 num">{count(g)}</span>}</>])} />
                {live && <button className="link t-sm" onClick={() => setVer((v) => v + 1)}>Обновить</button>}
              </div>
              <p className="t-sm c2" style={{ marginBottom: 4 }}>Кто, что и когда сделал по объекту: сверка оснований и готовности с источником и временем фиксации, публикации и решения по ним, сигналы, внутренние запросы сведений. Новые — сверху.</p>
              {mode === 'snapshot' ? (
                <div className="empty">Журнал объекта хранится на сервере и в офлайн-копию не входит.<br />С сервером здесь появятся события с автором и временем.</div>
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
              {dev.screening && <div style={{ marginBottom: 20, maxWidth: 640 }}><DevScreeningBlock s={dev.screening} state={from(`ЖК «${c.name}»`, `/complexes/${c.id}?tab=dev`)} /></div>}
              {dev.id === UNKNOWN_DEV && <p className="t-sm c2" style={{ marginBottom: 12, maxWidth: 560 }}>Застройщик по рекламе не установлен — укажите его после сверки (БИН в реестре юрлиц, разрешение на строительство).</p>}
              <div className="kv" style={{ maxWidth: 560 }}>
                {[['Наименование', isDemo(dev) ? <>{dev.name}<span className="c2"> · {DEMO_ORG}</span></> : isRegistry(dev) ? <>{dev.name}<span className="c2"> · {REGISTRY_NOTE}</span></> : dev.name], ['БИН', dev.bin || NO_DATA], ['Руководитель', dev.director || NO_DATA], ['Дата регистрации', day(dev.registered)],
                  ['Объектов всего', isRegistry(dev) ? NO_DATA : dev.objects], ['Незавершённых', isRegistry(dev) ? NO_DATA : dev.unfinished], ['Сайт', dev.site ?? '—'], ['Instagram', dev.instagram ?? '—']].flatMap(([k, v]) => [<span key={`${k}k`}>{k}</span>, <span key={`${k}v`}>{v}</span>])}
              </div>
              {isRegistry(dev) && <p className="t-sm c2" style={{ marginTop: 12, maxWidth: 560 }}>Застройщик из открытого реестра: название и БИН — как у источника. Руководитель, дата регистрации и портфель не запрашивались — сверьте по БИН в реестре юрлиц.</p>}
              {isAdvertised(dev) && dev.id !== UNKNOWN_DEV && <p className="t-sm c2" style={{ marginTop: 12, maxWidth: 560 }}>Застройщик по рекламе: название{dev.bin ? ' и БИН' : ''} — со слов рекламы и аналитика, в открытом реестре его нет. Сверьте по БИН в реестре юрлиц.</p>}
              <div style={{ marginTop: 24, maxWidth: 560 }}><ObservationList items={r.observations.filter((o) => o.code === 'O2')} /></div>
            </div>
          )}

          {/* карта — в обычном потоке, не в липкой колонке: так та короче окна и внизу страницы действия не уходят под шапку */}
          <div className="row between" style={{ margin: '40px 0 16px', alignItems: 'flex-end' }}>
            <div><h2 className="t-h2">Расположение</h2><div className="t-sm c2" style={{ marginTop: 4 }}>{placeText(c) || 'адрес не указан'}</div></div>
            {point ? <Link to={`/?sel=${c.id}`} className="link t-sm bold">На общей карте</Link> : <span className="t-sm c2">на карте — в списке «Без координат»</span>}
          </div>
          {point ? (
            <div className="ph" style={{ height: 240, borderRadius: 16 }}>
              {/* key: после переноса координат карта создаётся заново с новым центром */}
              <MapView key={`${point.lng},${point.lat}`} items={[{ c, zone: r.zone, score: r.score, off: !scored, screen: statusOf(c) }]}
                center={[point.lng, point.lat]} zoom={14.5} interactive={false} labels />
            </div>
          ) : (
            <div className="nomap"><div><IcMapPin size={28} /><b className="bold" style={{ color: 'var(--text)' }}>Координат нет</b><br />
              {advCx ? 'Карточка создана по рекламе без точки на карте.' : 'У источника нет координат объекта.'} Сверьте адрес в 2ГИС ниже и перенесите координаты кандидата в карточку — тогда ЖК появится на общей карте.</div></div>
          )}
          {/* сверка адреса в 2ГИС: кандидаты — по кнопке, решение и перенос координат — за аналитиком; события — в журнал объекта */}
          <GeoCheck key={c.id} target={{ kind: 'complex', id: c.id }} address={c.address || c.name} point={point} canApply
            onChange={() => setVer((v) => v + 1)} onApplied={applied} />
        </div>

        {/* липкая колонка — только сводка и действия, короче окна: внизу страницы она уходит вверх вместе с концом контента */}
        <aside className="stack sticky">
          <div style={{ padding: '4px 0' }}>
            <span className="t-h3">Об объекте</span>
            <div className="kv" style={{ marginTop: 14, gridTemplateColumns: '130px 1fr' }}>
              <span>Стадия</span><span>{progressText(c)}</span>
              <span>Квартир</span><span>{c.flats ? `${c.flats} · ${c.floors} эт.` : NO_DATA}</span>
              <span>Срок сдачи</span><span>{quarter(c.plannedEnd)}</span>
              <span>Реклама</span><span>{linked.length ? `${linked.length} ${plural(linked.length, ['публикация', 'публикации', 'публикаций'])}` : 'не найдена'}</span>
              {c.screening && <><span>Проверка по рекламе</span><span><ScreeningTag s={c.screening} short /></span></>}
              <span>Риск-скоринг</span>{scored
                ? <span className="bold" style={{ color: `var(--${r.zone === 'yellow' ? 'amber' : r.zone})` }}>{r.score} из 100</span>
                : <span className="bold c2">не оценивается</span>}
            </div>
            {scored ? (
              <button type="button" className="why" onClick={() => goTab('risk')} title="Вкладка «Риск»: факторы, пояснения и доказательства">
                <Gauge value={r.score} />
                <div className="why-f">
                  {hitF.length > 0
                    ? hitF.map((f) => <div key={f.code}><span className="code">{f.code}</span><span>{f.title}</span><b>+{f.points}</b></div>)
                    : <span className="c2">Подтверждённых факторов нет</span>}
                </div>
                <span className="why-l">Почему {points(r.score)} →</span>
              </button>
            ) : (
              <button type="button" className="box t-sm" style={{ display: 'block', width: '100%', textAlign: 'left', margin: '14px 0 12px' }} onClick={() => goTab('risk')} title="Вкладка «Риск»: что нужно для оценки">
                <b>{NOT_ASSESSED}</b><div className="c2" style={{ marginTop: 4 }}>Оценка появится после подтверждённой привязки публикации, сверенного документа основания, проверенного обращения или снимка площадки →</div>
              </button>
            )}
            {scored && r.needsCheck && <div style={{ marginBottom: 12 }}><NeedsCheck /></div>}
            <div className="stack" style={{ gap: 8 }}>
              <SignalPanel complex={c} />
              <button className="btn g full" disabled={!live} onClick={openReq}>Запросить сведения</button>
              <RequestLetterButton complexId={c.id} complexName={c.name} topics={suggestTopics(r)} onSaved={() => setVer((v) => v + 1)} />
              {mode === 'snapshot' && <div className="t-xs c2">Запрос записывается в журнал объекта на сервере — в офлайн-копии недоступен</div>}
            </div>
            <div className="row" style={{ marginTop: 18, alignItems: 'flex-start', gap: 12 }}>
              <div style={{ flex: 1 }}>
                <button type="button" className="link" onClick={() => goTab('dev')} style={{ textAlign: 'left' }}>Застройщик {dev?.name}</button>
                <div className="t-xs c3" style={{ marginTop: 2 }}>{dev ? portfolioText(dev) : ''}{isDemo(dev) && ` · ${DEMO_ORG}`}{dev?.bin && isRegistry(dev) && ` · БИН ${dev.bin}`}</div>
              </div>
              <span className="tag dark" style={{ height: 28 }}><IcUsers size={12} />{dev?.name.replace(/ТОО «|»/g, '').split(' ')[0]}</span>
            </div>
            <p className="t-xs c2 row" style={{ marginTop: 16, gap: 6, alignItems: 'flex-start' }}><IcShield size={14} />ИИ даёт рекомендацию. Решение о нарушении принимает аналитик.</p>
          </div>
        </aside>
      </div>

      <VerifyDialogs c={c} dlg={dlg} setDlg={setDlg} onSaved={notify} />
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
