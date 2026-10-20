// «Объекты из рекламы»: ЖК, которых нет ни в справочнике, ни в открытом реестре, — их называет реклама (анализ ИИ публикаций)
// или импорт. По каждому решает аналитик: создать карточку ЖК (с пометкой «по рекламе»), привязать к существующему ЖК или
// отклонить с причиной. 2ГИС — только по кнопке «Найти в 2ГИС»; кандидат — подсказка, точку выбирает аналитик.
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Origin } from '../components/Provenance'
import { Disclaimer, ReasonText, ScreeningPic } from '../components/Screening'
import { Cell, Modal, PageTitle, PlatformIcon, adKey, fmtShort, plural } from '../components/ui'
import { loadSnapshot } from '../data/snapshot'
import type { Complex, Discovered as Obj, DiscoveredStatus, Platform } from '../data/types'
import { isAdmin, onAdminChange } from '../lib/admin'
import { errorText } from '../lib/api'
import { fmtStamp } from '../lib/audit'
import { plain } from '../lib/plain'
import {
  DISCOVERED_TABS, DISMISS_PRESETS, NO_DISCOVERED, STAGE_LABEL, countDiscovered, createBody, createForm, createFromDiscovered, createProblem,
  decisionText, dismissDiscovered, geoPicks, linkDiscovered, listDiscovered, mergeChecks, parseDiscoveredTab, pickComplexes, runDiscoveredGeo,
  saleLine, signalTypesLine, sourcesLine, stageLine, type CreateForm,
} from '../lib/discovered'
import { GEO_COPY, cacheLine, candidateDistance, candidatePoint, candidateTitle, fmtDist, fmtPoint, isExact, precisionLabel } from '../lib/geo'
import { from } from '../lib/nav'
import { PLATFORMS } from '../lib/platform'
import { compareScreening, topReason } from '../lib/screening'
import { useStore } from '../lib/store'
import { IcBuilding, IcCheck, IcClose, IcMegaphone, IcRefresh, IcSearch } from '../ui/icons'

const BACK = from('Объекты из рекламы', '/discovered')
const REASON_MAX = 2000
const EMPTY: Record<DiscoveredStatus, string> = {
  new: 'Новых объектов нет: все ЖК из рекламы разобраны или уже есть в справочнике и реестре',
  linked: 'Привязанных объектов пока нет',
  dismissed: 'Отклонённых объектов нет',
}

type Load = { items?: Obj[] | null; err?: string }
/** Сверка 2ГИС по объекту: идёт, ошибка, ошибки отдельных запросов, ответ из кэша */
type GeoState = { busy?: boolean; err?: string; partial?: { query: string; detail: string }[]; cached?: boolean }
type Dialog =
  | { kind: 'create'; o: Obj; form: CreateForm; busy?: boolean; err?: string }
  | { kind: 'link'; o: Obj; q: string; pick?: string; busy?: boolean; err?: string }
  | { kind: 'dismiss'; o: Obj; reason: string; busy?: boolean; err?: string }

const platformOf = (p?: string | null): Platform | null => (p && Object.hasOwn(PLATFORMS, p) ? p as Platform : null)
const when = (d?: string | null) => (d ? fmtStamp(d) : '—')

