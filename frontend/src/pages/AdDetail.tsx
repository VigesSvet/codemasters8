import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { AuditList } from '../components/Audit'
import { FactorList } from '../components/Factors'
import { SignalAction } from '../components/SignalPanel'
import { Cell, Gauge, Highlight, Modal, NeedsCheck, PageTitle, PlatformIcon, Score, StatusDot, ZoneTag, adKey, fmtDT, platformMeta, statusMeta } from '../components/ui'
import type { AdStatus, AuditEvent, Complex } from '../data/types'
import { errorText } from '../lib/api'
import { initialsOf } from '../lib/audit'
import { linkConfirmed } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcBuilding, IcCheck, IcClose, IcExternal, IcSearch, IcSend, IcUsers } from '../ui/icons'

const ORDER: AdStatus[] = ['new', 'needs_check', 'in_review', 'confirmed', 'dismissed']
const TEXT_MAX = 2000

/** Решения с основанием: для «Нарушение» оно обязательно, для остальных — по желанию */
type Decision = 'confirmed' | 'dismissed' | 'needs_check'
const DECISION: Record<Decision, { title: string; label: string; button: string; done: string; required: boolean; hint: string }> = {
  confirmed: { title: 'Подтвердить нарушение?', label: 'Основание решения — обязательно', button: 'Подтвердить', done: 'Нарушение подтверждено, основание записано в журнал', required: true,
    hint: 'Например: сверено с картотекой ГАСК, гарантии ЕО и разрешения МИО нет' },
  dismissed: { title: 'Отклонить публикацию?', label: 'Почему отклонено — необязательно', button: 'Отклонить', done: 'Публикация отклонена', required: false,
    hint: 'Например: у объекта действующая гарантия ЕО, номер указан в профиле' },
  needs_check: { title: 'Отправить на доп. проверку?', label: 'Что проверить — необязательно', button: 'Отправить', done: 'Отправлено на доп. проверку', required: false,
    hint: 'Например: запросить в ГАСК сведения о разрешении МИО' },
}
const isDecision = (m: string | null): m is Decision => !!m && m in DECISION

/** Журнал публикации с сервера: id — чья запись; при ошибке перечитывания остаются прежние события */
type Log = { id: string; events?: AuditEvent[]; err?: string }

