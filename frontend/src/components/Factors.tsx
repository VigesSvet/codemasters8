import { Link } from 'react-router-dom'
import type { EvidenceItem, FactorState, Observation, ObservationState, Platform, RiskFactor, Tri } from '../data/types'
import { MARKERS } from '../lib/risk'
import { IcCamera, IcClock, IcDoc, IcUsers, IcWorker } from '../ui/icons'
import { PlatformIcon, TriTag, fmtDT, fmtDate, platformMeta } from './ui'

const stateMeta: Record<FactorState, { name: string; tag: string }> = {
  hit: { name: 'Подтверждён', tag: 'red' },
  unknown: { name: 'Нужна проверка', tag: 'yellow' },
  clear: { name: 'Не выявлен', tag: 'green' },
}
export const StateTag = ({ state }: { state: FactorState }) => <span className={`tag ${stateMeta[state].tag}`}>{stateMeta[state].name}</span>

const obsMeta: Record<ObservationState, { name: string; tag: string }> = {
  flag: { name: 'Есть замечание', tag: 'yellow' },
  unknown: { name: 'Нет сведений', tag: 'gray' },
  ok: { name: 'Без замечаний', tag: 'green' },
}

const appealMeta: Record<string, { name: string; tag: string }> = {
  verified: { name: 'проверено', tag: 'red' },
  pending: { name: 'на проверке', tag: 'yellow' },
  rejected: { name: 'отклонено', tag: 'gray' },
}

const MONEY = new Set(MARKERS.money.map((m) => m.toLowerCase()))

/** Цитата-основание в «ёлочках» с подсвеченным маркером словаря: предложение денег (R2) — красным, реклама продажи — обычной подсветкой */
export function Quote({ text, marker }: { text: string; marker?: string }) {
  if (!marker) return <>«{text}»</>
  const i = text.toLowerCase().indexOf(marker.toLowerCase())
  if (i < 0) return <>«{text}»</>
  const end = text.slice(i).search(/[^\p{L}\d]/u)
  const j = end < 0 ? text.length : i + Math.max(end, marker.length)
  return <>«{text.slice(0, i)}<mark className={MONEY.has(marker.toLowerCase()) ? 'fund' : undefined}>{text.slice(i, j)}</mark>{text.slice(j)}»</>
}

const when = (d?: string) => (d ? (d.length > 10 ? fmtDT(d) : fmtDate(d)) : undefined)
const meta = (...xs: (string | undefined)[]) => xs.filter(Boolean).join(' · ')

/** Одно доказательство: публикация, документ, обращение, значение готовности, снимок, срок */
export function EvidenceView({ item: x, linkAds = true }: { item: EvidenceItem; linkAds?: boolean }) {
  if (x.kind === 'ad') {
    const p = x.source && x.source in platformMeta ? (x.source as Platform) : undefined
    const body = (
      <>
        {p ? <PlatformIcon p={p} size={20} /> : <IcDoc size={20} />}
        <div>
          <div className="meta">{meta(x.label, p && platformMeta[p].name, when(x.date))}</div>
          {x.quote && <div className="q"><Quote text={x.quote} marker={x.marker} /></div>}
        </div>
      </>
    )
    return linkAds && x.ref ? <Link to={`/ads/${x.ref}`} className="ev">{body}</Link> : <div className="ev">{body}</div>
  }
  if (x.kind === 'document') return (
    <div className="ev">
      <IcDoc size={20} />
      <div>
        <div className="row" style={{ gap: 8 }}><span className="bold">{x.label}</span>{typeof x.value === 'string' && <TriTag v={x.value as Tri} />}</div>
        {x.quote && <div className="q">{x.quote}</div>}
        {(x.source || x.date) && <div className="meta">{meta(x.source, x.date && `сверено ${fmtDate(x.date)}`)}</div>}
      </div>
    </div>
  )
  if (x.kind === 'appeal') {
    const st = appealMeta[String(x.value)] ?? appealMeta.pending
    return (
      <div className="ev">
        <IcUsers size={20} />
        <div>
          <div className="row" style={{ gap: 8, alignItems: 'flex-start' }}><span style={{ flex: 1 }}>{x.label}</span><span className={`tag ${st.tag}`}>{st.name}</span></div>
          <div className="meta">{meta(x.source, when(x.date))}</div>
        </div>
      </div>
    )
  }
  if (x.kind === 'readiness') return (
    <div className="ev">
      <IcWorker size={20} />
      <div><span className="bold">{x.label}: {x.value}%</span><div className="meta">{meta(x.source, when(x.date))}</div></div>
    </div>
  )
  if (x.kind === 'photo') return (
    <div className="ev">
      {x.ref ? <img className="th" src={x.ref} alt="" /> : <IcCamera size={20} />}
      <div><span className="bold">{when(x.date)}</span>{x.label && <> · {x.label}</>}<div className="meta">{meta(x.source, x.value !== undefined ? `активность: ${x.value}` : undefined)}</div></div>
    </div>
  )
  return (
    <div className="ev">
      <IcClock size={20} />
      <div><span className="bold">{x.label}</span>{x.date && <> · {fmtDate(x.date)}</>}</div>
    </div>
  )
}

/** Факторы R1–R6: статус, пояснение, баллы и доказательства под каждым */
export function FactorList({ factors, items = true, linkAds = true }: { factors: RiskFactor[]; items?: boolean; linkAds?: boolean }) {
  return (
    <div>
      {factors.map((f) => (
        <div key={f.code} className={`factor ${f.state}`}>
          <span className="code">{f.code}</span>
          <div style={{ minWidth: 0 }}>
            <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}><span className="t">{f.title}</span><StateTag state={f.state} /></div>
            <div className="e">{f.evidence}</div>
            {items && f.items.length > 0 && <div className="ev-list">{f.items.map((x, i) => <EvidenceView key={i} item={x} linkAds={linkAds} />)}</div>}
          </div>
          <span className="p">+{f.points}</span>
        </div>
      ))}
    </div>
  )
}

/** Наблюдения вне балла (перечень ГАСК, профиль застройщика, короткий простой) */
export function ObservationList({ items }: { items: Observation[] }) {
  return (
    <div>
      {items.map((o) => (
        <div key={o.code} className="obs">
          <span className="tag gray">{o.code}</span>
          <div><div className="t-sm bold">{o.title}</div><div className="t-xs c2" style={{ marginTop: 2 }}>{o.text}</div></div>
          <span className={`tag ${obsMeta[o.state].tag}`}>{obsMeta[o.state].name}</span>
        </div>
      ))}
    </div>
  )
}
