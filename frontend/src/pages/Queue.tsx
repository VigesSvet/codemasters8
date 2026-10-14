import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PageTitle, PlatformIcon, Score, StatusDot, Seg, adKey, fmtShort, statusMeta } from '../components/ui'
import type { AdStatus } from '../data/types'
import { scoreAd } from '../lib/risk'
import { useStore } from '../lib/store'

const COLS: AdStatus[] = ['new', 'needs_check', 'in_review', 'confirmed', 'dismissed']

/** Очередь проверки: доска по статусам, карточки перетаскиваются между колонками */
export default function Queue() {
  const { ads, complexes, setAdStatus, notify } = useStore()
  const nav = useNavigate()
  const [drag, setDrag] = useState<string | null>(null)
  const [over, setOver] = useState<AdStatus | null>(null)
  const [who, setWho] = useState<'all' | 'me' | 'none'>('all')
  const items = ads.map((a) => { const c = complexes.find((x) => x.id === a.complexId); return { a, c, s: scoreAd(a, c) } })
    .filter(({ a }) => who === 'all' || (who === 'me' ? !!a.assignee : !a.assignee))
    .sort((x, y) => y.s.score - x.s.score)

  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Очередь проверки']]} title="Очередь проверки"
        sub="Сверху — самые рискованные. Перетащите карточку в колонку, чтобы сменить статус. Нарушение подтверждается в карточке публикации с указанием основания."
        actions={<Seg value={who} onChange={setWho} items={[['all', 'Все'], ['me', 'Мои'], ['none', 'Без исполнителя']]} />} />
      <div className="board">
        {COLS.map((st) => {
          const col = items.filter((x) => x.a.status === st)
          return (
            <div key={st} className={`bcol ${over === st ? 'over' : ''}`} onDragOver={(e) => { e.preventDefault(); setOver(st) }} onDragLeave={() => setOver(null)}
              onDrop={() => {
                if (drag && st === 'confirmed') { nav(`/ads/${drag}`); notify('Подтверждение — с указанием основания') }
                else if (drag) { setAdStatus(drag, st, 'Нестерова А.'); notify(`Статус: ${statusMeta[st].name}`) }
                setDrag(null); setOver(null)
              }}>
              <div className="h"><StatusDot s={st} />{statusMeta[st].name}<span className="c3 num" style={{ fontWeight: 400 }}>{col.length}</span></div>
              {col.map(({ a, c, s }) => (
                <div key={a.id} className="bcard" draggable onDragStart={() => setDrag(a.id)} onDragEnd={() => setDrag(null)} onClick={() => nav(`/ads/${a.id}`)} style={{ opacity: drag === a.id ? 0.4 : 1 }}>
                  <div className="row between">
                    <div className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={24} /><span className="t-xs c3 num">{adKey(a.id)}</span></div>
                    <Score value={s.score} zone={s.zone} />
                  </div>
                  <div className="bold">{c ? `ЖК «${c.name}»` : 'ЖК не определён'}</div>
                  <div className="t-sm c2 clamp2" style={{ lineHeight: '19px' }}>{a.text}</div>
                  <div className="row between">
                    <span className="t-xs c3">{fmtShort(a.date)}</span>
                    {a.assignee ? <span className="ava sm" title={a.assignee}>ВН</span> : <span className="ava sm ghost" title="Без исполнителя" />}
                  </div>
                </div>
              ))}
              {col.length === 0 && <div className="t-sm c3" style={{ padding: 24, textAlign: 'center' }}>Перетащите сюда</div>}
            </div>
          )
        })}
      </div>
    </>
  )
}
