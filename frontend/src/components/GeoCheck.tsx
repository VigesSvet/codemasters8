// Сверка адреса в 2ГИС (integration-spec §12.5, §13.3; план §11). При открытии карточки читаем только сохранённые сверки и квоту —
// 2ГИС запрашивается по кнопке «Сверить адрес», и сервер сначала отвечает из кэша. Кандидаты — подсказка, решает аналитик:
// первый в списке не выбирается сам, «рекомендуется» — лишь пометка. Координаты в карточке меняются только по явной отметке
// «Перенести координаты в карточку ЖК», и в журнал попадает «было → стало». Порядок координат: у 2ГИС {lat, lon}, у ЖК {lat, lng}.
import { useEffect, useRef, useState } from 'react'
import type { GeoCandidate, GeoCheck as Check, GeoStatus, GeoTargetKind } from '../data/types'
import { isAdmin, onAdminChange } from '../lib/admin'
import { ApiError, errorText } from '../lib/api'
import { fmtStamp } from '../lib/audit'
import {
  GEO_COPY, acceptGeo, acceptLines, awaitingDecision, cacheLine, candidateDistance, candidatePoint, candidateTitle, chosenOf, decisionLine,
  defaultGeoQuery, fmtDist, fmtPoint, getGeoChecks, getGeoStatus, isExact, precisionLabel, quotaLine, rejectGeo, runGeoCheck, validPoint,
} from '../lib/geo'
import { useStore } from '../lib/store'
import { IcCheck, IcClose, IcRefresh, IcSearch } from '../ui/icons'
import { Cell, Modal, PlatformIcon, plural } from './ui'

type LatLng = { lat: number; lng: number }

interface Props {
  target: { kind: GeoTargetKind; id: string }
  /** Адрес из карточки — из него запрос по умолчанию «Казахстан, Усть-Каменогорск, {адрес}» */
  address: string
  /** Точка в карточке: от неё расстояние до кандидатов и «было» при переносе */
  point?: LatLng | null
  /** Можно перенести координаты кандидата в карточку (только ЖК: сведения открытых данных не изменяются) */
  canApply: boolean
  /** Сверка или решение сохранены на сервере — в журнале объекта новое событие */
  onChange?: (check: Check) => void
  /** Координаты перенесены в карточку ЖК: новая точка */
  onApplied?: (point: LatLng) => void
}

/** Загруженное для объекта key: checks null — сверка недоступна (нет связи, сервер без 2ГИС); err — сервер ответил ошибкой */
type Loaded = { key: string; checks: Check[] | null; status: GeoStatus | null; err?: string }
type Dialog =
  | { kind: 'accept'; check: Check; cand: GeoCandidate; apply: boolean; comment: string; busy?: boolean; err?: string }
  | { kind: 'reject'; check: Check; comment: string; busy?: boolean; err?: string }

const QUERY_MAX = 200
const COMMENT_MAX = 2000
/** Как сервер сравнивает запросы для кэша: регистр и пробелы не важны */
const norm = (s: string) => s.toLowerCase().replace(/\s+/g, ' ').trim()
const cap = (s: string) => s[0].toUpperCase() + s.slice(1)

/** Квота 2ГИС исчерпана: новый запрос не выполнится, ответ из кэша — да (тексты как у сервера) */
function quotaProblem(s: GeoStatus): string | null {
  if (typeof s.limit === 'number' && s.used >= s.limit) return `Лимит запросов 2ГИС по ключу исчерпан (${s.used} из ${s.limit})`
  if (typeof s.dailyLimit === 'number' && s.usedToday >= s.dailyLimit) return `Дневной лимит сверок 2ГИС исчерпан (${s.dailyLimit})`
  return null
}

/** Кэш сверки ещё действует: сервер ответит им без запроса к 2ГИС */
const cacheAlive = (x: Check) => !x.purged && (!x.expiresAt || !(Date.parse(x.expiresAt) <= Date.now()))

