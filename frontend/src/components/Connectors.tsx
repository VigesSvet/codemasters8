// «Подключения» на странице «Источники» (integration-spec §13.2): фактическое состояние сборщиков публикаций, приёма заявок,
// геосервиса и анализатора — с сервера (GET /api/sources/connectors). В демо-режиме поддельных статусов нет.
// Настройка и ручной запуск — только в режиме администратора: токен SAQ_ADMIN_TOKEN вводит человек, проверяет сервер,
// хранится он только в этой вкладке (lib/admin.ts) и уходит заголовком лишь в маршруты администратора.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import type { CollectAllResult, CollectionRun, ConfigField, Connector, ConnectorConfig, ConnectorGroup, ConnectorPatch, ConnectorsResponse } from '../data/types'
import { checkAdmin, clearAdminToken, isAdmin, onAdminChange, setAdminToken, tokenProblem } from '../lib/admin'
import { ApiError, errorText } from '../lib/api'
import {
  COPY, CONFIG_META, RUN_TONE, TRIGGER_LABEL, accountState, anyBusy, blockReason, collectAll, collectAllToast, collectConnector, collectable,
  collectedLine, configList, configSummary, effectiveStatus, finishedWithData, fmtUsd, getConnectors, getRun, groupConnectors, hasNewData, hhmm,
  iconPlatform, intervalProblem, intervalText, isTerminal, joinLines, lastNotes, lastOkRow, listProblem, missingLine, nextRun, olxDisconnect,
  olxStart, parseLines, patchConnector, retryLine, runLine, runSummary, runTitle, runsSeen, runsToast, sourcesWord, splitMissing, statusText,
  testConnector, testOkLine, toneOf, when, workingCollectors, type Action, type RunsSeen,
} from '../lib/connectors'
import { useStore } from '../lib/store'
import {
  IcCheck, IcGear, IcInfo, IcLock, IcPlay, IcPlugConnection, IcRefresh, IcShield, IcSparkles, IcTriangleExclamation,
} from '../ui/icons'
import { HeroTile, Modal, PlatformIcon } from './ui'

/** Пока идут проверки или сбор — перечитываем подключения */
const POLL_MS = 5000
/** Ход запуска после «Проверить подключение» / «Собрать сейчас» */
const RUN_POLL_MS = 2000
const NO_USER = 'Войдите, чтобы действовать от своего имени'
/** Ограничение OLX показываем всегда — даже если сервер его не прислал (integration-spec §10.8) */
const LIMITED_NOTE = 'Partner API даёт доступ только к ресурсам своего авторизованного аккаунта — чужие объявления так не собрать.'

/** Пояснение к группе: геосервис, анализатор и OLX — не равнозначные сборщики рекламы */
const GROUP_NOTE: Record<ConnectorGroup, string> = {
  collectors: 'Читают публичные аккаунты, каналы и выдачу площадок из списка, который задаёт администратор. Таргетированную рекламу так не увидеть.',
  intake: 'Сюда присылают: Telegram-бот — заявки жителей, внешние сборщики — готовые публикации. Доступ — по серверному токену.',
  geo: 'Не сбор рекламы: кандидаты адреса для карточки ЖК. Сверку запускает аналитик в карточке ЖК, решение принимает он же.',
  analyzer: 'Анализ публикаций сейчас выполняют правила saq-ai-1 на сервере, без внешних API.',
  limited: 'Доступ только к ресурсам своего авторизованного аккаунта — это не сборщик чужих объявлений.',
}

// ---------- Общее состояние раздела: страница кладёт кнопку «Обновить всё» в шапку, а карточки — ниже ----------

type Load = { data: ConnectorsResponse | null; err?: string; stale?: boolean }
type All = { busy?: boolean; res?: CollectAllResult; err?: string }
type Finished = { name: string; run: CollectionRun }

/** true — компонент уже снят (модальное окно закрыли, пока шёл запрос): итог запроса — тостом, а не в закрытое окно */
function useGone() {
  const gone = useRef(false)
  useEffect(() => {
    gone.current = false
    return () => { gone.current = true }
  }, [])
  return gone
}

interface Conn {
  /** Есть бэкенд (mode api) */
  live: boolean
  /** null — ещё не загружено */
  load: Load | null
  list: Connector[]
  reload: () => void
  /** Токен администратора в этой вкладке и администрирование настроено на сервере */
  admin: boolean
  adminConfigured: boolean
  author: string | null
  /** Что поставит в очередь «Обновить всё» */
  ready: Connector[]
  all: All
  runAll: () => void
  closeAll: () => void
  nameOf: (code: string) => string
  replace: (c: Connector) => void
  patchLocal: (code: string, patch: Partial<Connector>) => void
  /** Запуски завершились: с новыми данными — перечитать ленту публикаций и сообщить (каждый запуск — один раз) */
  settle: (items: Finished[]) => void
}

const Ctx = createContext<Conn | null>(null)

function useConn(): Conn {
  const x = useContext(Ctx)
  if (!x) throw new Error('Connectors: нет ConnectorsProvider')
  return x
}