export default function Discovered() {
  const { mode, user, complexes, developers, notify, revision, refreshScreening, refreshComplex } = useStore()
  const live = mode === 'api'
  // офлайн-копия: список объектов — из неё, только просмотр
  const ro = mode === 'snapshot'
  const nav = useNavigate()
  const [sp, setSp] = useSearchParams()
  const tab = parseDiscoveredTab(sp.get('tab'))
  const setTab = (t: DiscoveredStatus) => setSp((p) => { const n = new URLSearchParams(p); if (t === 'new') n.delete('tab'); else n.set('tab', t); return n }, { replace: true })
  const [load, setLoad] = useState<Load | null>(null)
  const [ver, setVer] = useState(0)
  const [geo, setGeo] = useState<Record<number, GeoState>>({})
  const [dlg, setDlg] = useState<Dialog | null>(null)
  const [admin, setAdmin] = useState(() => isAdmin())
  useEffect(() => onAdminChange(setAdmin), [])
  const author = user?.name ?? ''

  // список с сервера (в офлайн-копии — из неё); после сохранённых изменений и восстановления связи (revision) — заново
  useEffect(() => {
    if (!live && !ro) return
    let alive = true
    const got = ro ? loadSnapshot().then((s) => s.discovered) : listDiscovered()
    got.then(
      (items) => { if (alive) setLoad({ items }) },
      (e: unknown) => { if (alive) setLoad((l) => ({ items: l?.items, err: errorText(e) })) },
    )
    return () => { alive = false }
  }, [live, ro, ver, revision])

  const items = useMemo(() => load?.items ?? [], [load])
  const counts = countDiscovered(items)
  // в вкладке — подозрение первыми, дальше порядок сервера (по числу публикаций)
  const shown = useMemo(() => items.map((o, i) => ({ o, i })).filter(({ o }) => o.status === tab)
    .sort((a, b) => compareScreening(a.o.screening, b.o.screening) || a.i - b.i).map((x) => x.o), [items, tab])

  /** Объект с сервера — на своё место в списке */
  const put = (o: Obj) => setLoad((l) => (l?.items ? { ...l, items: l.items.map((x) => (x.id === o.id ? o : x)) } : l))

  const findGeo = async (o: Obj, force = false) => {
    if (!author || geo[o.id]?.busy) return
    setGeo((g) => ({ ...g, [o.id]: { busy: true } }))
    try {
      const got = await runDiscoveredGeo(o.id, author, force)
      put({ ...o, geo: mergeChecks(got.checks, o.geo ?? []) })
      const cached = got.checks.length > 0 && got.checks.every((c) => c.cached)
      setGeo((g) => ({ ...g, [o.id]: { cached, partial: got.errors.map((e) => ({ query: e.query, detail: e.detail })) } }))
      const n = geoPicks(got.checks).length
      notify(cached ? 'Кандидаты из кэша — 2ГИС повторно не запрашивался' : n ? `2ГИС: ${n} ${plural(n, ['кандидат', 'кандидата', 'кандидатов'])} — выберите дом при создании карточки` : '2ГИС ничего не нашёл по названию и адресу')
    } catch (e) {
      setGeo((g) => ({ ...g, [o.id]: { err: errorText(e) } }))
    }
  }

  const submit = async () => {
    if (!dlg || dlg.busy || !author) return
    const d = dlg
    if (d.kind === 'create') {
      const problem = createProblem(d.form)
      if (problem) { setDlg({ ...d, err: problem }); return }
    }
    if (d.kind === 'link' && !d.pick) { setDlg({ ...d, err: 'Выберите ЖК из списка' }); return }
    if (d.kind === 'dismiss' && !d.reason.trim()) { setDlg({ ...d, err: 'Укажите причину — она попадёт в журнал' }); return }
    setDlg({ ...d, busy: true, err: undefined })
    try {
      if (d.kind === 'create') {
        const c = await createFromDiscovered(d.o.id, createBody(d.form, author))
        setDlg(null)
        notify(`Карточка ЖК «${c.name}» создана по рекламе — объект привязан, событие в журнале`)
        // карточка должна быть в списке ЖК до перехода к ней: перечитываем ЖК и сводку, новую — добавляем
        await refreshScreening()
        await refreshComplex(c.id)
        setVer((v) => v + 1)
        nav(`/complexes/${encodeURIComponent(c.id)}`, { state: BACK })
        return
      }
      const got = d.kind === 'link' ? await linkDiscovered(d.o.id, author, d.pick!) : await dismissDiscovered(d.o.id, author, d.reason)
      put(got)
      setDlg(null)
      notify(d.kind === 'link' ? `Объект привязан к ЖК «${got.complexName ?? d.pick}» — другое написание названия` : 'Объект отклонён — причина в журнале')
      void refreshScreening()
    } catch (e) {
      setDlg((x) => x && { ...x, busy: false, err: errorText(e) })
    }
  }

  if (mode === 'loading') return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Объекты из рекламы']]} title="Объекты из рекламы" />
      <div className="empty">Подключаемся к серверу…</div>
    </>
  )

  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Новостройки', '/complexes'], ['Объекты из рекламы']]} title="Объекты из рекламы"
        sub={<>ЖК, которых нет ни в справочнике, ни в открытом реестре разрешений: их называет реклама (анализ ИИ публикаций) или импорт.
          По каждому решите: создать карточку ЖК, привязать к существующему или отклонить. Оценка — предварительная, не вывод о нарушении.</>}
        actions={live && <button className="btn g m" onClick={() => setVer((v) => v + 1)}><IcRefresh size={18} />Обновить</button>} />

      <div className="subtabs">
        {DISCOVERED_TABS.map(([k, l]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}{load?.items && <span className="n">{counts[k]}</span>}</button>
        ))}
      </div>

      {ro ? <div className="ai-note" style={{ marginBottom: 16 }}>{NO_DISCOVERED}</div>
        : !author && <div className="ai-note" style={{ marginBottom: 16 }}>Войдите как аналитик: решения по объектам записываются в журнал от вашего имени.</div>}
      {load?.err && <div className="alog-err t-sm">Список не обновился: {load.err} <button className="link" onClick={() => setVer((v) => v + 1)}>Повторить</button></div>}
      {!load ? <div className="empty">Загружаем объекты из рекламы…</div>
        : load.items === null ? <div className="empty">На сервере нет этой функции — обновите бэкенд до версии с объектами из рекламы</div>
          : !load.items && load.err ? null
            : shown.length === 0 ? <div className="empty">{EMPTY[tab]}</div>
              : (
                <div className="disc-list">
                  {shown.map((o) => (
                    <ObjectCard key={o.id} o={o} g={geo[o.id]} author={author} admin={admin} ro={ro}
                      onGeo={(force) => void findGeo(o, force)}
                      onCreate={(candidateId) => setDlg({ kind: 'create', o, form: createForm(o, candidateId) })}
                      onLink={() => setDlg({ kind: 'link', o, q: '' })}
                      onDismiss={() => setDlg({ kind: 'dismiss', o, reason: '' })} />
                  ))}
                </div>
              )}

      {dlg && <ActionDialog dlg={dlg} setDlg={setDlg} onSubmit={() => void submit()} complexes={complexes} developers={developers} user={user} />}
    </>
  )
}

