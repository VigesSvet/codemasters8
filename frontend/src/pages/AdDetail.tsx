import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { AuditList } from '../components/Audit'
import { FactorList, Quote } from '../components/Factors'
import { AiBadge, Confidence, HypothesisItem, LinkMark, OcrTag, TermList } from '../components/Hypothesis'
import { MediaViewer } from '../components/MediaViewer'
import { Origin } from '../components/Provenance'
import { SignalAction } from '../components/SignalPanel'
import { Cell, Gauge, Highlight, Modal, NeedsCheck, PageTitle, PlatformIcon, Score, StatusDot, ZoneTag, adKey, fmtDT, plural, statusMeta } from '../components/ui'
import type { Ad, AdAnalysis, AdScore, AdStatus, AdVersion, AuditEvent, Complex, LinkHypothesis, LlmAnalysis, Processing } from '../data/types'
import { isAdmin, onAdminChange } from '../lib/admin'
import { linkConf, linkText, pct, visibleHypotheses, type LinkConf } from '../lib/ai'
import { errorText } from '../lib/api'
import { SYSTEM, fmtStamp, initialsOf } from '../lib/audit'
import { getVersions, processingNote, stageLabel } from '../lib/connectors'
import { CONF_LABEL, CONF_TONE, REANALYZE_HINT, amountText, costText, droppedText, llmState, modelText, reanalyzeAd, reanalyzeBlock, signalLabel } from '../lib/llm'
import { adImage, mediaRefSrc } from '../lib/media'
import { from, useFrom } from '../lib/nav'
import { externalUrl, platformName } from '../lib/platform'
import { isDemoAd } from '../lib/provenance'
import { linkConfirmed, model } from '../lib/risk'
import { matches } from '../lib/search'
import { useHeadHeight } from '../lib/sticky'
import { useStore } from '../lib/store'
import { adGallery } from '../lib/viewer'
import { IcBuilding, IcCheck, IcChevronDown, IcChevronUp, IcClose, IcExternal, IcSearch, IcSend, IcUsers, IcZoomIn } from '../ui/icons'

const ORDER: AdStatus[] = ['new', 'needs_check', 'in_review', 'confirmed', 'dismissed']
const TEXT_MAX = 2000

/** Решения с основанием: для «Нарушение» оно обязательно, для остальных — по желанию */
type Decision = 'confirmed' | 'dismissed' | 'needs_check'
const DECISION: Record<Decision, { title: string; label: string; button: string; done: string; required: boolean; hint: string }> = {
  confirmed: { title: 'Подтвердить нарушение?', label: 'Основание решения — обязательно', button: 'Подтвердить нарушение', done: 'Нарушение подтверждено, основание записано в журнал', required: true,
    hint: 'Например: сверено с картотекой ГАСК, гарантии ЕО и разрешения МИО нет' },
  dismissed: { title: 'Отклонить публикацию?', label: 'Почему отклонено — необязательно', button: 'Отклонить', done: 'Публикация отклонена', required: false,
    hint: 'Например: у объекта действующая гарантия ЕО, номер указан в профиле' },
  needs_check: { title: 'Отправить на доп. проверку?', label: 'Что проверить — необязательно', button: 'Отправить', done: 'Отправлено на доп. проверку', required: false,
    hint: 'Например: запросить в ГАСК сведения о разрешении МИО' },
}
const isDecision = (m: string | null): m is Decision => !!m && m in DECISION

/** Журнал публикации с сервера: id — чья запись; при ошибке перечитывания остаются прежние события */
type Log = { id: string; events?: AuditEvent[]; err?: string }
/** Анализ ИИ публикации с сервера: id — чей; при ошибке перечитывания остаётся прежний */
type AiLoad = { id: string; data?: AdAnalysis; err?: string }

/** Анализ собранной публикации ещё идёт: пока он в очереди или выполняется, ленту опрашиваем — каждые 10 с, не дольше 5 минут */
const POLL_MS = 10_000
const POLL_MAX = 30
const pending = (p?: Processing | null) => p?.status === 'queued' || p?.status === 'running'