export default function GeoCheck({ target, address, point, canApply, onChange, onApplied }: Props) {
  const { mode, user, notify, revision } = useStore()
  const live = mode === 'api'
  const key = `${target.kind}:${target.id}`
  const def = defaultGeoQuery(address)
  const author = user?.name
  const applyAllowed = canApply && target.kind === 'complex'

  const [data, setData] = useState<Loaded | null>(null)
  const [ver, setVer] = useState(0)
  // запрос, выбранная сверка и ошибки — свои у каждого объекта: при смене объекта начинаем с адреса его карточки
  const [q, setQ] = useState<{ key: string; text: string } | null>(null)
  const [sel, setSel] = useState<{ key: string; id: number; cached?: boolean } | null>(null)
  const [runErr, setRunErr] = useState<{ key: string; text: string; limit: boolean } | null>(null)
  const [busy, setBusy] = useState<'run' | 'force' | null>(null)
  const [dlg, setDlg] = useState<Dialog | null>(null)
  // номер открытого окна решения: ответ сервера после закрытия окна (или когда открыли другое) это окно не трогает,
  // а ошибку такого решения показываем тостом — иначе она пропала бы вместе с окном
  const gen = useRef(0)
  const openDlg = (d: Dialog) => { gen.current += 1; setDlg(d) }
  const closeDlg = () => { gen.current += 1; setDlg(null) }
  const [hist, setHist] = useState(false)
  const [admin, setAdmin] = useState(() => isAdmin())
  useEffect(() => onAdminChange(setAdmin), [])

  // Сохранённые сверки и квота — без запроса к 2ГИС; revision — после восстановления связи и сохранённых изменений
  useEffect(() => {
    if (!live) return
    let alive = true
    Promise.allSettled([getGeoChecks(target.kind, target.id), getGeoStatus()]).then(([c, s]) => {
      if (!alive) return
      setData((d) => {
        const same = d?.key === key ? d : null
        return {
          key,
          // ошибка перечитывания не стирает уже показанные сверки
          checks: c.status === 'fulfilled' ? c.value : same?.checks ?? null,
          status: s.status === 'fulfilled' ? s.value : same?.status ?? null,
          err: c.status === 'rejected' ? errorText(c.reason) : undefined,
        }
      })
    })
    return () => { alive = false }
  }, [key, live, ver, revision]) // eslint-disable-line react-hooks/exhaustive-deps

  const loaded = data?.key === key ? data : null
  const checks = loaded?.checks ?? []
  const status = loaded?.status ?? null
  const query = q?.key === key ? q.text : def
  const picked = sel?.key === key ? sel : null
  const current = (picked && checks.find((x) => x.id === picked.id)) || checks[0] || null
  const others = checks.filter((x) => x !== current)
  const err = runErr?.key === key ? runErr : null
  const quota = status?.configured ? quotaProblem(status) : null
  const canRun = live && !!author && status?.configured !== false && !busy
  // «Обновить запрос» — мимо кэша: только администратор и только когда по этому запросу есть действующий кэш
  const cachedForQuery = checks.find((x) => cacheAlive(x) && norm(x.query) === norm(query || def))
  const canForce = live && admin && !!author && !!cachedForQuery && status?.configured !== false

  /** Сверка с сервера — в список (та же — на своё место, новая — первой) */
  const upsert = (chk: Check) => setData((d) => {
    const same = d?.key === key ? d : null
    const xs = same?.checks ?? []
    return { key, status: same?.status ?? null, err: same?.err, checks: xs.some((x) => x.id === chk.id) ? xs.map((x) => (x.id === chk.id ? chk : x)) : [chk, ...xs] }
  })
  const refreshStatus = () => getGeoStatus().then((s) => { if (s) setData((d) => (d?.key === key ? { ...d, status: s } : d)) }, () => {})

  const run = async (force: boolean) => {
    if (!author || busy) return
    setBusy(force ? 'force' : 'run')
    setRunErr(null)
    try {
      const got = await runGeoCheck({ targetKind: target.kind, targetId: target.id, query, author, ...(force ? { force: true } : {}) })
      upsert(got)
      setSel({ key, id: got.id, cached: got.cached })
      if (got.cached) notify('Кандидаты из кэша — 2ГИС повторно не запрашивался')
      else {
        onChange?.(got)
        const n = got.candidates.length
        notify(got.status === 'not_found' || !n ? '2ГИС не нашёл адрес — уточните запрос' : `2ГИС: ${n} ${plural(n, ['кандидат', 'кандидата', 'кандидатов'])} — выберите подходящий дом`)
      }
    } catch (e) {
      setRunErr({ key, text: errorText(e), limit: e instanceof ApiError && e.status === 429 })
    } finally {
      setBusy(null)
      void refreshStatus()
    }
  }

  const decide = async () => {
    if (!dlg || dlg.busy || !author) return
    const d = dlg
    const g = gen.current
    setDlg({ ...d, busy: true, err: undefined })
    try {
      const got = d.kind === 'accept'
        ? await acceptGeo(d.check.id, { candidateId: d.cand.id, author, apply: applyAllowed && d.apply, comment: d.comment })
        : await rejectGeo(d.check.id, { author, comment: d.comment })
      upsert(got)
      setSel({ key, id: got.id })
      if (gen.current === g) closeDlg()
      onChange?.(got)
      if (d.kind === 'reject') notify('Кандидаты 2ГИС отклонены — решение в журнале')
      else if (got.applied) {
        const p = candidatePoint(chosenOf(got) ?? d.cand)
        if (validPoint(p)) onApplied?.(p)
        notify('Кандидат 2ГИС принят, координаты ЖК перенесены — «было → стало» в журнале')
      } else notify('Кандидат 2ГИС принят, координаты в карточке не изменены — решение в журнале')
    } catch (e) {
      const text = errorText(e)
      if (gen.current === g) setDlg((x) => x && { ...x, busy: false, err: text })
      else notify(`${d.kind === 'accept' ? 'Кандидат 2ГИС не принят' : 'Кандидаты 2ГИС не отклонены'}: ${text}`)
      // сверку уже решили в другой вкладке или кандидаты устарели — показываем, что на сервере
      if (e instanceof ApiError && e.status === 409) setVer((v) => v + 1)
    }
  }

  const head = (
    <>
      <div className="row between wrap-f" style={{ gap: 8 }}>
        <h3 className="t-h3 row" style={{ gap: 10 }}><PlatformIcon p="2gis" size={24} />{GEO_COPY.title}</h3>
        {status?.configured && <span className="t-xs c2 num">{quotaLine(status)}</span>}
      </div>
      <p className="t-sm c2" style={{ margin: '6px 0 12px' }}>
        2ГИС предлагает дома по адресу — решает аналитик: сам кандидат не выбирается, {applyAllowed
          ? 'координаты в карточке без явной отметки не меняются'
          : 'сведения открытого источника не изменяются — решение сохраняется как сверка'}.
        При открытии карточки 2ГИС не запрашивается, повторная сверка отвечает из кэша.
      </p>
    </>
  )

  if (!live) return (
    <section style={{ marginTop: 24 }}>
      {head}
      <div className="row wrap-f" style={{ gap: 8 }}>
        <input className="input" style={{ flex: '1 1 280px', width: 'auto', minWidth: 0 }} value={def} disabled aria-label="Запрос к 2ГИС" />
        <button className="btn d m" disabled><IcSearch size={18} />{GEO_COPY.run}</button>
      </div>
      <div className="ai-note" style={{ marginTop: 10 }}>{mode === 'loading' ? 'Подключаемся к серверу…' : GEO_COPY.demo}</div>
    </section>
  )

  if (!loaded || !loaded.checks) return (
    <section style={{ marginTop: 24 }}>
      {head}
      {!loaded ? <div className="ai-note">Загружаем сверки…</div>
        : loaded.err ? <div className="ai-note err">Сверки не загрузились: {loaded.err} <button className="link" onClick={() => setVer((v) => v + 1)}>Повторить</button></div>
          : <div className="ai-note">{GEO_COPY.unavailable}. <button className="link" onClick={() => setVer((v) => v + 1)}>Повторить</button></div>}
    </section>
  )

  const awaiting = !!current && awaitingDecision(current)
  const decision = current ? decisionLine(current, fmtStamp) : null
  const chosen = current ? chosenOf(current) : null
  // у сверки с перенесёнными координатами расстояния — от прежней точки; иначе — от текущей точки карточки
  // (расстояние сервера посчитано в момент сверки и могло устареть) и, только без неё, — с сервера
  const from = current?.applied ? current.prevPoint : point
  const fromLabel = current?.applied ? 'прежней точки в карточке' : 'точки в карточке'
  const distOf = (c: GeoCandidate) => (validPoint(from) ? candidateDistance({ ...c, distanceM: null }, from) : candidateDistance(c))

  return (
    <section style={{ marginTop: 24 }}>
      {head}
      <div className="row wrap-f" style={{ gap: 8 }}>
        <input className="input" style={{ flex: '1 1 280px', width: 'auto', minWidth: 0 }} value={query} maxLength={QUERY_MAX} placeholder={def}
          aria-label="Запрос к 2ГИС" disabled={!live || status?.configured === false}
          onChange={(e) => { setQ({ key, text: e.target.value }); setRunErr(null) }}
          onKeyDown={(e) => { if (e.key === 'Enter' && canRun) void run(false) }} />
        <button className="btn d m" disabled={!canRun} onClick={() => run(false)}><IcSearch size={18} />{busy === 'run' ? 'Сверяем…' : GEO_COPY.run}</button>
        {canForce && (
          <button className="btn g m" disabled={!!busy} onClick={() => run(true)}
            title="Запросить 2ГИС заново, мимо кэша: расходует квоту ключа. Только для администратора">
            <IcRefresh size={18} />{busy === 'force' ? 'Обновляем…' : GEO_COPY.force}
          </button>
        )}
      </div>
      {norm(query) !== norm(def) && (
        <button className="link t-xs" style={{ marginTop: 6 }} onClick={() => { setQ({ key, text: def }); setRunErr(null) }}>Вернуть адрес из карточки</button>
      )}

      {status?.configured === false && (
        <div className="ai-note warn" style={{ marginTop: 10 }}>{GEO_COPY.notConfigured} — новые сверки недоступны; сохранённых кандидатов можно принять или отклонить.</div>
      )}
      {quota && <div className="ai-note warn" style={{ marginTop: 10 }}>{quota} — новый запрос к 2ГИС не выполнится; сохранённые сверки и кэш доступны.</div>}
      {!author && <div className="ai-note" style={{ marginTop: 10 }}>Войдите как аналитик: запрос и решение по сверке записываются в журнал от вашего имени.</div>}
      {err && <div className={`ai-note ${err.limit ? 'warn' : 'err'}`} style={{ marginTop: 10 }}>{err.limit ? err.text : `Сверка не выполнена: ${err.text}`}</div>}
      {loaded.err && <div className="ai-note err" style={{ marginTop: 10 }}>Не удалось обновить сверки: {loaded.err} <button className="link" onClick={() => setVer((v) => v + 1)}>Повторить</button></div>}

      {!current ? (
        <div className="t-sm c2" style={{ marginTop: 14 }}>По объекту сверок ещё не было — нажмите «{GEO_COPY.run}».</div>
      ) : (
        <div style={{ marginTop: 16 }}>
          <div className="t-sm"><span className="c2">Запрос:</span> «{current.query}»</div>
          <div className="t-xs c2" style={{ marginTop: 2 }}>
            {[
              current.purged ? (current.fetchedAt ? `Сверка от ${fmtStamp(current.fetchedAt)} · кандидаты удалены по сроку хранения` : 'Кандидаты удалены по сроку хранения')
                : cacheLine(current, fmtStamp),
              current.requestedBy && `запросил ${current.requestedBy}`,
            ].filter(Boolean).join(' · ')}
            {picked?.id === current.id && picked.cached && ' · ответ из кэша: 2ГИС повторно не запрашивался, квота не расходовалась'}
          </div>
          {current.purged && current.status === 'candidates' && <div className="ai-note warn" style={{ marginTop: 8 }}>{GEO_COPY.purged}</div>}

          {decision && (
            <div className={`ai-decision ${current.status === 'accepted' ? 'ok' : current.status === 'rejected' ? 'no' : ''}`}>
              {decision}
              {current.applied && validPoint(current.prevPoint) && chosen && <>: было {fmtPoint(current.prevPoint)} → стало {fmtPoint(candidatePoint(chosen))}</>}
              {current.comment && <div style={{ marginTop: 2 }}>Комментарий: {current.comment}</div>}
            </div>
          )}
          {awaiting && (
            <div className="t-sm c2" style={{ marginTop: 10 }}>
              {current.candidates.length} {plural(current.candidates.length, ['кандидат', 'кандидата', 'кандидатов'])} — ни один не выбран.
              Сверьте дом, литеру и корпус: соседние дома (8/3 и 8/3а) 2ГИС тоже показывает.
            </div>
          )}

          {!current.purged && current.candidates.length > 0 && (
            <div className="ai-list" style={{ marginTop: 4 }}>
              {current.candidates.map((c) => {
                const title = candidateTitle(c)
                const addr = [c.fullName, c.addressName].find((x) => x && x !== title)
                const d = distOf(c)
                return (
                  <div key={c.id} className="ai-item">
                    <div className="row between" style={{ gap: 8, alignItems: 'flex-start' }}>
                      <div style={{ minWidth: 0 }}>
                        <div className="bold">{title}</div>
                        {addr && <div className="t-sm" style={{ marginTop: 2 }}>{addr}</div>}
                      </div>
                      <div className="row wrap-f" style={{ gap: 6, justifyContent: 'flex-end' }}>
                        {current.chosenId === c.id && <span className="tag dark"><IcCheck size={12} strokeWidth={3} />выбран аналитиком</span>}
                        {c.recommended && (
                          <span className="tag green" title="Единственный точный дом в Усть-Каменогорске — подсказка, а не выбор: решает аналитик">{GEO_COPY.recommended}</span>
                        )}
                        <span className={`tag ${isExact(c) ? 'outline' : 'yellow'}`}>{isExact(c) && <IcCheck size={12} strokeWidth={3} />}{precisionLabel(c.precision)}</span>
                      </div>
                    </div>
                    <div className="t-xs c2" style={{ marginTop: 4 }}>
                      {[c.purpose && cap(c.purpose), fmtPoint(candidatePoint(c)), d === null ? null : `в ${fmtDist(d)} от ${fromLabel}`].filter(Boolean).join(' · ')}
                    </div>
                    {awaiting && (
                      <div className="row" style={{ marginTop: 8 }}>
                        <button className="btn g m" disabled={!author} onClick={() => openDlg({ kind: 'accept', check: current, cand: c, apply: false, comment: '' })}>
                          <IcCheck size={18} />{GEO_COPY.accept}
                        </button>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
          {awaiting && (
            <div className="row wrap-f" style={{ gap: 8, marginTop: 4 }}>
              <button className="btn g m" disabled={!author} onClick={() => openDlg({ kind: 'reject', check: current, comment: '' })}><IcClose size={18} />{GEO_COPY.rejectAll}</button>
              <span className="t-xs c2">Ни один дом не подходит — уточните запрос и сверьте заново</span>
            </div>
          )}
        </div>
      )}

      {others.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <button className="link t-sm" aria-expanded={hist} onClick={() => setHist(!hist)}>{hist ? 'Скрыть прошлые сверки' : `Прошлые сверки · ${others.length}`}</button>
          {hist && (
            <div className="stack" style={{ gap: 6, marginTop: 8 }}>
              {others.map((x) => (
                <div key={x.id} className="t-xs c2">
                  «{x.query}» · {x.fetchedAt ? fmtStamp(x.fetchedAt) : 'время не указано'} · {decisionLine(x, fmtStamp)
                    ?? (x.purged ? 'кандидаты удалены по сроку хранения' : `${x.candidates.length} ${plural(x.candidates.length, ['кандидат', 'кандидата', 'кандидатов'])}, решения нет`)}
                  {' '}<button className="link" onClick={() => setSel({ key, id: x.id })}>Показать</button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {dlg && (
        <Modal title={dlg.kind === 'accept' ? GEO_COPY.acceptTitle : 'Отклонить всех кандидатов 2ГИС?'} onClose={closeDlg}
          footer={<>
            <button className="btn g" onClick={closeDlg}>Отмена</button>
            {dlg.kind === 'accept'
              ? <button className="btn y" disabled={dlg.busy} onClick={decide}>{dlg.busy ? 'Сохраняем…' : dlg.apply && applyAllowed ? 'Принять и перенести координаты' : 'Принять кандидата'}</button>
              : <button className="btn d" disabled={dlg.busy} onClick={decide}>{dlg.busy ? 'Сохраняем…' : GEO_COPY.rejectAll}</button>}
          </>}>
          {dlg.kind === 'accept' ? (() => {
            const c = dlg.cand
            const lines = acceptLines(validPoint(point) ? { ...c, distanceM: null } : c, point)
            return (
              <>
                <Cell icon={<span className="ico"><PlatformIcon p="2gis" size={24} /></span>} title={candidateTitle(c)}
                  sub={[[c.fullName, c.addressName].find((x) => x && x !== candidateTitle(c)), c.purpose, precisionLabel(c.precision)].filter(Boolean).join(' · ')} />
                <div className="t-sm" style={{ padding: '6px 24px 2px', lineHeight: '20px' }}>
                  <div className="c2">{lines.now}</div>
                  <div>→ {lines.next}</div>
                </div>
                {applyAllowed ? (
                  <button type="button" role="checkbox" aria-checked={dlg.apply} className="cell chk-row" style={{ marginTop: 6 }}
                    onClick={() => setDlg({ ...dlg, apply: !dlg.apply, err: undefined })}>
                    <span className={`chk ${dlg.apply ? 'on' : ''}`}>{dlg.apply && <IcCheck size={14} strokeWidth={3} />}</span>
                    <div className="main">
                      <div className="ttl">{GEO_COPY.apply}</div>
                      <div className="sub">{dlg.apply
                        ? 'Точка ЖК на карте сменится; «было → стало» с вашим именем попадёт в журнал'
                        : 'Без отметки координаты в карточке не меняются — сохраняется только решение по сверке'}</div>
                    </div>
                  </button>
                ) : target.kind === 'registry' && (
                  <p className="t-xs c2" style={{ padding: '8px 24px 0', lineHeight: '17px' }}>Сведения открытого источника не изменяются — координаты 2ГИС сохраняются как сверка.</p>
                )}
              </>
            )
          })() : (
            <p className="t-sm" style={{ padding: '8px 24px', lineHeight: '20px' }}>
              Ни один из {dlg.check.candidates.length} {plural(dlg.check.candidates.length, ['кандидата', 'кандидатов', 'кандидатов'])} не совпадает с объектом.
              Координаты в карточке не меняются; решение с вашим именем попадёт в журнал. Сверить заново можно с уточнённым запросом.
            </p>
          )}
          <div style={{ padding: '12px 24px 4px' }}>
            <label className="t-sm c2" htmlFor="geo-why">Комментарий — необязательно · попадёт в журнал</label>
            <textarea id="geo-why" className="input" style={{ marginTop: 6, minHeight: 96 }} maxLength={COMMENT_MAX} value={dlg.comment}
              placeholder={dlg.kind === 'accept' ? 'Например: дом 8/3 — по разрешению на строительство, литера совпадает' : 'Например: 2ГИС не знает новый адрес, точка в карточке — по разрешению'}
              onChange={(e) => setDlg({ ...dlg, comment: e.target.value, err: undefined })} />
            {dlg.err && <div className="alog-err t-sm" style={{ marginTop: 8 }}>Не сохранено: {dlg.err}</div>}
          </div>
          {user && <Cell icon={<span className="ava">{user.short}</span>} title={user.name} sub={`${user.role} · автор решения, дата и время попадут в журнал`} />}
        </Modal>
      )}
    </section>
  )
}
