import { useState, type CSSProperties } from 'react'
import type { SitePhoto } from '../data/types'
import { mediaSrc } from '../lib/api'
import { fmtDate } from './ui'

/** Сравнение двух снимков площадки «до / после» с ползунком */
export default function Compare({ before, after }: { before: SitePhoto; after: SitePhoto }) {
  const [x, setX] = useState(50)
  return (
    <div className="compare" style={{ '--x': `${x}%` } as CSSProperties}>
      {/* снимок аналитика — файл сервера /api/media/…: адрес через mediaSrc (учитывает VITE_API_URL) */}
      <img src={mediaSrc(before.src) ?? before.src} alt="" />
      <img src={mediaSrc(after.src) ?? after.src} alt="" className="after" />
      <div className="handle" />
      <span className="lbl tag dark" style={{ left: 14 }}>{fmtDate(before.date)} · {before.source}</span>
      <span className="lbl tag dark" style={{ right: 14 }}>{fmtDate(after.date)} · {after.source}</span>
      <input type="range" min={0} max={100} value={x} onChange={(e) => setX(+e.target.value)} aria-label="Сравнение снимков" />
    </div>
  )
}
