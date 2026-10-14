import { useCallback, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, ArrowUpRight, LayoutGrid, Map as MapIcon } from 'lucide-react'
import MapView from '../components/MapView'
import { Highlight, NeedsCheck, PlatformTag, Score, TriMark, ZoneBadge, fmtDT } from '../components/ui'
import { developers } from '../data/demo'
import type { Zone } from '../data/types'
import { scoreAd, scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'

export default function Dashboard() {
  const { complexes, ads } = useStore()
  const nav = useNavigate()
  const [variant, setVariant] = useState<'map' | 'editorial'>(() => (localStorage.getItem('saq-dash') as 'map') || 'map')
  const [zf, setZf] = useState<Zone | 'all'>('all')
  const [sel, setSel] = useState<string | undefined>()

  const scored = useMemo(() => complexes.map((c) => { const r = scoreComplex(c, ads); return { c, r, zone: r.zone, score: r.score } }).sort((a, b) => b.score - a.score), [complexes, ads])
  const visible = useMemo(() => scored.filter((x) => zf === 'all' || x.zone === zf), [scored, zf])
  const count = (z: Zone) => scored.filter((x) => x.zone === z).length
  const queue = ads.filter((a) => a.status === 'new' || a.status === 'needs_check').length
  const selected = scored.find((x) => x.c.id === sel)
  const onSelect = useCallback((id: string) => setSel(id), [])

  const switchVariant = (v: 'map' | 'editorial') => { setVariant(v); localStorage.setItem('saq-dash', v) }

  const kpis = (
    <div className="kpis">
      <div className="kpi"><span className="label">Объектов на контроле</span><span className="v num">{scored.length}</span><span className="label">{scored.filter((x) => x.c.stage !== 'введён').length} строятся</span></div>
      <div className="kpi z-red"><span className="label">Красная зона</span><span className="v num" style={{ color: 'var(--red)' }}>{count('red')}</span><div className="bar"><b style={{ width: `${(count('red') / scored.length) * 100}%` }} /></div></div>
      <div className="kpi z-yellow"><span className="label">Жёлтая зона</span><span className="v num" style={{ color: 'var(--yellow)' }}>{count('yellow')}</span><div className="bar"><b style={{ width: `${(count('yellow') / scored.length) * 100}%` }} /></div></div>
      <div className="kpi z-green"><span className="label">Зелёная зона</span><span className="v num" style={{ color: 'var(--green)' }}>{count('green')}</span><div className="bar"><b style={{ width: `${(count('green') / scored.length) * 100}%` }} /></div></div>
      <div className="kpi"><span className="label">Ждут решения аналитика</span><span className="v num">{queue}</span><Link to="/queue" className="label row" style={{ gap: 6, color: 'var(--ink)' }}>Открыть очередь <ArrowRight size={12} /></Link></div>
    </div>
  )

  const switcher = (
    <div className="seg">
      <button className={variant === 'map' ? 'on' : ''} onClick={() => switchVariant('map')}><MapIcon size={13} />Карта</button>
      <button className={variant === 'editorial' ? 'on' : ''} onClick={() => switchVariant('editorial')}><LayoutGrid size={13} />Сводка</button>
    </div>
  )

  if (variant === 'editorial') {
    const attention = scored.filter((x) => x.zone !== 'green').slice(0, 3)
    return (
      <div className="fade-in">
        <section className="hero" style={{ height: 460 }}>
          <img src="/img/city-panorama.jpg" alt="Усть-Каменогорск" />
          <div className="over" />
          <div className="txt">
            <div style={{ flex: 1 }}>
              <div className="label" style={{ color: 'rgba(255,255,255,.75)', marginBottom: 16 }}>Өскемен · 14 октября 2026 · обновлено 23:10</div>
              <h1 className="display d-xxl" style={{ color: '#fff' }}>Строится <span className="serif-i">{scored.filter((x) => x.c.stage !== 'введён').length}</span><br />жилых комплексов</h1>
            </div>
            <div className="stack" style={{ alignItems: 'end' }}>
              {switcher}
              <span className="label" style={{ color: 'rgba(255,255,255,.7)' }}>Фото: Wikimedia Commons, CC BY-SA</span>
            </div>
          </div>
        </section>
        <div className="page" style={{ paddingTop: 0 }}>
          {kpis}
          <div className="row" style={{ justifyContent: 'space-between', margin: '48px 0 20px' }}>
            <h2 className="display d-l">Требуют <span className="serif-i">внимания</span></h2>
            <Link to="/complexes" className="btn ghost">Весь реестр <ArrowUpRight size={14} /></Link>
          </div>
          <div className="grid" style={{ gridTemplateColumns: '1.4fr 1fr 1fr' }}>
            {attention.map(({ c, r }, i) => (
              <Link key={c.id} to={`/complexes/${c.id}`} className="stack" style={{ gap: 14 }}>
                <div className="ph-frame" style={{ height: i === 0 ? 420 : 300 }}>
                  <img className="img" src={c.cover} alt="" />
                  <div className="tag"><ZoneBadge zone={r.zone} pill /></div>
                </div>
                <div className="row" style={{ alignItems: 'start' }}>
                  <span className="label">0{i + 1}</span>
                  <div style={{ flex: 1 }}>
                    <div className="display d-m">{c.name}</div>
                    <div className="muted" style={{ marginTop: 6 }}>{developers.find((d) => d.id === c.developerId)?.name} · {c.district}</div>
                  </div>
                  <Score value={r.score} zone={r.zone} solid />
                </div>
                <div className="ink2" style={{ borderTop: '1px solid var(--line)', paddingTop: 10 }}>{r.summary}</div>
              </Link>
            ))}
          </div>

          <div className="grid" style={{ gridTemplateColumns: '1fr 1.3fr', marginTop: 64, gap: 48 }}>
            <div>
              <div className="label" style={{ marginBottom: 14 }}>Распределение по зонам</div>
              <div style={{ display: 'flex', height: 56 }}>
                {(['red', 'yellow', 'green'] as Zone[]).map((z) => (
                  <div key={z} className={`z-${z}`} style={{ flex: count(z), background: 'var(--z)', color: '#fff', padding: 10 }}><span className="num" style={{ fontSize: 26 }}>{count(z)}</span></div>
                ))}
              </div>
              <div style={{ marginTop: 28, height: 320, position: 'relative' }}>
                <MapView items={scored} onSelect={(id) => nav(`/complexes/${id}`)} zoom={11} interactive={false} />
              </div>
            </div>
            <div>
              <div className="row" style={{ justifyContent: 'space-between', borderBottom: '1px solid var(--line-strong)', paddingBottom: 12 }}>
                <span className="label" style={{ color: 'var(--ink)' }}>Свежие публикации</span>
                <Link to="/ads" className="label">Вся лента →</Link>
              </div>
              {ads.slice(0, 5).map((a) => {
                const c = complexes.find((x) => x.id === a.complexId)
                const s = scoreAd(a, c)
                return (
                  <Link key={a.id} to={`/ads/${a.id}`} style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 16, padding: '16px 0', borderBottom: '1px solid var(--line)' }}>
                    <div>
                      <div className="row" style={{ marginBottom: 6 }}><PlatformTag p={a.platform} /><span className="label">{fmtDT(a.date)}</span>{c && <span className="label">→ {c.name}</span>}</div>
                      <div className="ink2" style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}><Highlight text={a.text} /></div>
                    </div>
                    <Score value={s.score} zone={s.zone} />
                  </Link>
                )
              })}
            </div>
          </div>
        </div>
      </div>
    )
  }

  // Вариант «Карта»
  return (
    <div className="page fade-in" style={{ paddingTop: 28 }}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'end', marginBottom: 24 }}>
        <div>
          <div className="label" style={{ marginBottom: 12 }}>01 — Карта города · Усть-Каменогорск</div>
          <h1 className="display d-xl">Светофор <span className="serif-i">рисков</span></h1>
        </div>
        {switcher}
      </div>
      {kpis}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 400px', height: 'calc(100vh - 330px)', minHeight: 560, borderBottom: '1px solid var(--line)' }}>
        <div style={{ position: 'relative' }}>
          <MapView items={visible} selected={sel} onSelect={onSelect} />
          <div className="map-legend">
            <span className="label">Легенда</span>
            <span className="zone z-red"><i />Красная · 60+ и R1+R2</span>
            <span className="zone z-yellow"><i />Жёлтая · 25–59</span>
            <span className="zone z-green"><i />Зелёная · до 25</span>
          </div>
        </div>
        <div style={{ borderLeft: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div className="row" style={{ padding: 12, borderBottom: '1px solid var(--line)', gap: 6 }}>
            {(['all', 'red', 'yellow', 'green'] as const).map((z) => (
              <button key={z} className={`chip ${zf === z ? 'on' : ''}`} onClick={() => setZf(z)}>
                {z === 'all' ? `Все · ${scored.length}` : <span className={`zone z-${z}`}><i />{count(z)}</span>}
              </button>
            ))}
          </div>
          {selected ? (
            <div className="fade-in" style={{ overflow: 'auto' }} key={selected.c.id}>
              <div className="ph-frame" style={{ height: 200 }}>
                <img className="img" src={selected.c.cover} alt="" />
                <div className="tag"><ZoneBadge zone={selected.zone} pill /></div>
                <button className="btn sm" style={{ position: 'absolute', right: 10, top: 10, background: 'var(--surface)' }} onClick={() => setSel(undefined)}>✕</button>
              </div>
              <div style={{ padding: 18 }} className="stack">
                <div className="row" style={{ alignItems: 'start' }}>
                  <div style={{ flex: 1 }}>
                    <div className="display d-m">{selected.c.name}</div>
                    <div className="muted" style={{ marginTop: 4 }}>{selected.c.address} · {selected.c.district}</div>
                  </div>
                  <Score value={selected.score} zone={selected.zone} solid />
                </div>
                <div className="ink2">{selected.r.summary}</div>
                {selected.r.needsCheck && <div><NeedsCheck /></div>}
                <hr className="rule" />
                <div className="grid g-2" style={{ gap: 10 }}>
                  <span className="label">Гарантия ЕО</span><TriMark v={selected.c.basis.guarantee} />
                  <span className="label">Разрешение МИО</span><TriMark v={selected.c.basis.mio} />
                  <span className="label">Банк (БВУ)</span><TriMark v={selected.c.basis.bvu} />
                  <span className="label">В перечне ГАСК</span><TriMark v={selected.c.inPublishedList} />
                  <span className="label">Стадия</span><span>{selected.c.stage} · {selected.c.progress}%</span>
                </div>
                <Link to={`/complexes/${selected.c.id}`} className="btn primary" style={{ justifyContent: 'center', marginTop: 6 }}>Открыть карточку ЖК <ArrowRight size={14} /></Link>
              </div>
            </div>
          ) : (
            <div style={{ overflow: 'auto' }}>
              {visible.map(({ c, r }) => (
                <button key={c.id} onClick={() => setSel(c.id)} style={{ display: 'grid', gridTemplateColumns: '54px 1fr auto', gap: 12, padding: '12px 14px', border: 0, borderBottom: '1px solid var(--line)', background: 'transparent', width: '100%', textAlign: 'left', alignItems: 'center' }}>
                  <img src={c.cover} alt="" style={{ width: 54, height: 40, objectFit: 'cover', filter: 'var(--img-filter)' }} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 500 }}>{c.name}</div>
                    <div className="label" style={{ fontSize: 10, marginTop: 2 }}>{c.district} · {c.stage}</div>
                  </div>
                  <Score value={r.score} zone={r.zone} />
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
