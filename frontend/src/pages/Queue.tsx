import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Columns3, Rows3 } from 'lucide-react'
import { PageHead, PlatformTag, Score, StatusTag, ZoneBadge, fmtDT, statusMeta } from '../components/ui'
import type { AdStatus } from '../data/types'
import { scoreAd } from '../lib/risk'
import { useStore } from '../lib/store'

const COLS: AdStatus[] = ['new', 'needs_check', 'in_review', 'confirmed', 'dismissed']

export default function Queue() {
  const { ads, complexes, setAdStatus } = useStore()
  const nav = useNavigate()
  const [view, setView] = useState<'kanban' | 'list'>('kanban')
  const [drag, setDrag] = useState<string | null>(null)
  const items = ads.map((a) => { const c = complexes.find((x) => x.id === a.complexId); return { a, c, s: scoreAd(a, c) } }).sort((x, y) => y.s.score - x.s.score)

  return (
    <div className="page">
      <PageHead idx="04" label="Рабочее место аналитика" title={<>Очередь <span className="serif-i">проверки</span></>}
        aside={<div className="seg"><button className={view === 'kanban' ? 'on' : ''} onClick={() => setView('kanban')}><Columns3 size={13} />Канбан</button><button className={view === 'list' ? 'on' : ''} onClick={() => setView('list')}><Rows3 size={13} />Список</button></div>}>
        <p className="ink2" style={{ maxWidth: 640, marginTop: 18 }}>Публикации отсортированы по риск-скору. Перетащите карточку в колонку, чтобы сменить статус. Подтверждение нарушения — только после сверки с картотекой.</p>
      </PageHead>

      {view === 'kanban' ? (
        <div className="kanban fade-in">
          {COLS.map((st) => {
            const col = items.filter((x) => x.a.status === st)
            return (
              <div key={st} className="col" onDragOver={(e) => e.preventDefault()} onDrop={() => { if (drag) setAdStatus(drag, st, 'Нестерова А.'); setDrag(null) }}>
                <div className="col-h"><span className="mono">{statusMeta[st].name}</span><span className="num" style={{ fontSize: 22 }}>{col.length}</span></div>
                {col.map(({ a, c, s }) => (
                  <div key={a.id} className="kcard" draggable onDragStart={() => setDrag(a.id)} onClick={() => nav(`/ads/${a.id}`)} style={{ opacity: drag === a.id ? 0.4 : 1 }}>
                    <div className="row" style={{ justifyContent: 'space-between' }}><PlatformTag p={a.platform} /><Score value={s.score} zone={s.zone} solid /></div>
                    <div style={{ fontWeight: 500 }}>{c?.name ?? 'ЖК не определён'}</div>
                    <div className="muted" style={{ fontSize: 12, display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{a.text}</div>
                    <div className="row" style={{ justifyContent: 'space-between' }}><span className="label" style={{ fontSize: 10 }}>{fmtDT(a.date)}</span>{a.assignee ? <span className="avatar" style={{ width: 22, height: 22, fontSize: 9 }} title={a.assignee}>ВН</span> : <span className="label" style={{ fontSize: 10 }}>не назначено</span>}</div>
                  </div>
                ))}
                {col.length === 0 && <div className="muted" style={{ border: '1px dashed var(--line)', padding: 20, textAlign: 'center', fontSize: 12 }}>Перетащите сюда</div>}
              </div>
            )
          })}
        </div>
      ) : (
        <table className="t fade-in" style={{ borderTop: '1px solid var(--line-strong)' }}>
          <thead><tr><th>Риск</th><th>Площадка</th><th>Публикация</th><th>ЖК</th><th>Дата</th><th>Ответственный</th><th>Статус</th></tr></thead>
          <tbody>
            {items.map(({ a, c, s }) => (
              <tr key={a.id} onClick={() => nav(`/ads/${a.id}`)}>
                <td><div className="row"><Score value={s.score} zone={s.zone} solid /><ZoneBadge zone={s.zone} /></div></td>
                <td><PlatformTag p={a.platform} /><div className="label" style={{ fontSize: 10 }}>{a.account}</div></td>
                <td style={{ maxWidth: 420 }}><div style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.text}</div></td>
                <td>{c ? <Link to={`/complexes/${c.id}`} onClick={(e) => e.stopPropagation()} style={{ fontWeight: 500 }}>{c.name}</Link> : <span className="muted">—</span>}</td>
                <td className="mono">{fmtDT(a.date)}</td>
                <td>{a.assignee ?? <span className="muted">—</span>}</td>
                <td><StatusTag s={a.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
