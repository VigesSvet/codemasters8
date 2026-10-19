// «Спутник против рекламы» — карточка над архивом снимков: что видно на снимке (гипотеза ИИ с датой съёмки) и что о
// стадии заявляет реклама; расхождение — подсказка сверить готовность (R5) или основание (R1), не вывод и не баллы.
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { Complex } from '../data/types'
import { platformName } from '../lib/platform'
import { CONF_RU, DISCREPANCY, getSatStage, type SatStageOut } from '../lib/satstage'

const day = (d?: string | null) => (d ? new Date(`${d.slice(0, 10)}T12:00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' }) : 'дата не указана')

export default function SatStage({ c, onShow }: { c: Complex; onShow?: (releaseId: number) => boolean }) {
  const [load, setLoad] = useState<{ id: string; d: SatStageOut | null } | null>(null)
  useEffect(() => {
    let alive = true
    getSatStage(c.id).then((d) => { if (alive) setLoad({ id: c.id, d }) }, () => { if (alive) setLoad({ id: c.id, d: null }) })
    return () => { alive = false }
  }, [c.id])
  const d = load?.id === c.id ? load.d : null
  if (!d) return null // офлайн-копия или сервер без функции — только архив снимков
  if (!d.images.length) return <div className="ai-note">Стадия стройки на снимке ещё не определялась (гипотезы ИИ — <code>python -m app.satstage</code>).</div>
  const img = d.images.find((x) => x.role === 'latest') ?? d.images[0]
  const start = d.images.find((x) => x !== img)
  const warn = d.hints.filter((h) => DISCREPANCY.includes(h.kind))
  const notes = d.hints.filter((h) => !DISCREPANCY.includes(h.kind))
  return (
    <div className="card">
      <div className="card-head"><h3>Спутник против рекламы</h3>{warn.length > 0 ? <span className="tag yellow">Расхождение — проверьте</span> : <span className="tag gray">гипотеза ИИ</span>}</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 20 }}>
        <div>
          <div className="t-xs c2" style={{ marginBottom: 4 }}>Что видно на снимке (гипотеза ИИ, дата съёмки {day(img.acquired ?? img.releaseDate)})</div>
          <div className="row wrap-f" style={{ gap: 8 }}>
            <span className="bold" style={{ fontSize: 17 }}>{img.stageLabel}</span>
            <span className="tag gray">уверенность {CONF_RU[img.confidence]}</span>
          </div>
          <p className="t-sm" style={{ marginTop: 6, lineHeight: '19px' }}>{img.reasoning}</p>
          {start && <p className="t-sm c2" style={{ marginTop: 4 }}>Снимок ближе к началу стройки ({day(start.acquired)}): {start.stageLabel}, уверенность {CONF_RU[start.confidence]}</p>}
          <div className="row wrap-f" style={{ gap: 12, marginTop: 6 }}>
            {onShow && <button className="link t-sm" onClick={() => onShow(img.releaseId)}>Показать этот снимок на ленте</button>}
            <span className="t-xs c3">{[img.sensor, img.res ? `${img.res} м/пкс` : null, img.servedModel ?? img.model].filter(Boolean).join(' · ')}</span>
          </div>
        </div>
        <div>
          <div className="t-xs c2" style={{ marginBottom: 4 }}>Что заявляет реклама (цитата, публикация)</div>
          {!d.claims.length ? <div className="t-sm c2">Публикаций ЖК с заявлениями о стадии нет.</div>
            : d.claims.slice(0, 4).map((x, i) => (
              <div key={`${x.adId}-${x.kind}-${i}`} style={{ padding: '4px 0', borderTop: i ? '1px solid var(--line)' : undefined }}>
                <div className="t-sm"><span className="tag gray" style={{ marginRight: 6 }}>{x.kindLabel}</span>«{x.quote}»</div>
                <div className="t-xs c2" style={{ marginTop: 2 }}><Link className="link" to={`/ads/${x.adId}`}>{platformName(x.platform)} · {x.account || 'аккаунт не указан'} · {day(x.date)}</Link></div>
              </div>
            ))}
          {d.claims.length > 4 && <div className="t-xs c3" style={{ marginTop: 4 }}>и ещё {d.claims.length - 4} — во вкладке «Реклама»</div>}
        </div>
      </div>
      {warn.map((h, i) => (
        <div key={`w${i}`} className="box warn t-sm" style={{ marginTop: 12, padding: '10px 14px', lineHeight: '19px' }}>
          {h.factor && <span className="tag white" style={{ marginRight: 8 }}>{h.factor} · сверить</span>}{h.text}
          {h.adId && <> · <Link className="link" to={`/ads/${h.adId}`}>публикация</Link></>}
        </div>
      ))}
      {notes.map((h, i) => <div key={`n${i}`} className="t-sm c2" style={{ marginTop: 8, lineHeight: '19px' }}>{h.text}</div>)}
      <div className="t-xs c3" style={{ marginTop: 10 }}>{d.disclaimer}</div>
    </div>
  )
}