/** Разбор публикации: слева сама реклама и история, справа — решение аналитика */
export default function AdDetail() {
  const { id } = useParams()
  const { mode, ads, complexes, setAdStatus, linkAd, acceptHypothesis, getAnalysis, addComment, getAudit, sessionLog, notify, riskOf, adScoreOf, user, analysts, revision, refreshAds } = useStore()
  const a = ads.find((x) => x.id === id)
  const found = !!a
  // опрос ленты, пока сервер анализирует собранную публикацию: n — сколько раз уже опросили эту публикацию
  const [poll, setPoll] = useState<{ id?: string; n: number }>({ n: 0 })
  const [comment, setComment] = useState('')
  const [sending, setSending] = useState(false)
  const [modal, setModal] = useState<'status' | 'cx' | Decision | null>(null)
  const [reasons, setReasons] = useState<Partial<Record<Decision, string>>>({})
  const [cq, setCq] = useState('')
  const [linking, setLinking] = useState(false)
  const [log, setLog] = useState<Log | null>(null)
  const [ver, setVer] = useState(0)
  const [aiLoad, setAiLoad] = useState<AiLoad | null>(null)
  const [aiVer, setAiVer] = useState(0)
  // просмотр файлов публикации на весь экран: номер файла в галерее, null — закрыт
  const [viewer, setViewer] = useState<number | null>(null)
  const live = mode === 'api'
  // откуда пришли (сигнал, очередь, ЖК) — крошка «← …» ведёт туда
  const back = useFrom()
  useHeadHeight()

  // журнал — с сервера; revision растёт после каждого сохранённого изменения (статус, привязка, комментарий —
  // в том числе из очереди, если PATCH завершился уже здесь) и после восстановления связи — тогда перечитываем
  useEffect(() => {
    // нет такой публикации — журнал не запрашиваем: вместо баннера «Журнал не загрузился» — понятное «не найдена»
    if (!live || !id || !found) return
    let alive = true
    getAudit({ adId: id }).then(
      (events) => { if (alive) setLog({ id, events }) },
      (e: unknown) => { if (alive) setLog((l) => ({ id, events: l?.id === id ? l.events : undefined, err: errorText(e) })) },
    )
    return () => { alive = false }
  }, [id, live, found, ver, revision]) // eslint-disable-line react-hooks/exhaustive-deps

  // анализ ИИ — один на страницу: по нему и карточка «Анализ ИИ», и уверенность в текущей привязке в панели справа.
  // Решения меняют revision — анализ перечитывается (отклонённые гипотезы уходят, решение по условиям приходит с сервера)
  useEffect(() => {
    if (!live || !id || !found) return
    let alive = true
    getAnalysis(id).then(
      (data) => { if (alive) setAiLoad({ id, data }) },
      (e: unknown) => { if (alive) setAiLoad((l) => ({ id, data: l?.id === id ? l.data : undefined, err: errorText(e) })) },
    )
    return () => { alive = false }
  }, [id, live, found, aiVer, revision]) // eslint-disable-line react-hooks/exhaustive-deps

  // Публикации нет в ленте этой вкладки: её могли собрать или прислать после загрузки страницы (ссылка из журнала, от коллеги) —
  // прежде чем сказать «не найдена», один раз перечитываем ленту с сервера. key — чья проверка: публикация и номер «Обновить ленту»
  const [miss, setMiss] = useState<{ key: string; ok: boolean } | null>(null)
  const [missVer, setMissVer] = useState(0)
  const missKey = `${id}:${missVer}`
  useEffect(() => {
    if (!live || !id || found) return
    let alive = true
    refreshAds({ quiet: true }).then((ok) => { if (alive) setMiss({ key: missKey, ok }) })
    return () => { alive = false }
  }, [live, id, found, missKey]) // eslint-disable-line react-hooks/exhaustive-deps

  // анализ собранной публикации в очереди или идёт: перечитываем ленту, пока он не закончится (готов или не выполнен).
  // Лента не изменилась — журнал и анализ не перечитываются (refreshAds quiet). Скрытая вкладка не опрашивает и не тратит
  // бюджет опросов (он — на реальные запросы); вкладка снова видна — ленту перечитываем сразу
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden')
  useEffect(() => {
    const on = () => setVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', on)
    return () => document.removeEventListener('visibilitychange', on)
  }, [])
  const polls = poll.id === id ? poll.n : 0
  const waiting = live && pending(a?.processing)
  useEffect(() => {
    if (!waiting || !visible || polls >= POLL_MAX) return
    const t = setTimeout(() => {
      void refreshAds({ quiet: true })
      setPoll({ id, n: polls + 1 })
    }, POLL_MS)
    return () => clearTimeout(t)
  }, [waiting, visible, polls, id]) // eslint-disable-line react-hooks/exhaustive-deps
  const wasVisible = useRef(visible)
  useEffect(() => {
    const was = wasVisible.current
    wasVisible.current = visible
    if (visible && !was && waiting) void refreshAds({ quiet: true })
  }, [visible]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!a) {
    const looked = miss?.key === missKey ? miss : null
    if (mode === 'loading' || (live && !looked)) return <div className="empty">Загружаем публикацию…</div>
    return (
      <div className="empty">
        <div className="t-h3" style={{ color: 'var(--text)', marginBottom: 6 }}>Публикация не найдена</div>
        <div>{!live ? `Публикации «${id}» нет в демо-наборе — возможно, ссылка ведёт на публикацию с сервера или устарела.`
          : looked?.ok ? `Публикации «${id}» нет на сервере — ссылка устарела или база была пересоздана.`
            : `Публикации «${id}» нет в ленте этой вкладки, а перечитать ленту с сервера не удалось — проверьте связь и обновите ленту.`}</div>
        <div className="row" style={{ justifyContent: 'center', gap: 8, marginTop: 16 }}>
          {back && <Link to={back.to} className="btn g m">← {back.label}</Link>}
          {live && <button className="btn g m" onClick={() => setMissVer((v) => v + 1)}>Обновить ленту</button>}
          <Link to="/ads" className="btn g m">К ленте публикаций</Link>
        </div>
      </div>
    )
  }
  // ЖК — только сохранённая привязка из стора (с бэкендом — PATCH /ads/{id}/complex, событие в журнале)
  const c = a.complexId ? complexes.find((x) => x.id === a.complexId) : undefined
  const s = adScoreOf(a)
  const cr = c ? riskOf(c) : null
  const fact = linkConfirmed(a)
  const ai = live && aiLoad?.id === a.id ? aiLoad : null
  // уверенность в привязке-гипотезе — по текущему анализу (она сохранится при принятии); записанная при сборе — отдельно
  const lc = linkConf(a, ai?.data)
  // цитаты-основания R2/R3 этой публикации (с пометкой, если взяты со скриншота)
  const quotes = s.factors.filter((f) => f.code === 'R2' || f.code === 'R3')
    .flatMap((f) => f.items.filter((x) => x.kind === 'ad' && x.quote).slice(0, 1).map((x) => ({ f, x })))
  const ME = user?.name ?? ''
  const demo = mode === 'demo'
  const reload = () => setVer((v) => v + 1)
  // с бэкендом — журнал сервера; в демо — только действия этой сессии
  const mine = live && log?.id === a.id ? log : null
  const events = live ? mine?.events ?? [] : sessionLog.filter((e) => e.adId === a.id)
  const loadingLog = mode === 'loading' || (live && (!mine || (!mine.events && !mine.err)))
  // не загрузился и показать нечего: только строка ошибки — не «0 событий» и не «Событий пока нет»
  const noLog = !!mine && !mine.events
  const dm = isDecision(modal) ? modal : null
  const reason = dm ? reasons[dm] ?? '' : ''
  // происхождение: демо-публикация сида или добавленная аналитиком (кто и когда — первое событие журнала)
  const demoAd = isDemoAd(a)
  const byAnalyst = !demoAd && (a.origin === 'analyst' || a.source === 'manual')
  const added = byAnalyst ? events.find((e) => e.who !== SYSTEM) : undefined
  // собрана подключённым источником или прислана жителем через бота: источник, площадка, время получения сервером
  const collected = a.origin === 'collected'
  const bot = collected && a.source === 'bot'
  const img = adImage(a)
  const href = externalUrl(a.url)
  // файлы публикации, кроме обложки (она — крупно слева)
  const files = (a.media ?? []).filter((m) => { const src = mediaRefSrc(m); return !!src && src !== img })
  // все файлы для просмотра: обложка первой, затем файлы публикации по порядку; at — номер файла в просмотре
  const gallery = adGallery(a)
  const at = (src?: string) => Math.max(0, gallery.findIndex((g) => g.src === src))
  const proc = a.processing ?? null
  /** «Обновить» у состояния анализа: перечитать ленту сейчас и снова опрашивать */
  const recheck = () => { setPoll({ id, n: 0 }); void refreshAds() }

  /** to — новый ЖК (null — снять привязку): выбор аналитика — привязка становится фактом с его именем */
  const link = (to: Complex | null) => {
    setModal(null)
    const same = !!to && to.id === a.complexId
    const msg = !to ? 'Привязка к ЖК снята' : same ? `Привязка к ЖК «${to.name}» принята` : `Публикация привязана к ЖК «${to.name}»`
    setLinking(true)
    // при ошибке стор откатывает привязку и показывает баннер
    linkAd(a.id, to?.id ?? null).then((ok) => {
      if (ok) notify(demo ? `${msg} — демо-режим, на сервере не сохранено` : `${msg}, оценка пересчитана, событие в журнале`)
    }).finally(() => setLinking(false))
  }
  /** «Принять привязку» — текущую гипотезу ИИ: с бэкендом — принятие гипотезы (уверенность ИИ сохраняется, событие ИИ в журнале), в демо — локально */
  const confirmLink = (to: Complex) => {
    if (!live) { link(to); return }
    setLinking(true)
    acceptHypothesis(a.id, to.id).then(
      () => notify(`Гипотеза принята: привязка к ЖК «${to.name}» теперь факт, риск объекта пересчитан`),
      (e: unknown) => notify(`Привязка не принята: ${errorText(e)}`),
    ).finally(() => setLinking(false))
  }

  /** Смена статуса с основанием; при ошибке статус откатан, основание остаётся в черновике */
  const decide = (st: AdStatus, done: string, why?: string) => {
    setModal(null)
    setAdStatus(a.id, st, { assignee: ME, comment: why }).then((ok) => {
      if (!ok) return
      if (isDecision(st)) setReasons((r) => ({ ...r, [st]: '' }))
      notify(demo ? `${done} — демо-режим, на сервере не сохранено` : done)
    })
  }

  const send = () => {
    const text = comment.trim()
    if (!text || sending) return
    setSending(true)
    addComment(a.id, text).then(
      () => setComment(''),
      () => notify('Комментарий не сохранён — текст остался в поле'),
    ).finally(() => setSending(false))
  }

  return (
    <>
      <PageTitle crumbs={back ? [[`← ${back.label}`, back.to], [adKey(a.id)]] : [['Карта', '/'], ['Лента публикаций', '/ads'], [adKey(a.id)]]}
        title={<span className="row wrap-f" style={{ gap: 14 }}>{c ? `Реклама ЖК «${c.name}»` : 'Реклама без привязки к ЖК'}
          {c && <LinkMark a={a} value={lc.now?.confidence} label="привязка — гипотеза ИИ" />}{demoAd && <Origin kind="demo" label="Демо-публикация (вымышленная)" />}</span>}
        sub={<span className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={22} /><span>{platformName(a.platform)} · {a.account} · {fmtDT(a.date)}
          {collected && a.collectedAt && <span title="Когда публикацию получил сервер — не время публикации в источнике"> · {bot ? 'прислано' : 'собрано'} {fmtDT(a.collectedAt)}</span>}</span></span>} />

      <div className="cols-r">
        <div className="stack" style={{ gap: 16 }}>
          <div className="card" style={{ display: 'grid', gridTemplateColumns: img ? '300px 1fr' : '1fr', gap: 28 }}>
            {img && (
              // обложка целиком, без обрезки: по нажатию — просмотр с масштабом и всеми файлами публикации
              <div className="mv-cover">
                <button type="button" className="ph mv-open contain" style={{ borderRadius: 18, aspectRatio: '1' }} onClick={() => setViewer(0)}
                  aria-label={gallery.length > 1 ? `Открыть файлы публикации в просмотре: ${gallery.length}` : 'Открыть скриншот в просмотре'}
                  title="Открыть в просмотре: масштаб, листание файлов, текст OCR">
                  <img src={img} alt="" /><span className="mv-hint"><IcZoomIn />Открыть</span>
                </button>
                <button type="button" className="btn g s full" onClick={() => setViewer(0)}><IcZoomIn />{gallery.length > 1 ? `Открыть файлы (${gallery.length})` : 'Открыть скриншот'}</button>
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <p style={{ fontSize: 20, lineHeight: '28px' }}><Highlight text={a.text} /></p>
              {a.ocr && (
                <div style={{ background: 'var(--bg)', borderRadius: 16, padding: '12px 16px' }}>
                  <div className="t-xs c2" style={{ marginBottom: 4 }}>Текст на изображении (OCR)</div>
                  <div className="t-sm" style={{ letterSpacing: '.02em' }}><Highlight text={a.ocr} /></div>
                </div>
              )}
              {files.length > 0 && (
                <div>
                  <div className="t-xs c2" style={{ marginBottom: 6 }}>Файлы публикации · {a.media?.length ?? files.length}</div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(112px, 1fr))', gap: 8 }}>
                    {files.map((m) => {
                      const src = mediaRefSrc(m)
                      const k = at(src)
                      const name = `файл ${k + 1} из ${gallery.length}: ${gallery[k]?.label ?? ''}`
                      // видео играет на месте, «Открыть» — в просмотре; изображения открываются в просмотре со своего места
                      return m.kind === 'video' ? (
                        <div key={m.id} className="mv-inline" style={{ gridColumn: '1 / -1' }}>
                          <video controls preload="metadata" src={src} />
                          <button type="button" className="mv-inline-open" onClick={() => setViewer(k)} aria-label={`Открыть в просмотре ${name}`} title="Открыть в просмотре"><IcZoomIn />Открыть</button>
                        </div>
                      ) : (
                        <button key={m.id} type="button" className="ph mv-open" style={{ borderRadius: 12, aspectRatio: '1' }} onClick={() => setViewer(k)}
                          aria-label={`Открыть в просмотре ${name}`} title="Открыть в просмотре">
                          <img src={src} alt="" /><span className="mv-hint sm"><IcZoomIn />Открыть</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}
              {proc && proc.status !== 'done' && <ProcessingNote p={proc} polling={waiting && polls < POLL_MAX} onRefresh={recheck} />}
              <div className="row wrap-f" style={{ gap: 6, marginTop: 'auto' }}>
                {a.price && <span className="tag gray">Цена: {a.price}</span>}
                {a.reach && <span className="tag gray">Охват ~{a.reach.toLocaleString('ru')}</span>}
                {demoAd ? <Origin kind="demo" label="Демонстрационные данные" title="Демонстрационная публикация: текст, аккаунт и ссылка вымышлены" />
                  : collected ? <Origin kind="source" label={bot ? 'Прислано жителем через бота' : 'Собрано из источника'} source={platformName(a.platform)} at={a.collectedAt ?? undefined}
                    title={`${bot ? 'Публикацию прислал житель через Telegram-бота заявок' : 'Публикацию собрал подключённый источник'}: текст и файлы — как в источнике на момент сбора, аналитиком не проверены. ${platformName(a.platform)}${a.collectedAt ? `, получено сервером ${fmtDT(a.collectedAt)}` : ''}`} />
                    : byAnalyst ? <Origin kind="analyst" label="Добавлено аналитиком" who={added?.who ?? a.assignee} at={added?.at} />
                      : <span className="tag gray">Источник: {a.source}</span>}
                {/* гипотеза помечена у заголовка; здесь — только факт с автором */}
                {c && fact && <Origin kind="analyst" label="Привязка — решение аналитика" who={a.linkConfirmedBy} at={a.linkConfirmedAt} />}
                {href && <a href={href} target="_blank" rel="noreferrer" className="tag outline" title={collected ? 'Публикация в источнике — могла измениться после сбора' : undefined}><IcExternal size={13} />{collected ? 'Открыть в источнике' : 'Источник'}</a>}
              </div>
            </div>
          </div>

          {/* оценка с факторами — в обычном потоке: липкая колонка справа держит только статус и решения */}
          <div className="card">
            <div className="card-head"><h3>Оценка публикации</h3><span className="row" style={{ gap: 6 }}>{proc && proc.status !== 'done' && (
              <span className={`tag ${proc.status === 'failed' ? 'yellow' : 'gray'}`} title="Анализ файлов публикации не завершён — оценка посчитана только по тексту">{proc.status === 'failed' ? 'анализ не выполнен' : 'предварительная'}</span>
            )}<ZoneTag zone={s.zone} /></span></div>
            <div style={{ display: 'grid', gridTemplateColumns: '290px minmax(0, 1fr)', gap: 32, alignItems: 'start' }}>
              <div>
                <div className="row" style={{ alignItems: 'flex-end', gap: 6, margin: '8px 0 16px' }}><span className="big-num" style={{ fontSize: 52 }}>{s.score}</span><span className="c3" style={{ paddingBottom: 4 }}>из 100</span></div>
                <Gauge value={s.score} />
                {s.needsCheck && <div style={{ marginTop: 14 }}><NeedsCheck /></div>}
                {proc && proc.status !== 'done' && (
                  <p className="t-xs c2" style={{ marginTop: 12, lineHeight: '17px' }}>{proc.status === 'failed'
                    ? 'Анализ не выполнен — оценка может быть неполной: текст со скриншотов и видео мог в неё не войти. Это не значит, что риска нет: проверьте вручную.'
                    : 'Оценка предварительная: анализ файлов публикации ещё не закончился, текст со скриншотов и видео в неё пока не вошёл.'}</p>
                )}
                {c && cr && (
                  <Link to={`/complexes/${c.id}`} state={from(`Публикация ${adKey(a.id)}`, `/ads/${a.id}`)} className="row" style={{ marginTop: 18, background: 'var(--bg)', borderRadius: 16, padding: 8, gap: 12 }}>
                    <img src={c.cover} alt="" style={{ width: 52, height: 52, borderRadius: 12, objectFit: 'cover' }} />
                    <div style={{ flex: 1, minWidth: 0 }}><div className="bold t-sm">ЖК «{c.name}»</div><div className="t-xs c2">риск объекта · привязка — {fact ? 'факт' : linkText(a, lc, true)}</div></div>
                    <Score value={cr.score} zone={cr.zone} />
                  </Link>
                )}
              </div>
              <FactorList factors={s.factors} items={false} />
            </div>
          </div>

          <div className="card">
            <h3 className="t-h3" style={{ marginBottom: 4 }}>Словарь модели: маркеры в тексте</h3>
            <p className="t-sm c2" style={{ marginBottom: 12 }}>Детерминированное правило модели риска, не гипотеза: маркер предложения денег даёт R2, маркер продажи — R3. Цитаты-основания — под плитками: из текста или со скриншота (OCR).</p>
            <div className="tiles">
              {([['Признаки продажи', s.signals.sale, ''], ['Предложение денег · R2', s.signals.funds, 'fund'], ['Признаки законности', s.signals.safe, 'safe']] as const).map(([t, list, cls]) => (
                <div key={t} className="tile" style={{ background: 'var(--bg)', minHeight: 110 }}>
                  <span className="nm c2" style={{ fontSize: 13 }}>{t}</span>
                  <span className="vl num" style={{ fontSize: 28, margin: '2px 0 6px' }}>{list.length}</span>
                  <div className="row wrap-f" style={{ gap: 4 }}>{list.map((k) => <mark key={k} className={cls} style={{ fontSize: 13 }}>{k}</mark>)}</div>
                </div>
              ))}
            </div>
            {quotes.length > 0 && (
              <div className="ev-list" style={{ marginTop: 14 }}>
                {quotes.map(({ f, x }) => (
                  <div key={f.code} className="ev" style={{ gridTemplateColumns: '28px minmax(0, 1fr)' }}>
                    <span className="bold">{f.code}</span>
                    <div>
                      <div className="q"><Quote text={x.quote ?? ''} marker={x.marker} /></div>
                      {(x.quoteFrom === 'ocr' || f.state !== 'hit') && (
                        <div className="row wrap-f" style={{ gap: 6, marginTop: 4 }}>
                          {x.quoteFrom === 'ocr' && <OcrTag />}
                          {f.state !== 'hit' && <span className="tag gray" title={f.evidence}>в балл не входит</span>}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <AiCard key={a.id} a={a} s={s} c={c} load={ai} lc={lc} retry={() => setAiVer((v) => v + 1)} onLink={() => setModal('cx')} />

          {/* анализа Claude не было (ключ не задан, демо, ручной ввод) — блока нет. «Проанализировать ещё раз» ставит
              задание — дальше опрос ленты, как у собранной публикации в анализе (recheck) */}
          {(a.llm || (mode === 'api' && a.origin === 'collected')) && <LlmCard key={`llm-${a.id}`} a={a} x={a.llm ?? null} onQueued={recheck} />}

          {proc && live && <SourceVersions key={a.id} a={a} version={proc.version} />}

          <div className="card">
            <div className="card-head"><h3>История</h3>{mine?.events && <span className="t-sm c2">{mine.events.length} · журнал сервера</span>}</div>
            {demo && <div className="box warn t-sm alog-note">Демо-режим: журнал не сохраняется на сервере. Здесь — только действия этой сессии; полная история с автором и временем хранится на бэкенде (<code>./dev.sh</code>).</div>}
            {mine?.err && (
              <div className="alog-err t-sm">Журнал не загрузился: {mine.err} <button className="link" onClick={reload}>Повторить</button></div>
            )}
            {loadingLog ? <div className="t-sm c2 alist-empty">{mode === 'loading' ? 'Подключаемся к серверу…' : 'Загружаем журнал…'}</div>
              : !noLog && <AuditList events={events} links={['signal']} empty={demo ? 'В этой сессии действий с публикацией ещё не было' : 'Событий пока нет'} />}
            <div className="row" style={{ paddingTop: 12, gap: 8 }}>
              <input className="input" placeholder="Комментарий: с чем сверено, на чём основано решение" value={comment} maxLength={TEXT_MAX} aria-label="Комментарий"
                onChange={(e) => setComment(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') send() }} disabled={mode === 'loading'} />
              <button className="btn d m" disabled={!comment.trim() || sending || mode === 'loading'} aria-label="Отправить комментарий" onClick={send}><IcSend size={20} /></button>
            </div>
            <p className="t-xs c2" style={{ marginTop: 6 }}>Автор комментария в журнале — {ME || '—'}{demo && ' (демо-режим: только до перезагрузки)'}</p>
          </div>
        </div>

        {/* липкие — только статус и решения: колонка короче окна, без своей прокрутки; внизу страницы она уходит вверх
            вместе с концом контента, а решения в её низу остаются рядом с полем комментария */}
        <aside className="stack sticky">
          <div className="card">
            <Cell title="Статус" end={<><StatusDot s={a.status} /><span style={{ color: 'var(--text)' }}>{statusMeta[a.status].name}</span></>} chevron onClick={() => setModal('status')} />
            <Cell title="Ответственный" end={a.assignee ? <><span className="ava sm">{initialsOf(a.assignee, analysts)}</span><span style={{ color: 'var(--text)' }}>{a.assignee}</span></> : 'не назначен'} />
            <Cell title="Объект" end={<span style={{ color: 'var(--text)', maxWidth: 170, overflow: 'hidden', textOverflow: 'ellipsis' }}>{c ? c.name : 'не определён'}</span>} chevron onClick={() => setModal('cx')}
              sub={c ? <span className={`tag ${fact ? 'green' : 'yellow'}`} style={{ marginTop: 4 }}
                title={fact ? `Привязка к ЖК — ${linkText(a, lc)}` : `Привязка к ЖК — ${linkText(a, lc)}. Пока аналитик её не примет, публикация не даёт баллов объекту`}>{linkText(a, lc, true)}</span> : undefined} />
            {c && !fact && (
              <div style={{ padding: '2px 0 14px' }}>
                <button className="btn g m full" disabled={linking} onClick={() => confirmLink(c)}
                  title="Привязку предложил ИИ — это гипотеза. Пока её не приняли, публикация не даёт объекту баллов R2/R3, а R1 объекта не входит в её оценку">
                  <IcCheck size={20} />{linking ? 'Сохраняем…' : 'Принять привязку'}</button>
                <p className="t-xs c2" style={{ marginTop: 6, lineHeight: '17px' }}>Пока привязка — гипотеза ИИ, публикация не даёт объекту баллов R2/R3
                  {live && lc.now ? `; при принятии сохранится уверенность ${pct(lc.now.confidence)}${lc.differs ? ` (${lc.when} — ${pct(lc.saved)})` : ''}` : ''}.</p>
              </div>
            )}
          </div>

          <div className="card">
            <div className="stack" style={{ gap: 8 }}>
              {(a.status === 'new' || a.status === 'needs_check') && <button className="btn y full" onClick={() => decide('in_review', 'Публикация взята в работу')}>Взять в работу</button>}
              {/* решение, совпадающее с текущим статусом, не предлагаем повторно — только сменить его */}
              {a.status !== 'confirmed' && <button className="btn d full" onClick={() => setModal('confirmed')}>Подтвердить нарушение</button>}
              {c ? <SignalAction complex={c} label="Сформировать сигнал по ЖК" compact />
                : <p className="t-sm c2" style={{ lineHeight: '18px' }}><button className="link" disabled={linking} onClick={() => setModal('cx')}>Привяжите публикацию к ЖК</button> — привязка сохраняется в журнале, после этого можно сформировать сигнал</p>}
              {(a.status === 'confirmed' || a.status === 'dismissed') && <div className="t-xs c2" style={{ marginTop: 4 }}>Решение принято — «{statusMeta[a.status].name}», основание в истории. Изменить решение:</div>}
              <div className="row" style={{ gap: 8 }}>
                {a.status !== 'dismissed' && <button className="btn g m" style={{ flex: 1 }} onClick={() => setModal('dismissed')}>Отклонить</button>}
                {a.status !== 'needs_check' && <button className="btn g m" style={{ flex: 1 }} onClick={() => setModal('needs_check')}>Доп. проверка</button>}
              </div>
            </div>
            <p className="t-xs c2" style={{ marginTop: 12, lineHeight: '17px' }}>ИИ даёт рекомендацию. Решение принимает сотрудник, автоматической отправки в госорганы нет.</p>
          </div>
        </aside>
      </div>

      {modal === 'status' && (
        <Modal title="Статус публикации" onClose={() => setModal(null)}>
          {ORDER.map((st) => <Cell key={st} icon={<StatusDot s={st} />} title={statusMeta[st].name} end={a.status === st ? <IcCheck size={22} strokeWidth={2.5} style={{ color: 'var(--text)' }} /> : undefined}
            onClick={() => (st === a.status ? setModal(null) : isDecision(st) ? setModal(st) : decide(st, `Статус: ${statusMeta[st].name}`))} />)}
        </Modal>
      )}
      {modal === 'cx' && (
        <Modal title="Привязать к объекту" onClose={() => setModal(null)}>
          <div style={{ padding: '8px 16px' }}><div className="search"><IcSearch size={20} /><input autoFocus placeholder="Название или адрес ЖК" value={cq} onChange={(e) => setCq(e.target.value)} /></div></div>
          {a.complexId && <Cell icon={<span className="ico"><IcClose size={20} /></span>} title="Снять привязку" sub="Публикация останется без ЖК — нужна ручная привязка" onClick={() => link(null)} />}
          {complexes.filter((x) => matches(cq, x.name, x.address)).map((x) => (
            <Cell key={x.id} inset icon={<img className="th" src={x.cover} alt="" style={{ width: 44, height: 44, borderRadius: 12 }} />} title={x.name}
              sub={x.id === a.complexId ? (fact ? 'привязка — факт' : `${linkText(a, lc, true)} — выбор примет привязку`) : x.address}
              end={x.id === a.complexId ? <IcCheck size={22} strokeWidth={2.5} style={{ color: 'var(--text)' }} /> : undefined}
              onClick={() => (x.id === a.complexId && fact ? setModal(null) : link(x))} />
          ))}
        </Modal>
      )}
      {dm && (
        <Modal title={DECISION[dm].title} onClose={() => setModal(null)}
          footer={<>
            <button className="btn g" onClick={() => setModal(null)}>Отмена</button>
            <button className={`btn ${dm === 'confirmed' ? 'y' : 'd'}`} disabled={DECISION[dm].required && !reason.trim()} onClick={() => decide(dm, DECISION[dm].done, reason)}>{DECISION[dm].button}</button>
          </>}>
          <div className="cell" style={{ minHeight: 56 }}><span className="ico"><IcBuilding size={20} /></span><div className="main"><div className="ttl">{c ? `ЖК «${c.name}»` : 'ЖК не определён'}</div><div className="sub">{a.account} · {statusMeta[a.status].name} → {statusMeta[dm].name}</div></div></div>
          <div className="cell" style={{ minHeight: 56 }}><span className="ico"><IcUsers size={20} /></span><div className="main"><div className="ttl">{ME}</div><div className="sub">автор, дата и время решения попадут в журнал</div></div></div>
          <div style={{ padding: '12px 24px 4px' }}>
            <label className="t-sm c2" htmlFor="reason">{DECISION[dm].label} · попадёт в журнал</label>
            <textarea id="reason" className="input" style={{ marginTop: 6 }} autoFocus maxLength={TEXT_MAX} placeholder={DECISION[dm].hint} value={reason}
              onChange={(e) => setReasons((r) => ({ ...r, [dm]: e.target.value }))} />
          </div>
        </Modal>
      )}
      {viewer !== null && gallery.length > 0 && (
        <MediaViewer items={gallery} start={viewer} title={`Файлы публикации ${adKey(a.id)}`} fileBase={adKey(a.id)}
          meta={{ platform: a.platform, account: a.account, date: a.date }} ocr={a.ocr} onClose={() => setViewer(null)} />
      )}
    </>
  )
}

/** saved — гипотеза по сохранённой привязке, которой нет в текущей выдаче: её уверенность — записанная при сборе или добавлении */
type AiModal = { kind: 'reject'; h: LinkHypothesis; saved?: boolean } | { kind: 'terms' }
/** Черновик «почему» — свой у каждого решения: текст, набранный и отменённый для одного ЖК, не уходит в журнал с другим решением */
const whyKey = (m: AiModal) => (m.kind === 'reject' ? `r:${m.h.complexId}` : 't')
const cap = (x: string) => x.charAt(0).toUpperCase() + x.slice(1)

/** «Анализ ИИ» (ТЗ §6): гипотезы ЖК и условия оплаты с цитатами и уверенностью; принять или отклонить — решение аналитика в журнал.
 *  load — анализ страницы (AdDetail), lc — уверенность в текущей привязке по нему же, onLink — открыть ручную привязку */
function AiCard({ a, s, c, load, lc, retry, onLink }: { a: Ad; s: AdScore; c?: Complex; load: AiLoad | null; lc: LinkConf; retry: () => void; onLink: () => void }) {
  const { mode, complexes, acceptHypothesis, rejectHypothesis, reviewTerms, notify } = useStore()
  const live = mode === 'api'
  const [busy, setBusy] = useState<string | null>(null)
  const [modal, setModal] = useState<AiModal | null>(null)
  const [whys, setWhys] = useState<Record<string, string>>({})

  const data = load?.data
  const fact = linkConfirmed(a)
  // решения аналитика — из публикации в сторе (обновляется сразу после ответа), иначе из анализа: пока анализ перечитывается,
  // отклонённая гипотеза не висит в списке с активными кнопками
  const review = a.termsReview !== undefined ? a.termsReview : data?.termsReview ?? null
  const reviewBy = a.termsReviewBy ?? data?.termsReviewBy
  const reviewAt = a.termsReviewAt ?? data?.termsReviewAt
  const { rejected: rejectedIds, hypotheses: hyps } = visibleHypotheses(a, data)
  const rejected = rejectedIds.map((id) => complexes.find((x) => x.id === id)?.name ?? id)
  const moneyFound = !!data?.terms.some((t) => t.money) || s.signals.funds.length > 0
  // текущая привязка-гипотеза, которой нет среди гипотез по тексту (например, получена при сборе публикации), — решение по ней здесь же
  const conf = a.matchConfidence || 0
  const orphan: LinkHypothesis | null = c && !fact && data && !hyps.some((h) => h.complexId === c.id)
    ? { complexId: c.id, complexName: c.name, confidence: conf, low: conf < model.thresholds.lowConfidence, reasons: [] } : null
  const wk = modal ? whyKey(modal) : ''
  // без гипотез пояснение сервера «привязку делает аналитик вручную» повторяет подсказку в блоке привязки — его не показываем
  const note = hyps.length ? data?.note : data?.note?.split('. ').filter((x) => !x.includes('привязку делает')).join('. ')

  /** wk — черновик комментария этого решения: после успеха очищается, при ошибке остаётся для повтора */
  const act = (k: string, p: () => Promise<unknown>, done: string, key?: string) => {
    setBusy(k)
    setModal(null)
    // ошибка — в баннере (стор) и коротко в тосте
    p().then(() => { if (key) setWhys((w) => ({ ...w, [key]: '' })); notify(done) }, (e: unknown) => notify(`Не сохранено: ${errorText(e)}`)).finally(() => setBusy(null))
  }
  const accept = (h: LinkHypothesis) => act(`a:${h.complexId}`, () => acceptHypothesis(a.id, h.complexId),
    `Гипотеза принята: привязка к ЖК «${h.complexName}» теперь факт, риск объекта пересчитан`)
  const reject = (m: Extract<AiModal, { kind: 'reject' }>) => {
    const key = whyKey(m)
    act(`r:${m.h.complexId}`, () => rejectHypothesis(a.id, m.h.complexId, whys[key]), `Гипотеза отклонена: ЖК «${m.h.complexName}» ИИ больше не предлагает`, key)
  }
  const terms = (d: 'accepted' | 'rejected') => act(`t:${d}`, () => reviewTerms(a.id, d, d === 'rejected' ? whys.t : undefined),
    d === 'accepted' ? 'Отмечено: условия оплаты верны — решение в журнале' : 'Отмечено: это не предложение внести деньги — R2 по публикации не засчитывается',
    d === 'rejected' ? 't' : undefined)

  return (
    <div className="card">
      <div className="card-head"><h3>Анализ ИИ</h3><AiBadge label="гипотезы ИИ" /></div>
      <p className="t-sm c2" style={{ marginBottom: 8 }}>ИИ предлагает — решает аналитик. Гипотеза входит в балл только после того, как её примут; каждое решение пишется в журнал. Юридический вывод ИИ не делает.</p>
      {!live ? (
        <div className="ai-note">{mode === 'loading' ? 'Подключаемся к серверу…'
          : 'Демо-режим без бэкенда: анализ ИИ (условия оплаты, гипотезы ЖК) выполняется на сервере. Привязку к ЖК можно принять или выбрать вручную справа — в демо она живёт только до перезагрузки.'}</div>
      ) : !data ? (
        load?.err ? <div className="ai-note err">Анализ не загрузился: {load.err} <button className="link" onClick={retry}>Повторить</button></div>
          : <div className="ai-note">Загружаем анализ…</div>
      ) : (
        <>
          {load?.err && <div className="ai-note err" style={{ marginBottom: 8 }}>Не удалось обновить анализ: {load.err} <button className="link" onClick={retry}>Повторить</button></div>}
          <div className="ai-sub">Привязка к ЖК</div>
          {c && !fact && lc.now ? (
            // гипотеза есть в текущей выдаче: её уверенность и сохранится при принятии; записанная при сборе — отдельно, если другая
            <div className="ai-decision no">
              <div className="row between" style={{ gap: 8, alignItems: 'flex-start' }}><span>ЖК «{c.name}» — гипотеза ИИ, аналитик её не принял</span><Confidence value={lc.now.confidence} low={lc.now.low} /></div>
              <div style={{ marginTop: 4 }}>
                {lc.differs && `${cap(lc.when)} публикации уверенность была ${pct(lc.saved)}, по текущему анализу — ${pct(lc.now.confidence)}: при принятии сохранится ${pct(lc.now.confidence)}. `}
                В балл объекта не входит, пока аналитик её не примет
              </div>
            </div>
          ) : (
            <div className={`ai-decision ${c ? (fact ? 'ok' : 'no') : ''}`}>
              {c ? <>ЖК «{c.name}» — {linkText(a, lc)}{fact ? '' : '. В балл объекта не входит, пока аналитик её не примет'}</>
                : hyps.length ? 'ЖК не определён: примите одну из гипотез ниже или привяжите публикацию вручную'
                  // одна подсказка вместо трёх: ИИ не предлагает ЖК — что делать дальше
                  : <>ЖК не определён: ИИ не нашёл в тексте признаков конкретного ЖК{rejected.length ? ' среди неотклонённых' : ''}. <button className="link" onClick={onLink}>Привязать вручную</button></>}
            </div>
          )}
          {orphan && (
            <div className="row wrap-f" style={{ gap: 8, marginTop: 10 }}>
              <button className="btn g m" disabled={!!busy} onClick={() => accept(orphan)}><IcCheck size={18} />{busy === `a:${orphan.complexId}` ? 'Сохраняем…' : 'Принять привязку'}</button>
              <button className="btn g m" disabled={!!busy} onClick={() => setModal({ kind: 'reject', h: orphan, saved: true })}><IcClose size={18} />{busy === `r:${orphan.complexId}` ? 'Сохраняем…' : 'Отклонить гипотезу'}</button>
            </div>
          )}
          {hyps.length > 0 ? (
            <div className="ai-list" style={{ marginTop: 4 }}>
              {hyps.map((h) => {
                const current = h.complexId === a.complexId
                return (
                  <HypothesisItem key={h.complexId} h={h} note={current && !fact ? `текущая привязка — гипотеза${lc.differs ? ` · ${lc.when} — ${pct(lc.saved)}` : ''}` : undefined}>
                    {current && fact ? <span className="tag green"><IcCheck size={12} strokeWidth={3} />{a.linkConfirmedBy ? 'принята — привязка факт' : 'текущая привязка — факт'}</span> : (
                      <>
                        <button className="btn g m" disabled={!!busy} onClick={() => accept(h)}><IcCheck size={18} />{busy === `a:${h.complexId}` ? 'Сохраняем…' : 'Принять привязку'}</button>
                        <button className="btn g m" disabled={!!busy} onClick={() => setModal({ kind: 'reject', h })}><IcClose size={18} />{busy === `r:${h.complexId}` ? 'Сохраняем…' : 'Отклонить гипотезу'}</button>
                      </>
                    )}
                  </HypothesisItem>
                )
              })}
            </div>
          ) : c && <div className="t-sm c2" style={{ marginTop: 10 }}>Других ЖК ИИ по тексту не предлагает</div>}
          {rejected.length > 0 && <p className="t-xs c2" style={{ marginTop: 8 }}>Отклонены аналитиком: {rejected.map((n) => `ЖК «${n}»`).join(', ')} — ИИ их больше не предлагает</p>}

          <div className="ai-sub">Условия оплаты <span className="t-sm c2" style={{ fontWeight: 400 }}>{data.terms.length}</span></div>
          <TermList terms={data.terms} empty="ИИ не нашёл условий оплаты" />
          {review && (
            <div className={`ai-decision ${review === 'accepted' ? 'ok' : 'no'}`}>
              {review === 'accepted' ? 'Аналитик отметил: условия верны — это предложение внести деньги' : 'Аналитик отметил: это не предложение внести деньги — R2 по публикации не засчитывается'}
              {reviewBy && ` · ${reviewBy}`}{reviewAt && `, ${fmtStamp(reviewAt)}`}
            </div>
          )}
          {(moneyFound || review) && (
            <div className="row wrap-f" style={{ gap: 8, marginTop: 10 }}>
              {review !== 'accepted' && <button className="btn g m" disabled={!!busy} onClick={() => terms('accepted')}><IcCheck size={18} />{busy === 't:accepted' ? 'Сохраняем…' : 'Условия верны'}</button>}
              {review !== 'rejected' && <button className="btn g m" disabled={!!busy} onClick={() => setModal({ kind: 'terms' })}><IcClose size={18} />{busy === 't:rejected' ? 'Сохраняем…' : 'Это не предложение денег'}</button>}
            </div>
          )}
          <p className="t-xs c2" style={{ marginTop: 8, lineHeight: '17px' }}>R2 ставит правило модели по словарю — это воспроизводимая цитата, а не вывод ИИ. Отметка «Это не предложение денег» снимает R2 этой публикации и у объекта; отметка «Условия верны» на балл не влияет.</p>

          {(data.phones.length > 0 || data.addresses.length > 0) && (
            <div className="row wrap-f" style={{ gap: 6, marginTop: 12 }}>
              <span className="t-sm c2">Найдено в тексте:</span>
              {data.phones.map((p) => <span key={p} className="tag gray">тел. {p}</span>)}
              {data.addresses.map((x) => <span key={x} className="tag gray">{x}</span>)}
            </div>
          )}
          {note && <div className="ai-note" style={{ marginTop: 12 }}>{note}</div>}
          <div className="t-xs c3" style={{ marginTop: 12 }}>Движок {data.engine} · офлайн: правила, словари, транслитерация, без внешних API · одинаковый текст — одинаковый результат</div>
        </>
      )}

      {modal && (
        <Modal title={modal.kind === 'reject' ? 'Отклонить гипотезу ИИ?' : 'Это не предложение внести деньги?'} onClose={() => setModal(null)}
          footer={<>
            <button className="btn g" onClick={() => setModal(null)}>Отмена</button>
            <button className="btn d" onClick={() => (modal.kind === 'reject' ? reject(modal) : terms('rejected'))}>{modal.kind === 'reject' ? 'Отклонить гипотезу' : 'Это не предложение денег'}</button>
          </>}>
          {modal.kind === 'reject' ? (
            <div className="cell" style={{ minHeight: 56 }}><span className="ico"><IcBuilding size={20} /></span><div className="main">
              <div className="ttl">ЖК «{modal.h.complexName}» · уверенность {modal.saved ? `${lc.when} ` : ''}{pct(modal.h.confidence)}</div>
              <div className="sub">{modal.h.complexId === a.complexId && !fact ? 'Текущая привязка-гипотеза будет снята. ' : ''}ИИ больше не будет предлагать этот ЖК для публикации</div>
            </div></div>
          ) : (
            <p className="t-sm" style={{ padding: '8px 24px', lineHeight: '20px' }}>R2 по этой публикации не будет засчитан — ни в её оценке, ни у объекта. Решение с вашим именем попадёт в журнал; его можно изменить.</p>
          )}
          <div style={{ padding: '12px 24px 4px' }}>
            <label className="t-sm c2" htmlFor="ai-why">Почему — необязательно · попадёт в журнал</label>
            <textarea id="ai-why" className="input" style={{ marginTop: 6 }} autoFocus maxLength={TEXT_MAX} value={whys[wk] ?? ''} onChange={(e) => { const v = e.target.value; setWhys((w) => ({ ...w, [wk]: v })) }}
              placeholder={modal.kind === 'reject' ? 'Например: в тексте другой ЖК с похожим названием' : 'Например: бронь бесплатная, деньги принимаются только по ДДУ с гарантией ЕО'} />
          </div>
        </Modal>
      )}
    </div>
  )
}

/** Claude ещё не разбирал публикацию: собранные публикации дорабатываются постепенно, в пределах дневного бюджета */
const PENDING_LLM = { tone: 'gray' as const, label: 'ещё не выполнен',
  note: 'Анализ ИИ ещё не выполнялся: публикация ждёт постепенной доработки в пределах дневного бюджета. Можно не ждать — «Проанализировать сейчас».' }

/** «Анализ ИИ (Claude)»: гипотезы языковой модели по тексту и OCR — признаки продажи, суммы, кандидаты ЖК с дословными
 *  цитатами (сервер сверил их с текстом), неясности. В балл не входят; привязку и решение принимает аналитик.
 *  Отказ модели, сбой и пропуск — «анализ ИИ не выполнен», а не «риска нет».
 *  «Проанализировать ещё раз» — администратор (запрос расходует дневной бюджет); onQueued — перечитать ленту и опрашивать её,
 *  пока анализ не закончится: новый результат придёт в a.llm. x = null — Claude эту публикацию ещё не разбирал
 *  (она ждёт постепенной доработки в пределах бюджета) — кнопка позволяет не ждать очереди */
function LlmCard({ a, x, onQueued }: { a: Ad; x: LlmAnalysis | null; onQueued: () => void }) {
  const { mode, user, notify } = useStore()
  const [admin, setAdmin] = useState(() => isAdmin())
  useEffect(() => onAdminChange(setAdmin), [])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const author = user?.name ?? ''
  const block = reanalyzeBlock({ live: mode === 'api', admin, collected: a.origin === 'collected', processing: a.processing, author })
  const st = llmState(x, fmtDT) ?? PENDING_LLM
  const dropped = x ? droppedText(x.dropped) : null
  const done = x?.status === 'done'
  const again = async () => {
    if (block || busy) return
    setBusy(true)
    setErr(null)
    try {
      await reanalyzeAd(a.id, author)
      notify('Повторный анализ ИИ запрошен — новый результат появится в этом блоке, прежний сохранится в истории')
      onQueued()
    } catch (e) {
      setErr(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="card">
      <div className="card-head"><h3>Анализ ИИ (Claude)</h3><span className="row" style={{ gap: 6 }}><span className={`tag ${st.tone}`}>{st.label}</span><AiBadge label="гипотезы ИИ" /></span></div>
      <p className="t-sm c2" style={{ marginBottom: 8 }}>Языковая модель Anthropic читает текст публикации и OCR. Всё ниже — гипотезы: в балл риска не входят, привязку к ЖК и решение по публикации принимает аналитик. Цитаты сервер сверил с текстом — остальное модель не подтверждает.</p>
      {st.note && <div className={`ai-note ${!x || (x.status === 'failed' && x.retryAt) ? '' : 'warn'}`}>{st.note}</div>}
      {x && done && (
        <>
          {x.summary && <p className="t-sm" style={{ lineHeight: '20px', marginTop: 4 }}>{x.summary}</p>}
          <div className="ai-sub">Признаки продажи <span className="t-sm c2" style={{ fontWeight: 400 }}>{x.saleSignals.length}</span></div>
          {x.saleSignals.length ? (
            <div className="ev-list">
              {x.saleSignals.map((g, i) => (
                <div key={`${g.type}-${i}`} className="ev" style={{ gridTemplateColumns: '150px minmax(0, 1fr)' }}>
                  <span className="bold">{signalLabel(g.type)}</span>
                  <div><div className="q">«{g.quote}»</div>{g.source === 'ocr' && <div style={{ marginTop: 4 }}><OcrTag /></div>}</div>
                </div>
              ))}
            </div>
          ) : <div className="t-sm c2">Модель не выделила признаков продажи — это не вывод об отсутствии риска</div>}
          {x.amounts.length > 0 && (
            <>
              <div className="ai-sub">Суммы <span className="t-sm c2" style={{ fontWeight: 400 }}>{x.amounts.length}</span></div>
              <div className="ev-list">
                {x.amounts.map((m, i) => (
                  <div key={`amt-${i}`} className="ev" style={{ gridTemplateColumns: '150px minmax(0, 1fr)' }}>
                    <span className="bold num">{amountText(m)}</span>
                    <div><div className="meta">{m.kind || 'сумма'}</div><div className="q">«{m.quote}»</div>{m.source === 'ocr' && <div style={{ marginTop: 4 }}><OcrTag /></div>}</div>
                  </div>
                ))}
              </div>
            </>
          )}
          <div className="ai-sub">Кандидаты ЖК <span className="t-sm c2" style={{ fontWeight: 400 }}>{x.complexCandidates.length}</span></div>
          {x.complexCandidates.length ? (
            <div className="ev-list">
              {x.complexCandidates.map((k) => (
                <div key={k.complexId} className="ev" style={{ gridTemplateColumns: 'minmax(0, 1fr) auto' }}>
                  <div>
                    <Link className="bold" to={`/complexes/${k.complexId}`} state={from(`Публикация ${adKey(a.id)}`, `/ads/${a.id}`)}>ЖК «{k.complexName}»</Link>
                    <div className="q">«{k.quote}»</div>
                    <div className="meta" style={{ marginTop: 2 }}>гипотеза — решение за аналитиком{k.complexId === a.complexId ? (a.linkConfirmedBy ? ' · совпадает с привязкой аналитика' : ' · совпадает с текущей привязкой-гипотезой') : ''}</div>
                  </div>
                  <span className={`tag ${CONF_TONE[k.confidence] ?? 'gray'}`} title="Уверенность модели — не вероятность и не факт">{CONF_LABEL[k.confidence] ?? k.confidence}</span>
                </div>
              ))}
            </div>
          ) : <div className="t-sm c2">Модель не предложила ЖК из справочника — привязку делает аналитик</div>}
          {x.uncertainties.length > 0 && (
            <>
              <div className="ai-sub">Требует проверки</div>
              <ul className="t-sm" style={{ margin: '4px 0 0', paddingLeft: 18, lineHeight: '20px' }}>{x.uncertainties.map((u, i) => <li key={i}>{u}</li>)}</ul>
            </>
          )}
          {dropped && <p className="t-xs c2" style={{ marginTop: 8 }}>{dropped} — их нет в выдаче.</p>}
        </>
      )}
      {x && (
        <div className="t-xs c3" style={{ marginTop: 12 }}>
          Claude · {modelText(x)}{x.updatedAt ? ` · ${fmtDT(x.updatedAt)}` : ''}{x.contentVersion && x.contentVersion > 1 ? ` · версия ${x.contentVersion}` : ''}
          {(x.inputTokens > 0 || x.outputTokens > 0) && ` · ${x.inputTokens.toLocaleString('ru-RU')} + ${x.outputTokens.toLocaleString('ru-RU')} токенов, ${costText(x.costUsd)}`}
          {' '}· промпт {x.promptVersion} · текст и OCR отправлены в Anthropic
        </div>
      )}
      <div className="row wrap-f" style={{ gap: 8, marginTop: 12, alignItems: 'center' }}>
        <button className="btn g m" disabled={!!block || busy} title={block ?? REANALYZE_HINT} onClick={() => void again()}>
          {busy ? 'Запрашиваем…' : x ? 'Проанализировать ещё раз' : 'Проанализировать сейчас'}</button>
        <span className="t-xs c2" style={{ flex: '1 1 240px', lineHeight: '17px' }}>{block ?? REANALYZE_HINT}</span>
      </div>
      {err && <div className="ai-note err" style={{ marginTop: 8 }}>Повторный анализ не запрошен: {err}</div>}
    </div>
  )
}

/** Состояние анализа собранной публикации, пока он не готов: в очереди и идёт — серая плашка с «Обновить»,
 *  не выполнен — предупреждение. Не выполненный анализ — «анализ не выполнен», а не «риска нет» */
function ProcessingNote({ p, polling, onRefresh }: { p: Processing; polling: boolean; onRefresh: () => void }) {
  const note = processingNote(p)
  const ver = p.version > 1 ? ` (версия ${p.version})` : ''
  if (p.status === 'failed') return (
    <div className="box warn t-sm" style={{ padding: '12px 16px', lineHeight: '19px' }}>
      <b>{note}</b>
      <div style={{ marginTop: 4 }}>{p.stage ? `Этап: ${stageLabel(p.stage)}${ver}. ` : ver ? `Версия ${p.version}. ` : ''}Текст со скриншотов и видео может быть не распознан, гипотезы ИИ — неполными.
        Оценка — только по тексту публикации: это не значит, что риска нет.</div>
    </div>
  )
  return (
    <div className="ai-note">{note}{ver}. Оценка пока только по тексту{polling ? ' — обновляем каждые 10 с' : ''}. <button className="link" onClick={onRefresh}>Обновить</button>
      {!polling && p.status === 'queued' && <> · Долго в очереди — проверьте воркер сбора на странице <Link className="link" to="/sources">«Источники»</Link></>}
    </div>
  )
}

/** Версии публикации с сервера: key — чьи (публикация и её текущая версия); при ошибке перечитывания остаются прежние.
 *  data null — недоступно (нет связи, сервер без сбора публикаций) */
type VerLoad = { key: string; data?: AdVersion[] | null; err?: string }

/** «Версии в источнике (N)»: как публикация менялась в источнике. Загружается только при раскрытии */
function SourceVersions({ a, version }: { a: Ad; version: number }) {
  const [open, setOpen] = useState(false)
  const [load, setLoad] = useState<VerLoad | null>(null)
  const [ver, setVer] = useState(0)
  const key = `${a.id}:${version}`
  useEffect(() => {
    if (!open) return
    let alive = true
    getVersions(a.id).then(
      (data) => { if (alive) setLoad({ key, data }) },
      (e: unknown) => { if (alive) setLoad((l) => ({ key, data: l?.key === key ? l.data : undefined, err: errorText(e) })) },
    )
    return () => { alive = false }
  }, [open, key, ver]) // eslint-disable-line react-hooks/exhaustive-deps
  const mine = load?.key === key ? load : null
  const list = mine?.data
  const retry = () => setVer((v) => v + 1)

  return (
    <div className="card">
      <button className="row between" style={{ width: '100%', textAlign: 'left', gap: 12 }} aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="t-h3">Версии в источнике ({version})</span>
        {open ? <IcChevronUp size={20} /> : <IcChevronDown size={20} />}
      </button>
      {open && (
        <div className="fade" style={{ marginTop: 8 }}>
          <p className="t-sm c2" style={{ marginBottom: 8 }}>Каждая правка текста, цены или файлов в источнике — новая версия. Решения аналитика остаются за публикацией: после правки сверьте их с текущей версией.</p>
          {mine?.err && <div className="alog-err t-sm">Версии не загрузились: {mine.err} <button className="link" onClick={retry}>Повторить</button></div>}
          {!mine ? <div className="t-sm c2">Загружаем версии…</div>
            : list === null ? <div className="ai-note">История версий недоступна: нет связи с сервером или сервер без сбора публикаций. <button className="link" onClick={retry}>Повторить</button></div>
              : !list ? null
                : !list.length ? <div className="t-sm c2">Версий нет: публикация не из приёма</div>
                  : list.map((v, i) => {
                    const n = v.mediaKeys?.length ?? 0
                    return (
                      <div key={v.version} style={{ padding: '12px 0', borderTop: i ? '1px solid var(--line)' : undefined }}>
                        <div className="row wrap-f" style={{ gap: 8 }}>
                          <span className="bold">Версия {v.version}</span>
                          {v.current && <span className="tag green">текущая</span>}
                          <span className="t-sm c2">{v.collectedAt ? `получена ${fmtDT(v.collectedAt)}` : 'время не указано'}</span>
                          {v.price && <span className="tag gray">Цена: {v.price}</span>}
                          <span className="tag gray">{n ? `${n} ${plural(n, ['файл', 'файла', 'файлов'])}` : 'без файлов'}</span>
                        </div>
                        <div className="t-sm" style={{ marginTop: 6, lineHeight: '19px', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 152, overflowY: 'auto' }}>
                          {v.text ? <Highlight text={v.text} /> : <span className="c3">без текста</span>}
                        </div>
                      </div>
                    )
                  })}
        </div>
      )}
    </div>
  )
}
