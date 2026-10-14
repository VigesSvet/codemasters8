import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ImageOff, LayoutGrid, Plus, Rows3, Radar } from 'lucide-react'
import { Highlight, PageHead, PlatformTag, Score, StatusTag, ZoneBadge, fmtDT, platformMeta, statusMeta } from '../components/ui'
import type { AdStatus, Platform } from '../data/types'
import { scoreAd } from '../lib/risk'
import { useStore } from '../lib/store'

export default function Ads() {
  const { ads, complexes } = useStore()
  const nav = useNavigate()
  const [view, setView] = useState<'feed' | 'grid'>('feed')
  const [plats, setPlats] = useState<Platform[]>([])
  const [status, setStatus] = useState<AdStatus | 'all'>('all')

  const rows = useMemo(() => ads
    .map((a) => { const c = complexes.find((x) => x.id === a.complexId); return { a, c, s: scoreAd(a, c) } })
    .filter(({ a }) => (plats.length === 0 || plats.includes(a.platform)) && (status === 'all' || a.status === status)), [ads, complexes, plats, status])

  return (
    <div className="page">
      <PageHead idx="03" label="Мониторинг рекламы" title={<>Лента <span className="serif-i">публикаций</span></>}
        aside={<><button className="btn ghost"><Radar size={14} />Запустить сбор</button><Link to="/ads/new" className="btn primary"><Plus size={14} />Добавить вручную</Link></>}>
        <div className="row wrap" style={{ marginTop: 20, gap: 8 }}>
          <span className="label">Подсветка:</span>
          <span><mark>продажа</mark></span><span><mark className="fund">сбор денег</mark></span><span><mark className="safe">признаки законности</mark></span>
        </div>
      </PageHead>

      <div className="toolbar" style={{ borderTop: '1px solid var(--line-strong)' }}>
        {(Object.keys(platformMeta) as Platform[]).map((p) => (
          <button key={p} className={`chip ${plats.includes(p) ? 'on' : ''}`} onClick={() => setPlats((xs) => xs.includes(p) ? xs.filter((x) => x !== p) : [...xs, p])}>
            <PlatformTag p={p} /><span className="mono muted">{ads.filter((a) => a.platform === p).length}</span>
          </button>
        ))}
        <div className="spacer" />
        <div className="seg">
          {(['all', 'new', 'needs_check', 'in_review', 'confirmed', 'dismissed'] as const).map((s) => (
            <button key={s} className={status === s ? 'on' : ''} onClick={() => setStatus(s)}>{s === 'all' ? 'Все' : statusMeta[s].name}</button>
          ))}
        </div>
        <div className="seg">
          <button className={view === 'feed' ? 'on' : ''} onClick={() => setView('feed')}><Rows3 size={13} /></button>
          <button className={view === 'grid' ? 'on' : ''} onClick={() => setView('grid')}><LayoutGrid size={13} /></button>
        </div>
      </div>

      {view === 'feed' ? (
        <div className="fade-in">
          {rows.map(({ a, c, s }) => (
            <div key={a.id} className="feed-item" onClick={() => nav(`/ads/${a.id}`)}>
              {a.image ? <img className="feed-img" src={a.image} alt="" /> : <div className="feed-noimg"><ImageOff size={18} /></div>}
              <div className="stack" style={{ gap: 10 }}>
                <div className="row wrap"><PlatformTag p={a.platform} /><span className="mono">{a.account}</span><span className="label">{fmtDT(a.date)}</span>{a.reach && <span className="label">охват ~{(a.reach / 1000).toFixed(1)}k</span>}<span className="chip" style={{ height: 20, fontSize: 10 }}>{a.source === 'manual' ? 'вручную' : a.source === 'parser' ? 'парсер' : 'демо'}</span></div>
                <div className="feed-text"><Highlight text={a.text} /></div>
                {a.ocr && <div className="mono muted" style={{ fontSize: 11 }}>OCR: <Highlight text={a.ocr} /></div>}
              </div>
              <div className="stack" style={{ gap: 10, alignItems: 'flex-start', borderLeft: '1px solid var(--line)', paddingLeft: 20 }}>
                <div className="row"><Score value={s.score} zone={s.zone} solid /><ZoneBadge zone={s.zone} /></div>
                <div>
                  <div className="label">Связано с ЖК</div>
                  <div style={{ fontWeight: 500, marginTop: 4 }}>{c ? c.name : <span className="muted">не определено</span>}</div>
                  <div className="label" style={{ fontSize: 10, marginTop: 2 }}>уверенность {(a.matchConfidence * 100).toFixed(0)}%</div>
                </div>
                <StatusTag s={a.status} />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="grid g-4 fade-in" style={{ paddingTop: 24 }}>
          {rows.map(({ a, c, s }) => (
            <Link to={`/ads/${a.id}`} key={a.id} className="card" style={{ display: 'flex', flexDirection: 'column' }}>
              <div className="row" style={{ padding: '10px 12px', borderBottom: '1px solid var(--line)' }}><div className="avatar" style={{ width: 26, height: 26, fontSize: 10, background: platformMeta[a.platform].color }}>{a.account.replace(/[@^\W]/g, '').slice(0, 2).toUpperCase()}</div><span className="mono" style={{ fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.account}</span></div>
              <div className="ph-frame" style={{ aspectRatio: '1' }}>
                {a.image ? <img className="img" src={a.image} alt="" /> : <div style={{ padding: 20, height: '100%', display: 'grid', placeItems: 'center' }} className="display d-m">{c?.name ?? 'Текстовое объявление'}</div>}
                <div className="tag"><Score value={s.score} zone={s.zone} solid /></div>
              </div>
              <div style={{ padding: 12, fontSize: 13 }} className="ink2"><div style={{ display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}><Highlight text={a.text} /></div></div>
              <div className="row" style={{ padding: '0 12px 12px', marginTop: 'auto', justifyContent: 'space-between' }}><PlatformTag p={a.platform} /><StatusTag s={a.status} /></div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