/** Карточка объекта — три шага разбора на направляющей: что нашли (название, цитаты, стадия, признаки продажи) → сверка 2ГИС → решение */
/** ro — офлайн-копия: решения и 2ГИС недоступны, кнопки с подсказкой «доступно с сервером» */
function ObjectCard({ o, g, author, admin, ro, onGeo, onCreate, onLink, onDismiss }: {
  o: Obj; g?: GeoState; author: string; admin: boolean; ro?: boolean
  onGeo: (force: boolean) => void; onCreate: (candidateId?: string) => void; onLink: () => void; onDismiss: () => void
}) {
  const s = o.screening
  const why = s ? topReason(s) : null
  const picks = geoPicks(o.geo)
  const decision = decisionText(o)
  const types = signalTypesLine(o.saleSignals.types)
  const src = sourcesLine(o.sources)
  const lastCheck = (o.geo ?? []).find((c) => !c.purged) ?? null
  const can = !!author && !ro
  // недоступная кнопка объясняет почему
  const blocked = ro ? NO_DISCOVERED : !author ? 'Войдите как аналитик' : undefined
  const st = s?.status ?? ''
  // направляющая разбора: шаг выполнен — зелёная галочка; узел первого шага — статус «Проверки по рекламе»
  const geoDone = !!lastCheck
  const decided = o.status !== 'new'
  return (
    <article className={`disc-card ${st}`} aria-label={`Объект из рекламы «${o.name}»`}>
      <div className={`dc-step ${st}`}>
        <span className="dc-node">{s ? <ScreeningPic status={s.status} size={32} /> : <i>1</i>}</span>
        <div className="dc-step-b">
          <div className="dc-step-h">1 · Что нашли в рекламе{s && <span className={`dc-st ${st}`}>{s.label}</span>}</div>
          <div className="dh">
            <div style={{ minWidth: 0 }}>
              <div className="nm">{o.name}</div>
              <div className="row wrap-f" style={{ gap: 6, marginTop: 6 }}>
                <span className="tag outline">{o.origin === 'import' ? 'импорт по рекламе' : 'найден в рекламе'}</span>
                {o.status === 'linked' && o.complexId && <Link className="tag green" to={`/complexes/${encodeURIComponent(o.complexId)}`} state={BACK}><IcCheck size={12} strokeWidth={3} />ЖК «{o.complexName || o.complexId}»</Link>}
                {o.status === 'dismissed' && <span className="tag gray">отклонён</span>}
              </div>
              {o.aliases.length > 0 && <div className="t-sm c2" style={{ marginTop: 6 }}>Другие написания: {o.aliases.join(', ')}</div>}
            </div>
            <div style={{ textAlign: 'right', flex: 'none' }}>
              <div className="t-h2 num">{o.adsCount}</div>
              <div className="t-xs c2">{plural(o.adsCount, ['упоминание', 'упоминания', 'упоминаний'])} в рекламе</div>
            </div>
          </div>
          {decision && <div className={`ai-decision ${o.status === 'linked' ? 'ok' : 'no'}`} style={{ marginTop: 0 }}>{decision}{o.decidedAt && `, ${when(o.decidedAt)}`}</div>}
          {s && (
            <div className="scr-line row wrap-f" style={{ gap: '4px 12px' }}>
              <span>{why ? <ReasonText r={why} /> : <b>{s.label}</b>}</span>
              <Disclaimer text={s.disclaimer} />
            </div>
          )}
          <div className="disc-grid">
            <div>
              <div className="ai-sub" style={{ marginTop: 0 }}>Цитаты из рекламы <span className="t-sm c2" style={{ fontWeight: 400 }}>{o.quotes.length}</span></div>
              {o.quotes.length ? (
                <div className="ev-list">
                  {o.quotes.map((x, i) => {
                    const p = platformOf(x.platform)
                    return (
                      <Link key={`${x.adId}-${i}`} to={`/ads/${encodeURIComponent(x.adId)}`} state={BACK} className="ev" style={{ gridTemplateColumns: p ? '20px minmax(0, 1fr)' : 'minmax(0, 1fr)' }}>
                        {p && <PlatformIcon p={p} size={20} />}
                        <div>
                          <div className="meta">{[x.account, x.source === 'bot' ? 'заявка жителя' : x.source === 'manual' ? 'ручной ввод' : null, x.date ? fmtShort(x.date) : null, adKey(x.adId)].filter(Boolean).join(' · ')}</div>
                          <div className="q">«{x.quote}»</div>
                          {(x.name && x.name !== o.name) || x.address ? <div className="meta" style={{ marginTop: 2 }}>{[x.name && x.name !== o.name ? `названо: «${x.name}»` : null, x.address ? `адрес: ${x.address}` : null].filter(Boolean).join(' · ')}</div> : null}
                        </div>
                      </Link>
                    )
                  })}
                </div>
              ) : <div className="t-sm c2">Цитат нет{o.origin === 'import' ? ' — объект добавлен импортом' : ''}</div>}
            </div>
            <div className="kv" style={{ alignContent: 'start' }}>
              <span>Адрес в рекламе</span><span>{o.addressHint || 'не назван'}</span>
              <span>Упоминания</span><span>{o.firstSeen ? `с ${when(o.firstSeen)}` : '—'}{o.lastSeen && when(o.lastSeen) !== when(o.firstSeen) ? ` по ${when(o.lastSeen)}` : ''}</span>
              <span>Аккаунты</span><span>{o.accounts.length ? o.accounts.join(', ') : '—'}</span>
              {src && <><span>Откуда</span><span>{src}</span></>}
              <span>Стадия по рекламе</span>
              <span>{stageLine(o.stageHints)}
                {o.stageHints.quotes.slice(0, 2).map((x, i) => <span key={i} className="t-xs c2" style={{ display: 'block', marginTop: 2 }}>{STAGE_LABEL[x.value]}: «{x.quote}»</span>)}</span>
              <span>Признаки продажи</span>
              <span>{saleLine(o.saleSignals, s?.counts.windowDays)}{types && <span className="t-xs c2" style={{ display: 'block', marginTop: 2 }}>{types}</span>}
                {o.saleSignals.lastAdAt && <span className="t-xs c2" style={{ display: 'block' }}>последняя публикация {fmtShort(o.saleSignals.lastAdAt)}</span>}</span>
              {o.sourceUrls.length > 0 && <><span>Ссылки</span><span style={{ overflowWrap: 'anywhere' }}>{o.sourceUrls.slice(0, 3).map((u) => <a key={u} className="link t-sm" href={u} target="_blank" rel="noreferrer" style={{ display: 'block' }}>{u}</a>)}</span></>}
            </div>
          </div>
        </div>
      </div>
      <div className={`dc-step ${geoDone ? 'done' : ''}`}>
        <span className="dc-node"><i>{geoDone ? '✓' : '2'}</i></span>
        <div className="dc-step-b">
          <div className="dc-step-h">2 · Сверка адреса в 2ГИС{!geoDone && <span className="dc-hint">ещё не сверяли — «Найти в 2ГИС»</span>}</div>
          {(g?.err || (g?.partial?.length ?? 0) > 0 || lastCheck) && (
            <div className="disc-geo">
              <div className="row between wrap-f" style={{ gap: 8 }}>
                <span className="bold row" style={{ gap: 8 }}><PlatformIcon p="2gis" size={20} />Кандидаты 2ГИС{picks.length > 0 && <span className="c2" style={{ fontWeight: 400 }}>· {picks.length}</span>}</span>
                {lastCheck && <span className="t-xs c2">{cacheLine(lastCheck, fmtStamp)}{g?.cached ? ' · ответ из кэша' : ''}</span>}
              </div>
              {g?.err && <div className="ai-note err" style={{ marginTop: 8 }}>Поиск в 2ГИС не выполнен: {g.err}</div>}
              {g?.partial?.map((x) => <div key={x.query} className="ai-note warn" style={{ marginTop: 8 }}>Запрос «{x.query}» не выполнен: {plain(x.detail)}</div>)}
              {lastCheck && !picks.length && <div className="t-sm c2" style={{ marginTop: 6 }}>2ГИС не нашёл подходящих домов — создайте карточку без координат и сверьте адрес позже</div>}
              {picks.length > 0 && (
                <div className="ai-list">
                  {picks.slice(0, 5).map(({ cand }) => {
                    const d = candidateDistance(cand)
                    return (
                      <div key={cand.id} className="ai-item">
                        <div className="row between" style={{ gap: 8, alignItems: 'flex-start' }}>
                          <div style={{ minWidth: 0 }}>
                            <div className="bold">{candidateTitle(cand)}</div>
                            {[cand.fullName, cand.addressName].find((x) => x && x !== candidateTitle(cand)) && <div className="t-sm">{[cand.fullName, cand.addressName].find((x) => x && x !== candidateTitle(cand))}</div>}
                            <div className="t-xs c2" style={{ marginTop: 2 }}>{[cand.purpose, fmtPoint(candidatePoint(cand)), d === null ? null : `в ${fmtDist(d)}`].filter(Boolean).join(' · ')}</div>
                          </div>
                          <div className="row wrap-f" style={{ gap: 6, justifyContent: 'flex-end' }}>
                            {cand.recommended && <span className="tag green" title="Единственный точный дом в Усть-Каменогорске — подсказка, а не выбор">{GEO_COPY.recommended}</span>}
                            <span className={`tag ${isExact(cand) ? 'outline' : 'yellow'}`}>{isExact(cand) && <IcCheck size={12} strokeWidth={3} />}{isExact(cand) ? 'точный дом' : precisionLabel(cand.precision)}</span>
                            {o.status !== 'linked' && <button className="btn o s" disabled={!can} title={blocked} onClick={() => onCreate(cand.id)}>Карточка с этой точкой</button>}
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      <div className={`dc-step last ${decided ? 'done' : ''}`}>
        <span className="dc-node"><i>{decided ? '✓' : '3'}</i></span>
        <div className="dc-step-b">
          <div className="dc-step-h">3 · Решение аналитика{!decided && <span className="dc-hint">создать карточку, привязать или отклонить</span>}</div>
          <div className="disc-acts">
            {o.status === 'linked' && o.complexId && <Link className="btn d m" to={`/complexes/${encodeURIComponent(o.complexId)}`} state={BACK}><IcBuilding size={18} />Карточка ЖК</Link>}
            {o.status !== 'linked' && <button className="btn g m" disabled={!can || g?.busy} title={blocked} onClick={() => onGeo(false)}><IcSearch size={18} />{g?.busy ? 'Ищем в 2ГИС…' : 'Найти в 2ГИС'}</button>}
            {o.status !== 'linked' && admin && !ro && lastCheck && <button className="btn g m" disabled={!can || g?.busy} onClick={() => onGeo(true)} title="Запросить 2ГИС заново, мимо кэша: расходует квоту ключа. Только для администратора"><IcRefresh size={18} />{GEO_COPY.force}</button>}
            {o.status !== 'linked' && <button className="btn y m" disabled={!can} title={blocked} onClick={() => onCreate()}><IcBuilding size={18} />Создать карточку ЖК</button>}
            <button className="btn g m" disabled={!can} title={blocked} onClick={onLink}>{o.status === 'linked' ? 'Привязать к другому ЖК' : 'Это существующий ЖК'}</button>
            {o.status !== 'dismissed' && <button className="btn g m" disabled={!can} title={blocked} onClick={onDismiss}><IcClose size={18} />Не ЖК — отклонить</button>}
            {ro && <span className="t-xs c2" style={{ alignSelf: 'center' }}>доступно с сервером</span>}
          </div>
        </div>
      </div>
    </article>
  )
}

/** Окна решений: создать карточку, привязать к существующему ЖК, отклонить */
function ActionDialog({ dlg, setDlg, onSubmit, complexes, developers, user }: {
  dlg: Dialog; setDlg: (d: Dialog | null) => void; onSubmit: () => void
  complexes: Complex[]; developers: ReturnType<typeof useStore>['developers']; user: ReturnType<typeof useStore>['user']
}) {
  const close = () => setDlg(null)
  const o = dlg.o
  const who = user && <Cell icon={<span className="ava">{user.short}</span>} title={user.name} sub={`${user.role} · автор решения, дата и время попадут в журнал`} />
  const err = dlg.err && <div className="alog-err t-sm" style={{ margin: '8px 24px 0' }}>Не сохранено: {dlg.err}</div>

  if (dlg.kind === 'create') {
    const f = dlg.form
    const set = (patch: Partial<CreateForm>) => setDlg({ ...dlg, form: { ...f, ...patch }, err: undefined })
    const picks = geoPicks(o.geo)
    return (
      <Modal title="Создать карточку ЖК" onClose={close} wide
        footer={<><button className="btn g" onClick={close}>Отмена</button><button className="btn y" disabled={dlg.busy} onClick={onSubmit}>{dlg.busy ? 'Создаём…' : 'Создать карточку'}</button></>}>
        <div className="box od-note t-sm" style={{ margin: '0 24px 12px', background: 'var(--amber-bg)', color: 'var(--amber-ink)' }}>
          <IcMegaphone size={18} />
          <div>Карточка будет с пометкой <Origin kind="advertised" label="по рекламе" />: в открытом реестре разрешений её нет, данные не проверены. Формальный риск не оценивается до проверенных сведений; «Проверка по рекламе» — по упоминаниям этого объекта.</div>
        </div>
        <div style={{ padding: '0 24px' }}>
          <div className="frow"><label htmlFor="dc-name">Название ЖК</label><input id="dc-name" className="input" value={f.name} maxLength={255} onChange={(e) => set({ name: e.target.value })} /></div>
          <div className="frow"><label htmlFor="dc-addr">Адрес</label><input id="dc-addr" className="input" value={f.address} maxLength={255} placeholder="Улица и дом — если известны" onChange={(e) => set({ address: e.target.value })} /></div>
          <div className="frow"><label htmlFor="dc-dev">Застройщик</label><input id="dc-dev" className="input" value={f.developerName} maxLength={255} placeholder="Не установлен — оставьте пустым" onChange={(e) => set({ developerName: e.target.value })} /></div>
          <div className="frow"><label htmlFor="dc-bin">БИН застройщика</label><input id="dc-bin" className="input num" value={f.developerBin} maxLength={12} inputMode="numeric" placeholder="12 цифр — если известен" onChange={(e) => set({ developerBin: e.target.value.replace(/\D/g, '') })} /></div>
          <div className="frow top">
            <label>Точка на карте</label>
            <div>
              {picks.length === 0 && <div className="t-sm c2" style={{ padding: '10px 0' }}>Кандидатов 2ГИС нет — нажмите «Найти в 2ГИС» на карточке объекта или создайте ЖК без координат и сверьте адрес в его карточке.</div>}
              {[{ id: '', title: 'Без координат — сверить адрес в 2ГИС позже', sub: 'ЖК попадёт в список «Без координат» на карте' },
                ...picks.map(({ cand }) => ({ id: cand.id, title: candidateTitle(cand), sub: [cand.fullName !== candidateTitle(cand) ? cand.fullName : null, isExact(cand) ? 'точный дом' : precisionLabel(cand.precision), fmtPoint(candidatePoint(cand))].filter(Boolean).join(' · ') }))]
                .map((x) => (
                  <div key={x.id || 'none'} className={`ai-pick ${f.candidateId === x.id ? 'on' : ''}`} onClick={() => set({ candidateId: x.id })}>
                    <button type="button" className="ai-pick-head" role="radio" aria-checked={f.candidateId === x.id} onClick={(e) => { e.stopPropagation(); set({ candidateId: x.id }) }}>
                      <span><span className="bold">{x.title}</span><span className="t-xs c2" style={{ display: 'block', marginTop: 2 }}>{x.sub}</span></span>
                      <span className={`ai-radio ${f.candidateId === x.id ? 'on' : ''}`}>{f.candidateId === x.id && <IcCheck size={12} strokeWidth={3} />}</span>
                    </button>
                  </div>
                ))}
            </div>
          </div>
        </div>
        {err}
        {who}
      </Modal>
    )
  }

  if (dlg.kind === 'link') {
    const list = pickComplexes(dlg.q, complexes, developers)
    const picked = complexes.find((c) => c.id === dlg.pick)
    return (
      <Modal title="Это существующий ЖК" onClose={close}
        footer={<><button className="btn g" onClick={close}>Отмена</button><button className="btn y" disabled={dlg.busy || !picked} onClick={onSubmit}>{dlg.busy ? 'Привязываем…' : picked ? 'Привязать к выбранному ЖК' : 'Выберите ЖК'}</button></>}>
        <p className="t-sm" style={{ padding: '0 24px 10px', lineHeight: '20px' }}>«{o.name}» станет другим написанием выбранного ЖК: упоминания в рекламе пойдут в его «Проверку по рекламе» как гипотезы по публикациям.</p>
        <div style={{ padding: '0 24px 10px' }}>
          <label className="fsearch" style={{ height: 44 }}><IcSearch size={18} />
            <input autoFocus placeholder="Название, адрес, застройщик или БИН" value={dlg.q} aria-label="Поиск ЖК" onChange={(e) => setDlg({ ...dlg, q: e.target.value, err: undefined })} />
          </label>
        </div>
        <div className="pick-list" role="radiogroup" aria-label="ЖК">
          {list.length === 0 && <div className="t-sm c2" style={{ padding: 16 }}>Ничего не найдено — уточните запрос или создайте карточку ЖК</div>}
          {list.map((c) => (
            <button key={c.id} type="button" role="radio" aria-checked={dlg.pick === c.id} className={`ai-pick ai-pick-head ${dlg.pick === c.id ? 'on' : ''}`}
              style={{ display: 'flex', alignItems: 'center' }} onClick={() => setDlg({ ...dlg, pick: c.id, err: undefined })}>
              <span className="row" style={{ gap: 10, minWidth: 0 }}>
                {c.screening ? <ScreeningPic status={c.screening.status} /> : <span className="scr-pic unknown" aria-hidden />}
                <span style={{ minWidth: 0 }}><span className="bold">ЖК «{c.name}»</span><span className="t-xs c2" style={{ display: 'block' }}>{c.address || 'адрес не указан'}{c.id === o.complexId ? ' · привязан сейчас' : ''}</span></span>
              </span>
              <span className={`ai-radio ${dlg.pick === c.id ? 'on' : ''}`}>{dlg.pick === c.id && <IcCheck size={12} strokeWidth={3} />}</span>
            </button>
          ))}
        </div>
        {picked && <div className="t-sm" style={{ padding: '10px 24px 0' }}><span className="c2">Выбран:</span> <b className="bold">ЖК «{picked.name}»</b>{picked.address && <span className="c2"> · {picked.address}</span>}</div>}
        {err}
        {who}
      </Modal>
    )
  }

  return (
    <Modal title="Не ЖК — отклонить" onClose={close}
      footer={<><button className="btn g" onClick={close}>Отмена</button><button className="btn d" disabled={dlg.busy || !dlg.reason.trim()} onClick={onSubmit}>{dlg.busy ? 'Отклоняем…' : 'Отклонить'}</button></>}>
      <p className="t-sm" style={{ padding: '0 24px 10px', lineHeight: '20px' }}>«{o.name}» больше не будет считаться ЖК из рекламы{o.status === 'linked' ? '; привязка к ЖК снимется, карточка ЖК останется' : ''}. Причина обязательна — она попадёт в журнал.</p>
      <div className="chips" style={{ padding: '0 24px 10px' }}>
        {DISMISS_PRESETS.map((r) => <button key={r} type="button" className={`chip ${dlg.reason === r ? 'on' : ''}`} style={{ height: 32, fontSize: 14 }} onClick={() => setDlg({ ...dlg, reason: r, err: undefined })}>{r}</button>)}
      </div>
      <div style={{ padding: '0 24px 4px' }}>
        <label className="t-sm c2" htmlFor="dd-why">Причина</label>
        <textarea id="dd-why" className="input" style={{ marginTop: 6, minHeight: 96 }} maxLength={REASON_MAX} value={dlg.reason}
          placeholder="Например: это подборка квартир агентства, а не жилой комплекс" onChange={(e) => setDlg({ ...dlg, reason: e.target.value, err: undefined })} />
      </div>
      {err}
      {who}
    </Modal>
  )
}
