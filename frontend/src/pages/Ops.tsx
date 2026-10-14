import { useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import MapView from '../components/MapView'
import { Cell, PageTitle, PlatformIcon, Score, fmtShort, platformMeta } from '../components/ui'
import type { Platform, Zone } from '../data/types'
import { scoreAd, scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcChevronRight } from '../ui/icons'

const WEEKS = [3, 5, 4, 7, 6, 9, 8, 12]

export default function Ops() {
  const { complexes, ads } = useStore()
  const nav = useNavigate()
  const scored = useMemo(() => complexes.map((c) => { const r = scoreComplex(c, ads); return { c, r, zone: r.zone, score: r.score } }).sort((a, b) => b.score - a.score), [complexes, ads])
  const count = (z: Zone) => scored.filter((x) => x.zone === z).length
  const queue = ads.filter((a) => a.status === 'new' || a.status === 'needs_check').length
  const byPlat = (Object.keys(platformMeta) as Platform[]).map((p) => ({ p, n: ads.filter((a) => a.platform === p).length })).filter((x) => x.n).sort((a, b) => b.n - a.n)
  const maxP = Math.max(...byPlat.map((x) => x.n))
  const idle = scored.filter((x) => x.c.idleDays >= 30).sort((a, b) => b.c.idleDays - a.c.idleDays)
  const stats: [string, number, string, string | undefined, string][] = [
    ['На контроле', scored.length, `${scored.filter((x) => x.c.stage !== 'введён').length} строятся`, undefined, '/complexes'],
    ['Красная зона', count('red'), 'R1 и R2 подтверждены', 'var(--red)', '/complexes?zone=red'],
    ['Жёлтая зона', count('yellow'), 'рекомендуется проверка', 'var(--amber)', '/complexes?zone=yellow'],
    ['Зелёная зона', count('green'), 'основания подтверждены', 'var(--green)', '/complexes?zone=green'],
    ['Ждут решения', queue, `из ${ads.length} публикаций`, undefined, '/queue'],
  ]

  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Оперативная сводка']]} title="Оперативная сводка" sub={`Усть-Каменогорск · на ${new Date().toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })}`} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 16, marginBottom: 16 }}>
        {stats.map(([l, v, s, col, to], i) => (
          <Link key={l} to={to} className="card stat" style={i === 4 ? { background: 'var(--yellow)' } : undefined}>
            <div className="row between"><span className="t-sm" style={{ color: i === 4 ? 'var(--text)' : 'var(--text-2)' }}>{l}</span><IcChevronRight size={18} /></div>
            <span className="v" style={{ color: col }}>{v}</span>
            <span className="t-xs" style={{ color: i === 4 ? 'var(--text)' : 'var(--text-2)' }}>{s}</span>
          </Link>
        ))}
      </div>
      <div className="ops">
        <div className="card" style={{ gridColumn: 'span 8', height: 460, position: 'relative', overflow: 'hidden' }}>
          <MapView items={scored} onSelect={(id) => nav(`/complexes/${id}`)} labels zoom={11.6} controls />
        </div>
        <div className="card" style={{ gridColumn: 'span 4', height: 460, overflow: 'auto' }}>
          <div className="card-head"><h3>Риск по объектам</h3></div>
          {scored.map(({ c, r }) => <Cell key={c.id} to={`/complexes/${c.id}`} title={c.name} sub={`${c.district} · ${c.stage}`} end={<Score value={r.score} zone={r.zone} />} />)}
        </div>
        <div className="card card-pad" style={{ gridColumn: 'span 5' }}>
          <h3 className="t-h3" style={{ marginBottom: 20 }}>Реклама с признаками продажи</h3>
          <div className="bars">{WEEKS.map((v, i) => <div key={i}><span className="num">{v}</span><b style={{ height: v * 11 }} /><span className="t-xs c3">н{34 + i}</span></div>)}</div>
        </div>
        <div className="card card-pad" style={{ gridColumn: 'span 4' }}>
          <h3 className="t-h3" style={{ marginBottom: 16 }}>По площадкам</h3>
          {byPlat.map(({ p, n }) => (
            <div key={p} style={{ display: 'grid', gridTemplateColumns: '28px 90px 1fr 24px', gap: 10, alignItems: 'center', padding: '6px 0' }}>
              <PlatformIcon p={p} size={24} /><span className="t-sm">{platformMeta[p].name}</span>
              <div className="hbar"><i style={{ width: `${(n / maxP) * 100}%`, background: 'var(--text)' }} /></div><span className="t-sm num" style={{ textAlign: 'right' }}>{n}</span>
            </div>
          ))}
        </div>
        <div className="card" style={{ gridColumn: 'span 3' }}>
          <div className="card-head"><h3>Стройка стоит</h3></div>
          {idle.map(({ c }) => <Cell key={c.id} to={`/complexes/${c.id}?tab=photos`} title={c.name} end={<span className={`tag ${c.idleDays >= 90 ? 'red' : 'yellow'}`}>{c.idleDays} дн.</span>} />)}
        </div>
        <div className="card" style={{ gridColumn: 'span 12' }}>
          <div className="card-head"><h3>Последние публикации</h3><Link to="/ads" className="t-sm c2 row" style={{ gap: 4 }}>Вся лента<IcChevronRight size={16} /></Link></div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)' }}>
            {ads.slice(0, 8).map((a) => {
              const c = complexes.find((x) => x.id === a.complexId); const s = scoreAd(a, c)
              return <Cell key={a.id} to={`/ads/${a.id}`} icon={<PlatformIcon p={a.platform} />} title={c?.name ?? 'ЖК не определён'} sub={`${a.account} · ${fmtShort(a.date)}`} end={<Score value={s.score} zone={s.zone} />} />
            })}
          </div>
        </div>
      </div>
    </>
  )
}
