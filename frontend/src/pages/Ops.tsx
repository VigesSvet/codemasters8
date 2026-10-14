import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { Theme } from '@carbon/react'
import MapView from '../components/MapView'
import { PlatformIcon, Score, ZoneDot, fmtShort, platformMeta } from '../components/ui'
import type { Platform, Zone } from '../data/types'
import { scoreAd, scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'

/** Оперативная сводка — компоновка как в ArcGIS Dashboards: индикаторы, список, карта, графики */
export default function Ops() {
  const { complexes, ads } = useStore()
  const nav = useNavigate()
  const scored = useMemo(() => complexes.map((c) => { const r = scoreComplex(c, ads); return { c, r, zone: r.zone, score: r.score } }).sort((a, b) => b.score - a.score), [complexes, ads])
  const count = (z: Zone) => scored.filter((x) => x.zone === z).length
  const queue = ads.filter((a) => a.status === 'new' || a.status === 'needs_check').length
  const byPlat = (Object.keys(platformMeta) as Platform[]).map((p) => ({ p, n: ads.filter((a) => a.platform === p).length })).filter((x) => x.n).sort((a, b) => b.n - a.n)
  const maxP = Math.max(...byPlat.map((x) => x.n))
  const idle = scored.filter((x) => x.c.idleDays >= 30).sort((a, b) => b.c.idleDays - a.c.idleDays)

  return (
    <Theme theme="g100">
      <div className="ops">
        <div className="kpis">
          {[
            ['Объектов на контроле', scored.length, `${scored.filter((x) => x.c.stage !== 'введён').length} строятся`, undefined],
            ['Красная зона', count('red'), 'R1 + R2 подтверждены', 'var(--zone-red)'],
            ['Жёлтая зона', count('yellow'), 'рекомендуется проверка', 'var(--zone-yellow)'],
            ['Зелёная зона', count('green'), 'основания подтверждены', 'var(--zone-green)'],
            ['Ждут решения', queue, `из ${ads.length} публикаций`, '#4589ff'],
          ].map(([l, v, s, color]) => (
            <div key={String(l)} className="ind">
              <h4 style={{ margin: 0 }}>{l}</h4>
              <div className="v" style={{ color: color as string | undefined }}>{v}</div>
              <div className="s">{s}</div>
            </div>
          ))}
        </div>

        <section style={{ gridRow: '2 / 4', overflow: 'auto', padding: 0 }}>
          <h4 style={{ padding: '12px 16px 0' }}>Объекты по риску</h4>
          {scored.map(({ c, r }) => (
            <button key={c.id} className="obj-row" style={{ gridTemplateColumns: '1fr auto', background: 'none' }} onClick={() => nav(`/complexes/${c.id}`)}>
              <div>
                <div className="t" style={{ display: 'flex', gap: 8, alignItems: 'center' }}><ZoneDot zone={r.zone} />{c.name}</div>
                <div className="a">{c.district} · {c.stage}</div>
              </div>
              <Score value={r.score} zone={r.zone} />
            </button>
          ))}
        </section>

        <section style={{ padding: 0 }}>
          <MapView items={scored} onSelect={(id) => nav(`/complexes/${id}`)} labels zoom={11.4} />
        </section>

        <section style={{ overflow: 'auto' }}>
          <h4>Последние публикации</h4>
          {ads.slice(0, 8).map((a) => {
            const c = complexes.find((x) => x.id === a.complexId)
            const s = scoreAd(a, c)
            return (
              <button key={a.id} onClick={() => nav(`/ads/${a.id}`)} style={{ display: 'grid', gridTemplateColumns: '20px 1fr auto', gap: 10, alignItems: 'center', width: '100%', padding: '8px 0', background: 'none', border: 0, borderBottom: '1px solid var(--cds-border-subtle-01)', color: 'inherit', textAlign: 'left', cursor: 'pointer' }}>
                <PlatformIcon p={a.platform} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c?.name ?? 'ЖК не определён'}</div>
                  <div className="label-01">{a.account} · {fmtShort(a.date)}</div>
                </div>
                <Score value={s.score} zone={s.zone} />
              </button>
            )
          })}
        </section>

        <section>
          <h4>Публикации по площадкам</h4>
          {byPlat.map(({ p, n }) => (
            <div key={p} style={{ display: 'grid', gridTemplateColumns: '90px 1fr 24px', gap: 10, alignItems: 'center', padding: '6px 0', fontSize: 12 }}>
              <span>{platformMeta[p].name}</span>
              <div style={{ height: 14, background: 'var(--cds-layer-02)' }}><div style={{ width: `${(n / maxP) * 100}%`, height: '100%', background: '#4589ff' }} /></div>
              <span className="mono" style={{ textAlign: 'right' }}>{n}</span>
            </div>
          ))}
        </section>

        <section>
          <h4>Простой площадок (по снимкам)</h4>
          {idle.map(({ c }) => (
            <div key={c.id} style={{ display: 'grid', gridTemplateColumns: '1fr auto', padding: '6px 0', fontSize: 13, borderBottom: '1px solid var(--cds-border-subtle-01)' }}>
              <span>{c.name}</span>
              <span className="mono" style={{ color: c.idleDays >= 90 ? 'var(--zone-red)' : 'var(--zone-yellow)' }}>{c.idleDays} дн.</span>
            </div>
          ))}
          <div className="label-01" style={{ marginTop: 12 }}>Источник: облёты дрона / камеры, демо-данные</div>
        </section>
      </div>
    </Theme>
  )
}
