import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PageHeader, PlatformIcon, Score, StatusIcon, fmtShort, statusMeta } from '../components/ui'
import type { AdStatus } from '../data/types'
import { scoreAd } from '../lib/risk'
import { useStore } from '../lib/store'

const COLS: AdStatus[] = ['new', 'needs_check', 'in_review', 'confirmed', 'dismissed']

/** Очередь проверки — доска как в Linear: колонки по статусам, перетаскивание карточек */
export default function Queue() {
  const { ads, complexes, setAdStatus, notify } = useStore()
  const nav = useNavigate()
  const [drag, setDrag] = useState<string | null>(null)
  const [over, setOver] = useState<AdStatus | null>(null)
  const items = ads.map((a) => { const c = complexes.find((x) => x.id === a.complexId); return { a, c, s: scoreAd(a, c) } }).sort((x, y) => y.s.score - x.s.score)

  return (
    <>
      <PageHeader crumbs={[['SAQ', '/'], ['Очередь проверки']]} title="Очередь проверки"
        sub="Публикации отсортированы по риску. Перетащите карточку в колонку, чтобы сменить статус. Подтверждение — только после сверки с картотекой." />
      <div className="board">
        {COLS.map((st) => {
          const col = items.filter((x) => x.a.status === st)
          return (
            <div key={st} className="board-col" onDragOver={(e) => { e.preventDefault(); setOver(st) }} onDragLeave={() => setOver(null)}
              onDrop={() => { if (drag) { setAdStatus(drag, st, 'Нестерова А.'); notify(`Статус: ${statusMeta[st].name}`) } setDrag(null); setOver(null) }}
              style={{ background: over === st ? 'var(--cds-layer-hover-01)' : undefined }}>
              <div className="h"><StatusIcon s={st} />{statusMeta[st].name}<span className="c">{col.length}</span></div>
              {col.map(({ a, c, s }) => (
                <div key={a.id} className="bcard" draggable onDragStart={() => setDrag(a.id)} onClick={() => nav(`/ads/${a.id}`)} style={{ opacity: drag === a.id ? 0.4 : 1 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span className="mono muted">AD-{a.id.replace(/\D/g, '').padStart(3, '0')}</span>
                    {a.assignee ? <span className="avatar" title={a.assignee}>ВН</span> : <span className="avatar empty" />}
                  </div>
                  <div className="cell-title">{c ? `ЖК «${c.name}»` : 'ЖК не определён'}</div>
                  <div className="t">{a.text}</div>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <PlatformIcon p={a.platform} /><Score value={s.score} zone={s.zone} />
                    <span className="label-01" style={{ marginLeft: 'auto' }}>{fmtShort(a.date)}</span>
                  </div>
                </div>
              ))}
              {col.length === 0 && <div className="bcol-drop">Перетащите сюда</div>}
            </div>
          )
        })}
      </div>
    </>
  )
}
