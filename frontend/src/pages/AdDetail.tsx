import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Cell, Gauge, Highlight, Modal, PageTitle, PlatformIcon, Score, StatusDot, ZoneTag, adKey, fmtDT, platformMeta, statusMeta } from '../components/ui'
import { audit } from '../data/demo'
import type { AdStatus } from '../data/types'
import { scoreAd, scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcBuilding, IcCheck, IcDoc, IcExternal, IcSearch, IcSend, IcUsers } from '../ui/icons'

const ORDER: AdStatus[] = ['new', 'needs_check', 'in_review', 'confirmed', 'dismissed']
const ME = 'Нестерова А.'

/** Разбор публикации: слева сама реклама и история, справа — решение аналитика */
export default function AdDetail() {
  const { id } = useParams()
  const { ads, complexes, setAdStatus, notify } = useStore()
  const a = ads.find((x) => x.id === id)
  const [comment, setComment] = useState('')
  const [notes, setNotes] = useState<{ at: string; who: string; what: string }[]>([])
  const [linkId, setLinkId] = useState<string | undefined>(a?.complexId)
  const [modal, setModal] = useState<'status' | 'cx' | 'confirm' | null>(null)
  const [reason, setReason] = useState('')
  const [cq, setCq] = useState('')
  if (!a) return <div className="empty">Публикация не найдена</div>
  const c = complexes.find((x) => x.id === linkId)
  const s = scoreAd(a, c)
  const cr = c ? scoreComplex(c, ads) : null
  const now = () => fmtDT(new Date().toISOString())
  const events = [...(audit[a.id] ?? [
    { at: fmtDT(a.date), who: 'Система', what: a.source === 'manual' ? 'Публикация добавлена вручную' : 'Публикация собрана автоматически' },
    { at: fmtDT(a.date), who: 'Система', what: c ? `Связана с ЖК «${c.name}», уверенность ${(a.matchConfidence * 100).toFixed(0)}%` : 'ЖК не определён — нужна ручная привязка' },
    { at: fmtDT(a.date), who: 'Система', what: `Оценка публикации — ${s.score} из 100` },
  ]), ...notes]

  const decide = (st: AdStatus, msg: string, why?: string) => {
    setAdStatus(a.id, st, ME)
    setNotes((n) => [...n, { at: now(), who: ME, what: `Статус → «${statusMeta[st].name}»${why ? `. ${why}` : ''}` }])
    setModal(null); setReason('')
    notify(msg)
  }

  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Лента публикаций', '/ads'], [adKey(a.id)]]} title={c ? `Реклама ЖК «${c.name}»` : 'Реклама без привязки к ЖК'}
        sub={<span className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={22} />{platformMeta[a.platform].name} · {a.account} · {fmtDT(a.date)}</span>} />

      <div className="cols-r">
        <div className="stack" style={{ gap: 16 }}>
          <div className="card" style={{ display: 'grid', gridTemplateColumns: a.image ? '300px 1fr' : '1fr', gap: 24, padding: 8 }}>
            {a.image && <div className="ph" style={{ borderRadius: 18, aspectRatio: '1' }}><img src={a.image} alt="" /></div>}
            <div style={{ padding: '16px 20px 16px 8px', display: 'flex', flexDirection: 'column', gap: 16 }}>
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
            <div className="tiles">
              {([['Признаки продажи', s.signals.sale, ''], ['Сбор денег', s.signals.funds, 'fund'], ['Признаки законности', s.signals.safe, 'safe']] as const).map(([t, list, cls]) => (
                <div key={t} className="tile" style={{ background: 'var(--bg)', minHeight: 110 }}>
                  <span className="nm c2" style={{ fontSize: 13 }}>{t}</span>
                  <span className="vl num" style={{ fontSize: 28, margin: '2px 0 6px' }}>{list.length}</span>
                  <div className="row wrap-f" style={{ gap: 4 }}>{list.map((k) => <mark key={k} className={cls} style={{ fontSize: 13 }}>{k}</mark>)}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="card-head"><h3>История</h3></div>
            {events.map((e, i) => (
              <div className="cell" key={i} style={{ minHeight: 56 }}>
                {e.who === 'Система' ? <span className="ava" style={{ background: 'var(--bg)' }}>S</span> : <span className="ava">ВН</span>}
                <div className="main"><div className="t-sm"><b className="bold">{e.who}</b> · {e.what}</div><div className="sub t-xs">{e.at}</div></div>
              </div>
            ))}
            <div className="row" style={{ padding: '8px 16px 16px', gap: 8 }}>
              <input className="input" placeholder="Комментарий: с чем сверено, на чём основано решение" value={comment} onChange={(e) => setComment(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && comment) { setNotes((n) => [...n, { at: now(), who: ME, what: comment }]); setComment('') } }} />
              <button className="btn d m" disabled={!comment} aria-label="Отправить" onClick={() => { setNotes((n) => [...n, { at: now(), who: ME, what: comment }]); setComment('') }}><IcSend size={20} /></button>
            </div>
          </div>
        </div>

        <aside className="stack sticky">
          <div className="card">
            <Cell title="Статус" end={<><StatusDot s={a.status} /><span style={{ color: 'var(--text)' }}>{statusMeta[a.status].name}</span></>} chevron onClick={() => setModal('status')} />
            <Cell title="Ответственный" end={a.assignee ? <><span className="ava sm">ВН</span><span style={{ color: 'var(--text)' }}>{a.assignee}</span></> : 'не назначен'} />
            <Cell title="Объект" end={<span style={{ color: 'var(--text)', maxWidth: 170, overflow: 'hidden', textOverflow: 'ellipsis' }}>{c ? c.name : 'не определён'}</span>} chevron onClick={() => setModal('cx')} />
          </div>

          <div className="card card-pad">
            <div className="row between"><span className="bold">Оценка публикации</span><ZoneTag zone={s.zone} /></div>
            <div className="row" style={{ alignItems: 'flex-end', gap: 6, margin: '12px 0 16px' }}><span className="big-num" style={{ fontSize: 52 }}>{s.score}</span><span className="c3" style={{ paddingBottom: 4 }}>из 100</span></div>
            <Gauge value={s.score} />
            {c && cr && (
              <Link to={`/complexes/${c.id}`} className="row" style={{ marginTop: 18, background: 'var(--bg)', borderRadius: 16, padding: 8, gap: 12 }}>
                <img src={c.cover} alt="" style={{ width: 52, height: 52, borderRadius: 12, objectFit: 'cover' }} />
                <div style={{ flex: 1, minWidth: 0 }}><div className="bold t-sm">ЖК «{c.name}»</div><div className="t-xs c2">риск объекта · уверенность {(a.matchConfidence * 100).toFixed(0)}%</div></div>
                <Score value={cr.score} zone={cr.zone} />
              </Link>
            )}
          </div>

          <div className="card" style={{ padding: 16 }}>
            <div className="stack">
              {(a.status === 'new' || a.status === 'needs_check') && <button className="btn y full" onClick={() => decide('in_review', 'Публикация взята в работу')}>Взять в работу</button>}
              <button className="btn d full" onClick={() => setModal('confirm')}>Подтвердить нарушение</button>
              <div className="row" style={{ gap: 8 }}>
                <button className="btn g m" style={{ flex: 1 }} onClick={() => decide('dismissed', 'Публикация отклонена')}>Отклонить</button>
                <button className="btn g m" style={{ flex: 1 }} onClick={() => decide('needs_check', 'Отправлено на доп. проверку')}>Доп. проверка</button>
              </div>
              <button className="btn g m full" onClick={() => notify('Акт фиксации сформирован (PDF, демо)')}><IcDoc size={20} />Акт фиксации публикации</button>
            </div>
            <p className="t-xs c2" style={{ marginTop: 12, lineHeight: '17px' }}>ИИ даёт рекомендацию. Решение принимает сотрудник, автоматической отправки в госорганы нет.</p>
          </div>
        </aside>
      </div>

      {modal === 'status' && (
        <Modal title="Статус публикации" onClose={() => setModal(null)}>
          {ORDER.map((st) => <Cell key={st} icon={<StatusDot s={st} />} title={statusMeta[st].name} end={a.status === st ? <IcCheck size={22} strokeWidth={2.5} style={{ color: 'var(--text)' }} /> : undefined}
            onClick={() => (st === 'confirmed' ? setModal('confirm') : decide(st, `Статус: ${statusMeta[st].name}`))} />)}
        </Modal>
      )}
      {modal === 'cx' && (
        <Modal title="Привязать к объекту" onClose={() => setModal(null)}>
          <div style={{ padding: '8px 16px' }}><div className="search"><IcSearch size={20} /><input autoFocus placeholder="Название ЖК" value={cq} onChange={(e) => setCq(e.target.value)} /></div></div>
          {complexes.filter((x) => x.name.toLowerCase().includes(cq.toLowerCase())).map((x) => (
            <Cell key={x.id} inset icon={<img className="th" src={x.cover} alt="" style={{ width: 44, height: 44, borderRadius: 12 }} />} title={x.name} sub={x.address}
              end={linkId === x.id ? <IcCheck size={22} strokeWidth={2.5} style={{ color: 'var(--text)' }} /> : undefined}
              onClick={() => { setLinkId(x.id); setModal(null); setNotes((n) => [...n, { at: now(), who: ME, what: `Привязала к ЖК «${x.name}»` }]); notify('Привязка изменена, оценка пересчитана') }} />
          ))}
        </Modal>
      )}
      {modal === 'confirm' && (
        <Modal title="Подтвердить нарушение?" onClose={() => setModal(null)}
          footer={<button className="btn y full" disabled={!reason.trim()} onClick={() => decide('confirmed', 'Нарушение подтверждено', reason)}>Подтвердить</button>}>
          <div className="cell" style={{ minHeight: 56 }}><span className="ico"><IcBuilding size={20} /></span><div className="main"><div className="ttl">{c ? `ЖК «${c.name}»` : 'ЖК не определён'}</div><div className="sub">{a.account}</div></div></div>
          <div className="cell" style={{ minHeight: 56 }}><span className="ico"><IcUsers size={20} /></span><div className="main"><div className="ttl">{ME}</div><div className="sub">решение фиксируется в журнале</div></div></div>
          <div className="field top" style={{ marginTop: 4 }}>
            <div className="body"><label>Основание решения</label><textarea autoFocus placeholder="Например: сверено с картотекой ГАСК, гарантии ЕО и разрешения МИО нет" value={reason} onChange={(e) => setReason(e.target.value)} /></div>
          </div>
        </Modal>
      )}
    </>
  )
}