/** Состояние подключений для всей страницы «Источники» */
export function ConnectorsProvider({ children }: { children: ReactNode }) {
  const { mode, revision, user, notify, refreshAds } = useStore()
  const live = mode === 'api'
  const [load, setLoad] = useState<Load | null>(null)
  const [ver, setVer] = useState(0)
  const [token, setToken] = useState(() => isAdmin())
  const [all, setAll] = useState<All>({})
  const reload = useCallback(() => setVer((v) => v + 1), [])

  // Сбор с новыми данными закончился — перечитываем ленту (иначе «Публикаций в ленте», очередь и карточки ЖК их не увидят).
  // Ловим и по панели запуска, и по опросу подключений: «Обновить всё» панелей не открывает, «Скрыть» панель снимает.
  // handled — номера уже учтённых запусков, чтобы лента и тост не повторялись
  const handled = useRef(new Set<number>())
  const seen = useRef<RunsSeen | null>(null)
  const settle = useCallback((items: Finished[]) => {
    const fresh = items.filter((x) => isTerminal(x.run) && hasNewData(x.run) && !handled.current.has(x.run.id))
    if (!fresh.length) return
    for (const x of fresh) handled.current.add(x.run.id)
    const toast = runsToast(fresh)
    void refreshAds().finally(() => { if (toast) notify(toast) })
  }, [refreshAds, notify])
  const settleRef = useRef(settle)
  useEffect(() => { settleRef.current = settle }, [settle])

  // при входе, после сохранённых изменений (revision) и после восстановления связи — перечитываем
  useEffect(() => {
    if (!live) return
    let alive = true
    getConnectors().then(
      // null — нет связи или сервер без сбора публикаций: прежний список не затираем
      (data) => {
        if (!alive) return
        setLoad((l) => (data ? { data } : l?.data ? { data: l.data, stale: true } : { data: null }))
        if (!data) return
        // первая загрузка только запоминает последние запуски: прежние сборы ленту не перечитывают
        settleRef.current(finishedWithData(seen.current, data.connectors))
        seen.current = runsSeen(data.connectors)
      },
      (e: unknown) => { if (alive) setLoad((l) => ({ data: l?.data ?? null, err: errorText(e) })) },
    )
    return () => { alive = false }
  }, [live, ver, revision])

  // проверка, сбор в очереди или идёт — опрашиваем каждые 5 с
  const busy = !!load?.data && anyBusy(load.data.connectors)
  useEffect(() => {
    if (!live || !busy) return
    const t = setTimeout(reload, POLL_MS)
    return () => clearTimeout(t)
  }, [live, busy, load, reload])

  // вход и выход из режима администратора (и сброс токена после отказа сервера)
  useEffect(() => onAdminChange(setToken), [])

  const value = useMemo<Conn>(() => {
    const list = load?.data?.connectors ?? []
    const adminConfigured = !!load?.data?.adminConfigured
    const author = user?.name ?? null
    const nameOf = (code: string) => list.find((c) => c.code === code)?.name ?? code
    const edit = (code: string, f: (c: Connector) => Connector) =>
      setLoad((l) => (l?.data ? { ...l, data: { ...l.data, connectors: l.data.connectors.map((c) => (c.code === code ? f(c) : c)) } } : l))
    return {
      live, load, list, reload, adminConfigured, author, nameOf, all,
      admin: live && token && adminConfigured,
      ready: collectable(list),
      runAll: () => {
        if (!author || all.busy) return
        setAll({ busy: true })
        collectAll(author).then(
          (res) => { setAll({ res }); notify(collectAllToast(res, nameOf)); reload() },
          (e: unknown) => { setAll({ err: errorText(e) }); notify(`«${COPY.collectAll}» не выполнено: ${errorText(e)}`) },
        )
      },
      closeAll: () => setAll({}),
      replace: (c) => { if (c && typeof c.code === 'string' && typeof c.status === 'string') edit(c.code, () => c); else reload() },
      patchLocal: (code, patch) => edit(code, (c) => ({ ...c, ...patch })),
      settle,
    }
  }, [live, load, token, all, user, notify, reload, settle])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

// ---------- Шапка страницы ----------

/** Кнопка «Обновить всё» в шапке: сбор всех включённых и проверенных сборщиков (администратор) */
export function CollectAllButton() {
  const x = useConn()
  const reason = !x.live ? COPY.demo
    : !x.load?.data ? 'Состояние подключений не загрузилось'
      : !x.adminConfigured ? COPY.adminNotConfigured
        : !x.admin ? COPY.needAdmin
          : !x.author ? NO_USER
            : !x.ready.length ? 'Нет включённых и проверенных сборщиков: сначала «Проверить подключение» и включите сбор по расписанию'
              : null
  const btn = (
    <button className="btn y m" disabled={!!reason || x.all.busy} onClick={x.runAll}>
      <IcRefresh size={18} />{x.all.busy ? 'Ставим в очередь…' : COPY.collectAll}
    </button>
  )
  return reason ? <span className="lnk-dis" title={reason}>{btn}</span> : btn
}

/** Плитка «Сборщики в работе»: включены и готовы, работают или собирают */
export function CollectorsTile() {
  const x = useConn()
  return (
    <HeroTile icon={<IcPlugConnection size={32} />} label="Сборщики в работе" value={x.load?.data ? workingCollectors(x.list).length : '—'}
      onClick={() => document.getElementById('connectors')?.scrollIntoView({ behavior: 'smooth' })} />
  )
}

// ---------- Раздел «Подключения» ----------

export default function Connectors() {
  const x = useConn()
  const { mode } = useStore()
  const data = x.load?.data
  return (
    <section className="lnk" id="connectors">
      <h2 className="t-h2" style={{ marginBottom: 4 }}>Подключения</h2>
      <p className="c2" style={{ marginBottom: 16, maxWidth: 820 }}>
        Сбор публикаций из соцсетей и площадок, приём заявок и публикаций, геосервис и анализатор. Состояние — фактическое, с сервера:
        «работает» появляется только после успешного сбора; «ключ создан» и «заявка отправлена» так не называются.
      </p>
      {mode === 'loading' ? <div className="ai-note">Подключаемся к серверу…</div>
        : mode === 'demo' ? <div className="ai-note">{COPY.demo}</div>
          : !x.load ? <div className="ai-note">Загружаем состояние подключений…</div>
            : !data ? (
              <div className={`ai-note ${x.load.err ? 'err' : ''}`}>
                {x.load.err ? `Состояние подключений не загрузилось: ${x.load.err}` : COPY.unavailable}{' '}
                <button className="link" onClick={x.reload}>Повторить</button>
              </div>
            )
              : (
                <>
                  <AdminBar />
                  {!data.worker?.alive && (
                    <div className="box warn t-sm" style={{ marginTop: 12, lineHeight: '20px' }}>
                      {COPY.workerDown}{data.worker?.lastSeenAt && <> Последний сигнал воркера: {when(data.worker.lastSeenAt)}.</>}
                    </div>
                  )}
                  <AllResult />
                  {x.load.err && <div className="alog-err t-sm" style={{ marginTop: 12 }}>Состояние не обновилось: {x.load.err} <button className="link" onClick={x.reload}>Повторить</button></div>}
                  {x.load.stale && !x.load.err && <p className="t-xs c2" style={{ marginTop: 10 }}>Состояние не обновилось — нет связи с сервером; показано последнее полученное. <button className="link" onClick={x.reload}>Повторить</button></p>}
                  {anyBusy(x.list) && <p className="t-xs c2" style={{ marginTop: 10 }}>Идут проверки или сбор — состояние обновляется каждые 5 с.</p>}
                  {x.list.length === 0 && <div className="ai-note" style={{ marginTop: 16 }}>Сервер не сообщил ни об одном подключении.</div>}
                  {groupConnectors(x.list).map((g) => (
                    <div key={g.group} className="lnk-group">
                      <h3 className="lnk-gh">{g.title}<span className="n">{g.items.length}</span></h3>
                      <p className="lnk-gnote">{GROUP_NOTE[g.group]}</p>
                      <div className="gcards lnk-grid">{g.items.map((c) => <ConnectorCard key={c.code} c={c} />)}</div>
                    </div>
                  ))}
                </>
              )}
    </section>
  )
}

/** Режим администратора: вход по токену (проверяет сервер), метка и выход */
function AdminBar() {
  const x = useConn()
  const [open, setOpen] = useState(false)
  return (
    <div className="lnk-bar">
      {x.admin ? <IcShield size={22} /> : <IcLock size={22} />}
      <div className="grow">
        {!x.adminConfigured ? <span>{COPY.adminNotConfigured}</span>
          : x.admin ? <span className="row" style={{ gap: '4px 8px', flexWrap: 'wrap' }}><span className="tag green">{COPY.adminTag}</span>Действия записываются в журнал от имени {x.author ?? '—'}</span>
            : <span>Настройка и ручной запуск закрыты</span>}
        <span className="c2">{COPY.adminLine}</span>
      </div>
      {x.adminConfigured && (x.admin
        ? <button className="btn g m" onClick={() => clearAdminToken()}>Выйти</button>
        : <button className="btn d m" onClick={() => setOpen(true)}><IcLock size={16} />{COPY.adminButton}</button>)}
      {open && <AdminModal onClose={() => setOpen(false)} />}
    </div>
  )
}

function AdminModal({ onClose }: { onClose: () => void }) {
  const { notify } = useStore()
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string>()
  // «Отмена», Esc или клик мимо окна, пока сервер проверяет токен, — отказ от входа: ответ сервера токен уже не сохраняет
  const gone = useGone()
  const submit = async (e?: FormEvent) => {
    e?.preventDefault()
    if (busy) return
    const problem = tokenProblem(token)
    if (problem) { setErr(problem); return }
    setBusy(true)
    setErr(undefined)
    try {
      await checkAdmin(token)
    } catch (ex) {
      if (gone.current) return
      setErr(errorText(ex))
      setBusy(false)
      return
    }
    if (gone.current) return
    const kept = setAdminToken(token)
    notify(kept ? 'Режим администратора включён до закрытия вкладки' : 'Режим администратора включён до перезагрузки страницы: хранилище вкладки недоступно')
    onClose()
  }
  return (
    <Modal title={COPY.adminButton} onClose={onClose}
      footer={<>
        <button className="btn g" onClick={onClose}>Отмена</button>
        <button className="btn y" type="submit" form="lnk-admin" disabled={busy || !token.trim()}>{busy ? 'Проверяем…' : 'Войти'}</button>
      </>}>
      <form id="lnk-admin" className="lnk-form" onSubmit={submit}>
        <div className="lnk-fld">
          <label className="t-sm c2" htmlFor="lnk-admin-token">{COPY.adminField}</label>
          <input id="lnk-admin-token" className="input" type="password" autoComplete="off" spellCheck={false} autoFocus value={token}
            onChange={(e) => { setToken(e.target.value); setErr(undefined) }} />
          <span className="t-xs c2" style={{ lineHeight: '17px' }}>{COPY.adminHint}</span>
        </div>
        {err && <div className="alog-err t-sm" style={{ margin: 0 }}>Не вошли: {err}</div>}
        <p className="t-xs c2" style={{ lineHeight: '17px' }}>{COPY.adminLine}</p>
      </form>
    </Modal>
  )
}

/** Итог «Обновить всё»: что поставлено в очередь, что пропущено и почему */
function AllResult() {
  const x = useConn()
  if (x.all.err) return <div className="alog-err t-sm" style={{ marginTop: 12 }}>«{COPY.collectAll}» не выполнено: {x.all.err} <button className="link" onClick={x.closeAll}>Скрыть</button></div>
  const res = x.all.res
  if (!res) return null
  const queued = res.queued ?? []
  const skipped = res.skipped ?? []
  return (
    <div className="lnk-all">
      <div className="row between" style={{ alignItems: 'flex-start' }}>
        <b>{queued.length ? `«${COPY.collectAll}»: в очереди ${queued.length} ${sourcesWord(queued.length)}` : `«${COPY.collectAll}»: ничего не поставлено в очередь`}</b>
        <button className="link t-sm" onClick={x.closeAll}>Скрыть</button>
      </div>
      {queued.length > 0 && <div>{queued.map((q) => `${x.nameOf(q.code)}${q.runId ? ` (запуск №${q.runId})` : ''}`).join(', ')}</div>}
      {skipped.length > 0 && (
        <>
          <div className="c2">Пропущено:</div>
          <ul>{skipped.map((s) => <li key={s.code}><b>{x.nameOf(s.code)}</b> — {s.reason || 'пропущен'}</li>)}</ul>
        </>
      )}
    </div>
  )
}

// ---------- Карточка подключения ----------

type Busy = Action | 'disconnect' | null

/** Последняя ошибка свежее последнего успеха (или успеха не было) */
const freshError = (c: Connector) =>
  !!c.lastError && (!c.lastSuccessAt || !c.lastErrorAt || new Date(c.lastErrorAt).getTime() >= new Date(c.lastSuccessAt).getTime())

function ConnectorCard({ c }: { c: Connector }) {
  const x = useConn()
  const { notify } = useStore()
  const [act, setAct] = useState<Busy>(null)
  const [err, setErr] = useState<string>()
  const [run, setRun] = useState<CollectionRun | null>(null)
  const [cfg, setCfg] = useState(false)
  const [sure, setSure] = useState(false)

  const s = effectiveStatus(c)
  const caps = c.capabilities
  const collector = c.kind === 'collector'
  const plat = iconPlatform(c)
  const why = (a: Action) => blockReason(c, a, x.admin) ?? (x.author ? null : NO_USER)
  // выключить расписание можно и у подключения, с которого убрали ключи
  const toggleWhy = c.enabled && !c.configured && x.admin && caps?.toggle && x.author ? null : why('toggle')
  const account = typeof c.facts?.account === 'string' && c.facts.account.trim() ? c.facts.account : null
  // OLX: токен сохранён — «Отключить аккаунт»; токен не действует (истёк, нет доступа, ошибка) — ещё и «Подключить заново»
  const acct = caps?.connect ? accountState(c) : { linked: false, relink: false }
  const missing = c.missing ?? []
  // сессия Telegram — не ключ в .env: её создаёт вход из консоли сервера (подсказка ниже)
  const { env: envMissing, session: noSession } = splitMissing(missing)
  const tgLogin = c.code === 'telegram' && (noSession || s === 'expired')
  // пояснение сервера без повторов: «Нет на сервере…» уже есть ниже, команды входа Telegram — в подсказке
  const detail = c.statusDetail?.trim()
    && !(missing.length && /^Нет на сервере/i.test(c.statusDetail.trim()))
    && !(tgLogin && c.statusDetail.includes('telegram_login'))
    ? c.statusDetail.trim() : null
  const warnDetail = s === 'partial' || s === 'access_check'
  const limitation = c.limitation?.trim() || (c.group === 'limited' ? LIMITED_NOTE : null)

  const start = async (a: 'test' | 'collect') => {
    if (!x.author || act) return
    setAct(a)
    setErr(undefined)
    try {
      setRun(await (a === 'test' ? testConnector : collectConnector)(c.code, x.author))
      x.reload()
    } catch (e) {
      setErr(errorText(e))
    } finally {
      setAct(null)
    }
  }

  // оптимистичное переключение с откатом
  const flip = async (on: boolean) => {
    if (!x.author || act) return
    setAct('toggle')
    setErr(undefined)
    x.patchLocal(c.code, { enabled: on })
    try {
      x.replace(await patchConnector(c.code, { author: x.author, enabled: on }))
      notify(`«${c.name}»: сбор по расписанию ${on ? 'включён' : 'выключен'} — событие в журнале`)
    } catch (e) {
      x.patchLocal(c.code, { enabled: !on })
      setErr(errorText(e))
      notify(`Не сохранено: ${errorText(e)}`)
    } finally {
      setAct(null)
    }
  }

  const connect = async () => {
    if (!x.author || act) return
    setAct('connect')
    setErr(undefined)
    try {
      const got = await olxStart(x.author)
      window.location.assign(got.authorizeUrl) // уходим на OLX — кнопка остаётся занятой
    } catch (e) {
      setErr(errorText(e))
      setAct(null)
    }
  }

  const disconnect = async () => {
    if (!x.author || act) return
    setAct('disconnect')
    setErr(undefined)
    try {
      await olxDisconnect(x.author)
      notify('OLX отключён: токены удалены на сервере — событие в журнале')
      x.reload()
    } catch (e) {
      setErr(errorText(e))
    } finally {
      setAct(null)
      setSure(false)
    }
  }

  // запуск завершился: состояние перечитываем; новые данные — лента и тост (через общее состояние раздела, один раз на запуск)
  const done = (r: CollectionRun) => {
    x.reload()
    x.settle([{ name: c.name, run: r }])
  }

  const ok = lastOkRow(c)
  const next = nextRun(c)
  const rows: [string, ReactNode][] = []
  if (c.configFields?.length) rows.push(['Что собираем', configSummary(c)])
  if (collector && c.intervalMin) rows.push(['Период', intervalText(c.intervalMin)])
  if (account) rows.push(['Аккаунт', account])
  if (ok) rows.push([ok.label, <span key="ok" title={ok.at ?? undefined}>{when(ok.at)}</span>])
  if (c.lastError) {
    rows.push(['Последняя ошибка', freshError(c)
      ? <span key="err" className="alog-err">{c.lastError}{c.lastErrorAt && ` · ${when(c.lastErrorAt)}`}</span>
      : <span key="err" className="c2">{c.lastError}{c.lastErrorAt && ` · ${when(c.lastErrorAt)}`} (после неё был успешный запуск)</span>])
  }
  if (collector || c.kind === 'intake') rows.push(['Собрано', collectedLine(c.counts ?? { items: 0, ads: 0, media: 0 })])
  if (c.lastRun) rows.push(['Последний запуск', runLine(c.lastRun)])
  // только если планировщик его действительно поставит; остановленный и ждущий ручного действия — «не запланирован — …»
  if (next) rows.push(['Следующий запуск', next.at ? <span key="next" title={next.at}>{next.text}</span> : <span key="next" className="c2">{next.text}</span>])

  return (
    <div className="devcard lnk-card">
      <div className="lnk-head">
        {plat ? <PlatformIcon p={plat} size={48} />
          : <span className="lnk-ico">{c.code === 'openai' || c.kind === 'analyzer' ? <IcSparkles size={22} /> : <IcPlugConnection size={22} />}</span>}
        <div className="who">
          <div className="t-h3">{c.name}</div>
          <div className="t-sm c2" style={{ marginTop: 2 }}>{c.via}</div>
          <div className="lnk-tags">
            <span className={`tag ${toneOf(c)}`} title={detail ?? undefined}>{statusText(c)}</span>
            {collector && !c.enabled && <span className="tag gray">{COPY.disabled}</span>}
            {c.stopped && <span className="tag red">{COPY.stoppedTag}</span>}
            {c.paid && <span className="tag outline">{COPY.paid}</span>}
            {c.contractVerified === false && <span className="tag yellow">{COPY.contractUnverified}</span>}
          </div>
        </div>
      </div>

      {collector && caps?.toggle && (
        <div className="lnk-sw">
          <span>{COPY.toggle}</span>
          <Switch on={c.enabled} label={COPY.toggle} disabled={!!toggleWhy || act !== null} title={toggleWhy} onChange={flip} />
        </div>
      )}

      {c.stopped && <div className="alog-err t-sm" style={{ margin: 0 }}>{COPY.stopped}</div>}
      {detail && (warnDetail ? <div className="box warn t-sm">{detail}</div> : <div className="t-sm c2" style={{ lineHeight: '19px' }}>{detail}</div>)}
      {s === 'access_check' && !detail && <div className="box warn t-sm">{COPY.accessCheck}</div>}
      {envMissing.length > 0 && <div className="t-sm c2" style={{ lineHeight: '19px' }}>{missingLine(envMissing)}</div>}
      {noSession && <div className="t-sm c2" style={{ lineHeight: '19px' }}>{COPY.noSession}</div>}
      {tgLogin && <TelegramLogin />}

      {rows.length > 0 && (
        <div className="kv lnk-kv">
          {rows.flatMap(([k, v]) => [<span key={`k-${k}`}>{k}</span>, <span key={`v-${k}`} className="long">{v}</span>])}
        </div>
      )}

      {c.kind === 'geo' && <div className="lnk-lim"><IcInfo size={16} />Сверка адреса — в карточке ЖК: «Сверить адрес» запускает аналитик, кандидата принимает тоже он.</div>}
      {limitation && (
        <div className={`lnk-lim ${c.group === 'limited' ? 'strong' : ''}`}>
          {c.group === 'limited' ? <IcTriangleExclamation size={16} /> : <IcInfo size={16} />}{limitation}
        </div>
      )}

      {run && <RunPanel key={run.id} initial={run} okLine={testOkLine(c)} onDone={done} onClose={() => setRun(null)} />}
      {err && <div className="alog-err t-sm" style={{ margin: 0 }}>{err}</div>}

      {(caps?.test || caps?.collect || caps?.configure || caps?.connect) && (
        <div className="lnk-acts">
          {caps.connect && (!acct.linked || acct.relink) && (
            <Act cls="y" why={why('connect')} busy={act !== null} onClick={connect} icon={<IcPlugConnection />}
              label={act === 'connect' ? 'Переходим на OLX…' : acct.relink ? COPY.olxRelink : COPY.olxConnect} />
          )}
          {caps.test && <Act why={why('test')} busy={act !== null} onClick={() => start('test')} icon={<IcCheck />} label={act === 'test' ? 'Ставим проверку…' : COPY.test} />}
          {caps.collect && <Act cls="y" why={why('collect')} busy={act !== null} onClick={() => start('collect')} icon={<IcPlay />} label={act === 'collect' ? 'Ставим сбор…' : COPY.collect} />}
          {caps.configure && <Act why={why('configure')} busy={act !== null} onClick={() => setCfg(true)} icon={<IcGear />} label={COPY.configure} />}
          {caps.connect && acct.linked && (sure
            ? (
              <span className="lnk-confirm">
                Отключить аккаунт OLX? Токены будут удалены на сервере.
                <button className="btn s r" disabled={act !== null} onClick={disconnect}>{act === 'disconnect' ? 'Отключаем…' : 'Отключить'}</button>
                <button className="btn s g" disabled={act !== null} onClick={() => setSure(false)}>Отмена</button>
              </span>
            )
            : <Act why={why('connect')} busy={act !== null} onClick={() => setSure(true)} label="Отключить аккаунт" />)}
        </div>
      )}

      {cfg && <ConfigModal c={c} onClose={() => setCfg(false)} />}
    </div>
  )
}

/** Кнопка действия; неактивная — с подсказкой, почему (title на обёртке) */
function Act({ label, icon, why, busy, onClick, cls = 'g' }: { label: string; icon?: ReactNode; why: string | null; busy: boolean; onClick: () => void; cls?: string }) {
  const btn = <button className={`btn s ${cls}`} disabled={!!why || busy} onClick={onClick}>{icon}{label}</button>
  return why ? <span className="lnk-dis" title={why}>{btn}</span> : btn
}

/** Переключатель с disabled (Toggle из ui.tsx его не принимает) */
function Switch({ on, onChange, label, disabled, title }: { on: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean; title?: string | null }) {
  return (
    <span className={disabled ? 'lnk-dis' : undefined} title={title ?? undefined} style={{ display: 'inline-flex' }}>
      <button type="button" className={`toggle ${on ? 'on' : ''}`} role="switch" aria-checked={on} aria-label={label} disabled={disabled} onClick={() => onChange(!on)} />
    </span>
  )
}

/** Вход в Telegram выполняется в консоли сервера — сессия в браузер не попадает */
function TelegramLogin() {
  return (
    <div className="lnk-cli">
      Вход в Telegram — на сервере, из консоли (нужны TG_API_ID, TG_API_HASH и TG_PHONE в .env). Запросить код и ввести его:
      <code>python -m app.integrations.telegram_login --send-code</code>
      <code>python -m app.integrations.telegram_login --code &lt;код&gt;</code>
      В Docker: <code>docker compose run --rm worker python -m app.integrations.telegram_login …</code>
      С паролем двухфакторной защиты — ещё <b>--password</b>. Сессия хранится на сервере и в браузер не передаётся.
    </div>
  )
}

// ---------- Панель запуска ----------

const noteTime = (iso: string) => (Number.isNaN(new Date(iso).getTime()) ? iso : hhmm(iso))

/** Ход проверки или сбора: опрос GET /api/collection-runs/{id} каждые 2 с до конечного статуса.
 *  okLine — итог успешной проверки: «можно включать сбор» — только у сборщика */
function RunPanel({ initial, okLine, onDone, onClose }: { initial: CollectionRun; okLine: string; onDone: (r: CollectionRun) => void; onClose: () => void }) {
  const [run, setRun] = useState(initial)
  const [err, setErr] = useState<string>()
  const [lost, setLost] = useState(false)
  const [tick, setTick] = useState(0)
  const fired = useRef(false)
  const fin = isTerminal(run)

  useEffect(() => {
    if (fin || lost) return
    let alive = true
    const t = setTimeout(() => {
      getRun(run.id).then(
        (r) => { if (alive) { setRun(r); setErr(undefined); setTick((n) => n + 1) } },
        (e: unknown) => {
          if (!alive) return
          setErr(errorText(e))
          // «Запуск не найден» — опрашивать нечего; прочие ошибки (нет связи) — пробуем ещё
          if (e instanceof ApiError && e.status === 404) setLost(true)
          else setTick((n) => n + 1)
        },
      )
    }, RUN_POLL_MS)
    return () => { alive = false; clearTimeout(t) }
  }, [run.id, fin, lost, tick])

  useEffect(() => {
    if (!fin || fired.current) return
    fired.current = true
    onDone(run)
  }, [fin, run, onDone])

  const c = run.counts
  const notes = lastNotes(run)
  const retry = retryLine(run)
  const media = c && (c.mediaSaved || c.mediaFailed)
    ? `файлов сохранено ${c.mediaSaved}${c.mediaFailed ? `, не загружено ${c.mediaFailed}` : ''}` : null
  return (
    <div className={`lnk-run ${fin ? RUN_TONE[run.status] ?? '' : ''}`}>
      <div className="lnk-run-h">
        <span className="row" style={{ gap: 8 }}>{!fin && !lost && <span className="lnk-dot" />}<b>{runTitle(run)}</b></span>
        <button className="link t-sm" onClick={onClose}>Скрыть</button>
      </div>
      <div className="t-xs c2">
        {TRIGGER_LABEL[run.trigger] ?? run.trigger}{run.requestedBy && ` · ${run.requestedBy}`}{run.createdAt && ` · ${when(run.createdAt)}`}
      </div>
      {!fin && !lost && <div className="t-sm c2">{COPY.polling}</div>}
      {fin && run.trigger !== 'test' && <div>{runSummary(run)}{media && ` · ${media}`}</div>}
      {fin && run.trigger === 'test' && run.status === 'succeeded' && <div>{okLine}</div>}
      {typeof run.costUsd === 'number' && <div className="t-sm">Стоимость запуска: {fmtUsd(run.costUsd)}</div>}
      {run.status === 'access_check' && <div className="box warn t-sm">{COPY.accessCheck}</div>}
      {run.error && <div className="alog-err t-sm">{run.error}</div>}
      {notes.length > 0 && (
        <ul className="lnk-notes">
          {notes.map((n, i) => <li key={`${n.at}-${i}`} className={n.level}><span className="at">{noteTime(n.at)}</span><span>{n.text}</span></li>)}
        </ul>
      )}
      {retry && <div className="t-sm c2">{retry[0].toUpperCase() + retry.slice(1)}</div>}
      {err && <div className="alog-err t-sm">{lost ? 'Запуск не найден: ' : 'Ход запуска не обновился: '}{err}</div>}
    </div>
  )
}

// ---------- Настройка подключения ----------

const sameList = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((v, i) => v === b[i])
const RPP_MAX = 50

function ConfigModal({ c, onClose }: { c: Connector; onClose: () => void }) {
  const x = useConn()
  const { notify } = useStore()
  const fields = (c.configFields ?? []).filter((f): f is ConfigField => Object.hasOwn(CONFIG_META, f))
  const opts = c.options ?? []
  const withRpp = opts.includes('resultsPerPage')
  const withVideos = opts.includes('downloadVideos')
  const withPeriod = c.kind === 'collector' && c.intervalMin != null
  const [lo, hi] = c.intervalBounds?.length === 2 ? c.intervalBounds : [5, 10080]
  const rpp0 = typeof c.config?.resultsPerPage === 'number' ? String(c.config.resultsPerPage) : ''
  const videos0 = c.config?.downloadVideos === true

  const [texts, setTexts] = useState<Partial<Record<ConfigField, string>>>(() => Object.fromEntries(fields.map((f) => [f, joinLines(configList(c, f))])))
  const [period, setPeriod] = useState(c.intervalMin != null ? String(c.intervalMin) : '')
  const [rpp, setRpp] = useState(rpp0)
  const [videos, setVideos] = useState(videos0)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string>()
  // окно закрыли, пока сохранялось: итог — тостом (ошибку в закрытом окне никто не увидит)
  const gone = useGone()

  const lists = Object.fromEntries(fields.map((f) => [f, parseLines(texts[f] ?? '')])) as Record<ConfigField, string[]>
  const changed = fields.filter((f) => !sameList(lists[f], configList(c, f)))
  const min = Number(period.trim())
  const periodChanged = withPeriod && period.trim() !== String(c.intervalMin ?? '')
  const rppN = Number(rpp.trim())
  const rppChanged = withRpp && rpp.trim() !== rpp0
  const videosChanged = withVideos && videos !== videos0
  const problems = [
    ...fields.map((f) => listProblem(f, lists[f])),
    periodChanged ? intervalProblem(period.trim() ? min : Number.NaN, c.intervalBounds) : null,
    rppChanged && (!/^\d+$/.test(rpp.trim()) || rppN < 1 || rppN > RPP_MAX) ? `Публикаций за запуск: от 1 до ${RPP_MAX}` : null,
  ].filter((p): p is string => !!p)
  const nothing = !changed.length && !periodChanged && !rppChanged && !videosChanged

  const save = async () => {
    if (busy || problems.length || nothing) return
    if (!x.author) { setErr(NO_USER); return }
    const config: ConnectorConfig = {}
    for (const f of changed) config[f] = lists[f]
    if (rppChanged) config.resultsPerPage = rppN
    if (videosChanged) config.downloadVideos = videos
    const body: ConnectorPatch = { author: x.author }
    if (Object.keys(config).length) body.config = config
    if (periodChanged) body.intervalMin = min
    setBusy(true)
    setErr(undefined)
    try {
      x.replace(await patchConnector(c.code, body))
      notify(`Настройки «${c.name}» сохранены — событие в журнале`)
      if (!gone.current) onClose()
    } catch (e) {
      if (gone.current) { notify(`Настройки «${c.name}» не сохранены: ${errorText(e)}`); return }
      setErr(errorText(e))
      setBusy(false)
    }
  }

  return (
    <Modal title={`${COPY.configure}: ${c.name}`} onClose={onClose}
      footer={<>
        <button className="btn g" onClick={onClose}>Отмена</button>
        <button className="btn y" disabled={busy || problems.length > 0 || nothing} onClick={save}>{busy ? 'Сохраняем…' : 'Сохранить'}</button>
      </>}>
      <div className="lnk-form">
        {fields.map((f) => {
          const m = CONFIG_META[f]
          return (
            <div key={f} className="lnk-fld">
              <label className="t-sm c2" htmlFor={`lnk-${c.code}-${f}`}>{m.label} — по одному в строке, до {m.max}</label>
              <textarea id={`lnk-${c.code}-${f}`} className="input" spellCheck={false} value={texts[f] ?? ''} placeholder={m.placeholder}
                onChange={(e) => { const v = e.target.value; setTexts((t) => ({ ...t, [f]: v })); setErr(undefined) }} />
              <span className="t-xs c3" style={lists[f].length > m.max ? { color: 'var(--red-ink)' } : undefined}>{lists[f].length} из {m.max}</span>
            </div>
          )
        })}
        {withRpp && (
          <div className="lnk-fld">
            <label className="t-sm c2" htmlFor={`lnk-${c.code}-rpp`}>Публикаций за запуск с каждого профиля — от 1 до {RPP_MAX}</label>
            <input id={`lnk-${c.code}-rpp`} className="input short" type="number" min={1} max={RPP_MAX} step={1} value={rpp}
              onChange={(e) => { setRpp(e.target.value); setErr(undefined) }} />
          </div>
        )}
        {withVideos && (
          <button type="button" role="checkbox" aria-checked={videos} className="lnk-chk" onClick={() => setVideos(!videos)}>
            <span className={`chk ${videos ? 'on' : ''}`}>{videos && <IcCheck size={14} strokeWidth={3} />}</span>
            <span>Загружать видео — кадры распознаёт OCR; каждое видео увеличивает расход у Apify. Без видео — только обложка и текст.</span>
          </button>
        )}
        {withPeriod && (
          <div className="lnk-fld">
            <label className="t-sm c2" htmlFor={`lnk-${c.code}-period`}>Период сбора, минут — от {lo} до {hi}</label>
            <div className="row" style={{ gap: 12 }}>
              <input id={`lnk-${c.code}-period`} className="input short" type="number" min={lo} max={hi} step={1} value={period}
                onChange={(e) => { setPeriod(e.target.value); setErr(undefined) }} />
              <span className="t-sm c2">{Number.isFinite(min) && min > 0 ? intervalText(min) : ''}</span>
            </div>
          </div>
        )}
        {problems.length > 0 && <div className="alog-err t-sm" style={{ margin: 0 }}>{problems.join('; ')}</div>}
        {err && <div className="alog-err t-sm" style={{ margin: 0 }}>Не сохранено: {err}</div>}
        <p className="t-xs c2" style={{ lineHeight: '17px' }}>
          Изменения пишутся в журнал от имени {x.author ?? '—'}. Ключи и токены площадок здесь не задаются — только в .env на сервере.
        </p>
      </div>
    </Modal>
  )
}
