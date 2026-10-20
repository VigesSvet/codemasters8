import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { AuditEvent } from '../data/types'
import { SYSTEM, fmtStamp, initialsOf, kindOf } from '../lib/audit'
import { plain } from '../lib/plain'
import { from as fromState, type From } from '../lib/nav'
import { useStore } from '../lib/store'
import { adKey } from './ui'

/** На что ставить ссылки у события: публикация, сигнал, ЖК */
export type AuditLink = 'ad' | 'signal' | 'complex'

/** Событие журнала: автор (инициалы, «S» — система), что сделано, когда, вид события, ссылки.
 *  from — откуда пришли: ссылка на публикацию передаёт его, и разбор показывает «← …» */
export function AuditItem({ e, links = ['ad', 'signal'], from }: { e: AuditEvent; links?: AuditLink[]; from?: From }) {
  const { analysts, complexes } = useStore()
  const k = kindOf(e)
  const cx = links.includes('complex') && e.complexId ? complexes.find((c) => c.id === e.complexId) : undefined
  return (
    <div className="aitem">
      <span className={`ava ${e.who === SYSTEM ? 'sys' : ''}`} title={e.who}>{initialsOf(e.who, analysts)}</span>
      <div className="main">
        <div className="t-sm what"><b className="bold">{e.who}</b> · {e.who === SYSTEM ? plain(e.what) : e.what}</div>
        <div className="meta">
          <span className="t-xs c2 num">{fmtStamp(e.at)}</span>
          <span className={`tag ${k.tag}`}><k.Icon size={12} />{k.name}</span>
          {links.includes('ad') && e.adId && <Link to={`/ads/${e.adId}`} state={from && fromState(from.label, from.to)} className="tag outline" title="Открыть публикацию">{adKey(e.adId)}</Link>}
          {links.includes('signal') && e.signalId != null && <Link to={`/signals/${e.signalId}`} className="tag outline" title="Открыть сигнал">S-{String(e.signalId).padStart(4, '0')}</Link>}
          {cx && <Link to={`/complexes/${cx.id}`} className="tag outline" title="Открыть карточку ЖК">ЖК «{cx.name}»</Link>}
        </div>
      </div>
    </div>
  )
}

/** Список событий в заданном порядке; пустой — подпись */
export function AuditList({ events, links, empty, from }: { events: AuditEvent[]; links?: AuditLink[]; empty?: ReactNode; from?: From }) {
  if (!events.length) return <div className="t-sm c2 alist-empty">{empty ?? 'Событий пока нет'}</div>
  return <div className="alist">{events.map((e, i) => <AuditItem key={`${e.id ?? 'x'}-${i}`} e={e} links={links} from={from} />)}</div>
}
