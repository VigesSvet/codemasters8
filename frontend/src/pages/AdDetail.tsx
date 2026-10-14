import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Check, ExternalLink, FileDown, HelpCircle, Link2, UserCheck, X } from 'lucide-react'
import { Disclaimer, Gauge, Highlight, PlatformTag, Score, StatusTag, ZoneBadge, fmtDT } from '../components/ui'
import { audit, developers } from '../data/demo'
import { scoreAd, scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'

export default function AdDetail() {
  const { id } = useParams()
  const nav = useNavigate()
  const { ads, complexes, setAdStatus, notify } = useStore()
  const a = ads.find((x) => x.id === id)
  const [comment, setComment] = useState('')
  const [linkId, setLinkId] = useState(a?.complexId ?? '')
  if (!a) return <div className="empty">Публикация не найдена</div>
  const c = complexes.find((x) => x.id === linkId)
  const s = scoreAd(a, c)
  const cr = c ? scoreComplex(c, ads) : null
  const dev = c && developers.find((d) => d.id === c.developerId)
  const events = audit[a.id] ?? [
    { at: fmtDT(a.date), who: 'Система', what: a.source === 'manual' ? 'Добавлено вручную аналитиком' : 'Публикация собрана автоматически' },
    { at: fmtDT(a.date), who: 'Система', what: c ? `Связано с ЖК «${c.name}» (${(a.matchConfidence * 100).toFixed(0)}%)` : 'ЖК не определён — требуется ручная привязка' },
    { at: fmtDT(a.date), who: 'Система', what: `Скоринг публикации: ${s.score}` },
  ]

  const decide = (st: Parameters<typeof setAdStatus>[1], msg: string) => { setAdStatus(a.id, st, 'Нестерова А.'); notify(msg) }

  return (
    <div className="page fade-in">
      <button className="btn sm ghost" onClick={() => nav(-1)}><ArrowLeft size={13} />Назад</button>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 440px', gap: 48, marginTop: 24 }}>
        <div>
          <div className="row" style={{ marginBottom: 14 }}><PlatformTag p={a.platform} /><span className="mono">{a.account}</span><span className="label">{fmtDT(a.date)}</span><StatusTag s={a.status} /></div>
          <h1 className="display d-l" style={{ marginBottom: 28 }}>Публикация <span className="serif-i">№ {a.id.toUpperCase()}</span></h1>
          <div className="grid" style={{ gridTemplateColumns: a.image ? '1fr 1fr' : '1fr', gap: 0, border: '1px solid var(--line)' }}>
            {a.image && <div className="ph-frame" style={{ minHeight: 420 }}><img className="img" src={a.image} alt="" style={{ position: 'absolute', inset: 0 }} /></div>}
            <div style={{ padding: 28, background: 'var(--surface)' }} className="stack">
              <span className="label">Текст публикации</span>
              <div style={{ fontSize: 19, lineHeight: 1.5 }}><Highlight text={a.text} /></div>
              {a.ocr && <><hr className="rule" /><span className="label">Распознано на изображении (OCR)</span><div className="mono" style={{ fontSize: 13 }}><Highlight text={a.ocr} /></div></>}
              <hr className="rule" />
              <div className="row wrap">{a.price && <span className="chip">Цена: {a.price}</span>}{a.reach && <span className="chip">Охват ~{a.reach.toLocaleString('ru')}</span>}<a className="chip" href={a.url} target="_blank" rel="noreferrer"><ExternalLink size={11} />Источник</a></div>
            </div>
          </div>

          <div className="grid g-3" style={{ marginTop: 32 }}>
            <div><div className="label" style={{ marginBottom: 10 }}>Признаки продажи</div><div className="row wrap" style={{ gap: 6 }}>{s.signals.sale.length ? s.signals.sale.map((k) => <mark key={k}>{k}</mark>) : <span className="muted">—</span>}</div></div>
            <div><div className="label" style={{ marginBottom: 10 }}>Признаки сбора денег</div><div className="row wrap" style={{ gap: 6 }}>{s.signals.funds.length ? s.signals.funds.map((k) => <mark className="fund" key={k}>{k}</mark>) : <span className="muted">—</span>}</div></div>
            <div><div className="label" style={{ marginBottom: 10 }}>Признаки законности</div><div className="row wrap" style={{ gap: 6 }}>{s.signals.safe.length ? s.signals.safe.map((k) => <mark className="safe" key={k}>{k}</mark>) : <span className="muted">—</span>}</div></div>
          </div>

          <h3 className="display d-m" style={{ marginTop: 48, marginBottom: 16 }}>Журнал действий</h3>
          <div className="timeline">
            {events.map((e, i) => (
              <div className="tl" key={i}><div className="row"><span className="mono">{e.at}</span><span className="label">{e.who}</span></div><div className="ink2" style={{ marginTop: 3 }}>{e.what}</div></div>
            ))}
          </div>
        </div>

        <aside className="stack" style={{ gap: 16, position: 'sticky', top: 80, alignSelf: 'start' }}>
          <div className="card card-pad stack">
            <div className="row" style={{ justifyContent: 'space-between' }}><span className="label">Оценка публикации</span><ZoneBadge zone={s.zone} /></div>
            <div className="row" style={{ alignItems: 'end' }}><span className="num" style={{ fontSize: 88, lineHeight: 0.85 }}>{s.score}</span><span className="muted" style={{ paddingBottom: 8 }}>/ 100</span></div>
            <Gauge value={s.score} />
          </div>

          <div className="card card-pad stack">
            <div className="row" style={{ justifyContent: 'space-between' }}><span className="label"><Link2 size={11} /> Связанный ЖК</span>{c && <span className="label">{(a.matchConfidence * 100).toFixed(0)}%</span>}</div>
            <select className="select" value={linkId} onChange={(e) => { setLinkId(e.target.value); notify('Привязка обновлена — скоринг пересчитан') }}>
              <option value="">— не определён —</option>
              {complexes.map((x) => <option key={x.id} value={x.id}>{x.name} · {x.district}</option>)}
            </select>
            {c && cr && (
              <Link to={`/complexes/${c.id}`} className="row" style={{ gap: 12, alignItems: 'center' }}>
                <img src={c.cover} alt="" style={{ width: 64, height: 48, objectFit: 'cover', filter: 'var(--img-filter)' }} />
                <div style={{ flex: 1 }}><div style={{ fontWeight: 500 }}>{c.name}</div><div className="label" style={{ fontSize: 10 }}>{dev?.name} · {c.stage}</div></div>
                <Score value={cr.score} zone={cr.zone} solid />
              </Link>
            )}
            {cr && cr.factors.slice(0, 3).map((f) => (
              <div key={f.code} className={`factor ${f.state}`} style={{ padding: '8px 0' }}><span className="code">{f.code}</span><div style={{ fontSize: 13 }}>{f.title}<div className="muted" style={{ fontSize: 11 }}>{f.evidence}</div></div><span className="pts">+{f.points}</span></div>
            ))}
          </div>

          <div className="card card-pad stack">
            <span className="label">Решение аналитика</span>
            <textarea className="textarea" style={{ minHeight: 80 }} placeholder="Комментарий: на чём основано решение, с чем сверено…" value={comment} onChange={(e) => setComment(e.target.value)} />
            <div className="grid g-2" style={{ gap: 8 }}>
              <button className="btn danger" onClick={() => decide('confirmed', 'Нарушение подтверждено, кейс передан во внутреннюю работу')}><Check size={14} />Подтвердить</button>
              <button className="btn ok" onClick={() => decide('dismissed', 'Публикация отклонена как не нарушение')}><X size={14} />Отклонить</button>
              <button className="btn ghost" onClick={() => decide('needs_check', 'Отмечено: нужна дополнительная проверка')}><HelpCircle size={14} />Доп. проверка</button>
              <button className="btn ghost" onClick={() => decide('in_review', 'Взято в работу')}><UserCheck size={14} />Взять в работу</button>
            </div>
            <button className="btn ghost" style={{ justifyContent: 'center' }} onClick={() => notify('Акт фиксации сформирован (PDF, демо)')}><FileDown size={14} />Сформировать акт фиксации</button>
            <Disclaimer />
          </div>
        </aside>
      </div>
    </div>
  )
}