/** Разбор публикации: слева сама реклама и история, справа — решение аналитика */
export default function AdDetail() {
  const { id } = useParams()
  const { mode, ads, complexes, setAdStatus, linkAd, addComment, getAudit, sessionLog, notify, riskOf, adScoreOf, user, analysts, revision } = useStore()
  const a = ads.find((x) => x.id === id)
  const [comment, setComment] = useState('')
  const [sending, setSending] = useState(false)
  const [modal, setModal] = useState<'status' | 'cx' | Decision | null>(null)
  const [reasons, setReasons] = useState<Partial<Record<Decision, string>>>({})
  const [cq, setCq] = useState('')
  const [linking, setLinking] = useState(false)
  const [log, setLog] = useState<Log | null>(null)
  const [ver, setVer] = useState(0)
  const live = mode === 'api'

  // журнал — с сервера; revision растёт после каждого сохранённого изменения (статус, привязка, комментарий —
  // в том числе из очереди, если PATCH завершился уже здесь) и после восстановления связи — тогда перечитываем
  useEffect(() => {
    if (!live || !id) return
    let alive = true
    getAudit({ adId: id }).then(
      (events) => { if (alive) setLog({ id, events }) },
      (e: unknown) => { if (alive) setLog((l) => ({ id, events: l?.id === id ? l.events : undefined, err: errorText(e) })) },
    )
    return () => { alive = false }
  }, [id, live, ver, revision]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!a) return <div className="empty">Публикация не найдена</div>
  // ЖК — только сохранённая привязка из стора (с бэкендом — PATCH /ads/{id}/complex, событие в журнале)
  const c = a.complexId ? complexes.find((x) => x.id === a.complexId) : undefined
  const s = adScoreOf(a)
  const cr = c ? riskOf(c) : null
  const fact = linkConfirmed(a)
  const conf = Math.round((a.matchConfidence || 0) * 100)
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

  /** to — новый ЖК (null — снять привязку); подтверждение гипотезы — тот же ЖК с уверенностью 100% */
  const link = (to: Complex | null) => {
    setModal(null)
    const same = !!to && to.id === a.complexId
    const msg = !to ? 'Привязка к ЖК снята' : same ? `Привязка к ЖК «${to.name}» подтверждена` : `Публикация привязана к ЖК «${to.name}»`
    setLinking(true)
    // при ошибке стор откатывает привязку и показывает баннер
    linkAd(a.id, to?.id ?? null).then((ok) => {
      if (ok) notify(demo ? `${msg} — демо-режим, на сервере не сохранено` : `${msg}, оценка пересчитана, событие в журнале`)
    }).finally(() => setLinking(false))
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
      <PageTitle crumbs={[['Карта', '/'], ['Лента публикаций', '/ads'], [adKey(a.id)]]} title={c ? `Реклама ЖК «${c.name}»` : 'Реклама без привязки к ЖК'}
        sub={<span className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={22} />{platformMeta[a.platform].name} · {a.account} · {fmtDT(a.date)}</span>} />

      <div className="cols-r">
        <div className="stack" style={{ gap: 16 }}>
          <div className="card" style={{ display: 'grid', gridTemplateColumns: a.image ? '300px 1fr' : '1fr', gap: 28 }}>
            {a.image && <div className="ph" style={{ borderRadius: 18, aspectRatio: '1' }}><img src={a.image} alt="" /></div>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <p style={{ fontSize: 20, lineHeight: '28px' }}><Highlight text={a.text} /></p>
              {a.ocr && (
                <div style={{ background: 'var(--bg)', borderRadius: 16, padding: '12px 16px' }}>
                  <div className="t-xs c2" style={{ marginBottom: 4 }}>Текст на изображении (OCR)</div>
                  <div className="t-sm" style={{ letterSpacing: '.02em' }}><Highlight text={a.ocr} /></div>
                </div>
              )}
              <div className="row wrap-f" style={{ gap: 6, marginTop: 'auto' }}>
                {a.price && <span className="tag gray">Цена: {a.price}</span>}
                {a.reach && <span className="tag gray">Охват ~{a.reach.toLocaleString('ru')}</span>}
                <span className="tag gray">{a.source === 'manual' ? 'Добавлено вручную' : a.source === 'parser' ? 'Сбор парсером' : 'Демо'}</span>
                <a href={a.url} target="_blank" rel="noreferrer" className="tag outline"><IcExternal size={13} />Источник</a>
              </div>
            </div>
          </div>

          <div className="card">
            <h3 className="t-h3" style={{ marginBottom: 12 }}>Что нашёл анализ текста</h3>
            <div className="tiles">
              {([['Признаки продажи', s.signals.sale, ''], ['Предложение денег · R2', s.signals.funds, 'fund'], ['Признаки законности', s.signals.safe, 'safe']] as const).map(([t, list, cls]) => (
                <div key={t} className="tile" style={{ background: 'var(--bg)', minHeight: 110 }}>
                  <span className="nm c2" style={{ fontSize: 13 }}>{t}</span>
                  <span className="vl num" style={{ fontSize: 28, margin: '2px 0 6px' }}>{list.length}</span>
                  <div className="row wrap-f" style={{ gap: 4 }}>{list.map((k) => <mark key={k} className={cls} style={{ fontSize: 13 }}>{k}</mark>)}</div>
                </div>
              ))}
            </div>
          </div>

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

        <aside className="stack sticky">
          <div className="card">
            <Cell title="Статус" end={<><StatusDot s={a.status} /><span style={{ color: 'var(--text)' }}>{statusMeta[a.status].name}</span></>} chevron onClick={() => setModal('status')} />
            <Cell title="Ответственный" end={a.assignee ? <><span className="ava sm">{initialsOf(a.assignee, analysts)}</span><span style={{ color: 'var(--text)' }}>{a.assignee}</span></> : 'не назначен'} />
            <Cell title="Объект" end={<span style={{ color: 'var(--text)', maxWidth: 170, overflow: 'hidden', textOverflow: 'ellipsis' }}>{c ? c.name : 'не определён'}</span>} chevron onClick={() => setModal('cx')}
              sub={c && !fact ? <span className="tag yellow" style={{ marginTop: 4 }} title="Уверенность ниже порога — пока аналитик не подтвердит, публикация не даёт баллов объекту">привязка {conf}% — гипотеза</span> : undefined} />
            {c && !fact && (
              <div style={{ padding: '4px 24px 20px' }}>
                <button className="btn g m full" disabled={linking} onClick={() => link(c)}><IcCheck size={20} />{linking ? 'Сохраняем…' : 'Подтвердить привязку'}</button>
                <p className="t-xs c2" style={{ marginTop: 8, lineHeight: '17px' }}>Привязка найдена автоматически. Пока она гипотеза, публикация не даёт объекту баллов R2/R3, а R1 объекта не входит в её оценку.</p>
              </div>
            )}
          </div>

          <div className="card card-pad">
            <div className="row between"><span className="bold">Оценка публикации</span><ZoneTag zone={s.zone} /></div>
            <div className="row" style={{ alignItems: 'flex-end', gap: 6, margin: '12px 0 16px' }}><span className="big-num" style={{ fontSize: 52 }}>{s.score}</span><span className="c3" style={{ paddingBottom: 4 }}>из 100</span></div>
            <Gauge value={s.score} />
            {s.needsCheck && <div style={{ marginTop: 14 }}><NeedsCheck /></div>}
            <div style={{ marginTop: 8 }}><FactorList factors={s.factors} items={false} /></div>
            {c && cr && (
              <Link to={`/complexes/${c.id}`} className="row" style={{ marginTop: 18, background: 'var(--bg)', borderRadius: 16, padding: 8, gap: 12 }}>
                <img src={c.cover} alt="" style={{ width: 52, height: 52, borderRadius: 12, objectFit: 'cover' }} />
                <div style={{ flex: 1, minWidth: 0 }}><div className="bold t-sm">ЖК «{c.name}»</div><div className="t-xs c2">риск объекта · привязка {conf}%{fact ? '' : ', гипотеза'}</div></div>
                <Score value={cr.score} zone={cr.zone} />
              </Link>
            )}
          </div>

          <div className="card">
            <div className="stack">
              {(a.status === 'new' || a.status === 'needs_check') && <button className="btn y full" onClick={() => decide('in_review', 'Публикация взята в работу')}>Взять в работу</button>}
              <button className="btn d full" onClick={() => setModal('confirmed')}>Подтвердить нарушение</button>
              <div className="row" style={{ gap: 8 }}>
                <button className="btn g m" style={{ flex: 1 }} onClick={() => setModal('dismissed')}>Отклонить</button>
                <button className="btn g m" style={{ flex: 1 }} onClick={() => setModal('needs_check')}>Доп. проверка</button>
              </div>
              {c ? <SignalAction complex={c} label="Сформировать сигнал по ЖК" compact />
                : <p className="t-sm c2" style={{ lineHeight: '18px' }}><button className="link" disabled={linking} onClick={() => setModal('cx')}>Привяжите публикацию к ЖК</button> — привязка сохраняется в журнале, после этого можно сформировать сигнал</p>}
            </div>
            <p className="t-xs c2" style={{ marginTop: 12, lineHeight: '17px' }}>ИИ даёт рекомендацию. Решение принимает сотрудник, автоматической отправки в госорганы нет.</p>
          </div>
        </aside>
      </div>

      {modal === 'status' && (
        <Modal title="Статус публикации" onClose={() => setModal(null)}>
          {ORDER.map((st) => <Cell key={st} icon={<StatusDot s={st} />} title={statusMeta[st].name} end={a.status === st ? <IcCheck size={22} strokeWidth={2.5} style={{ color: 'var(--text)' }} /> : undefined}
            onClick={() => (isDecision(st) ? setModal(st) : st === a.status ? setModal(null) : decide(st, `Статус: ${statusMeta[st].name}`))} />)}
        </Modal>
      )}
      {modal === 'cx' && (
        <Modal title="Привязать к объекту" onClose={() => setModal(null)}>
          <div style={{ padding: '8px 16px' }}><div className="search"><IcSearch size={20} /><input autoFocus placeholder="Название ЖК" value={cq} onChange={(e) => setCq(e.target.value)} /></div></div>
          {a.complexId && <Cell icon={<span className="ico"><IcClose size={20} /></span>} title="Снять привязку" sub="Публикация останется без ЖК — нужна ручная привязка" onClick={() => link(null)} />}
          {complexes.filter((x) => x.name.toLowerCase().includes(cq.toLowerCase())).map((x) => (
            <Cell key={x.id} inset icon={<img className="th" src={x.cover} alt="" style={{ width: 44, height: 44, borderRadius: 12 }} />} title={x.name}
              sub={x.id === a.complexId ? (fact ? 'привязка подтверждена' : `привязка ${conf}% — гипотеза, выбор подтвердит её`) : x.address}
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
    </>
  )
}
