import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Cell, Highlight, PageTitle, PlatformIcon, Score, Seg, StatusDot, StatusTag, Toggle, adKey, fmtDT, platformMeta, statusMeta } from '../components/ui'
import type { AdStatus, Platform } from '../data/types'
import { scoreAd } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcBuilding, IcCheck, IcClose, IcGrid, IcList, IcPlus, IcRefresh, IcSearch } from '../ui/icons'

const ORDER: AdStatus[] = ['new', 'needs_check', 'in_review', 'confirmed', 'dismissed']

export default function Ads() {
  const { ads, complexes, notify } = useStore()
  const nav = useNavigate()
  const [view, setView] = useState<'feed' | 'grid'>('feed')
  const [q, setQ] = useState('')
  const [plat, setPlat] = useState<Platform | 'all'>('all')
  const [status, setStatus] = useState<AdStatus | 'all'>('all')
  const [risky, setRisky] = useState(false)
  const [manual, setManual] = useState(false)

  const rows = useMemo(() => ads.map((a) => { const c = complexes.find((x) => x.id === a.complexId); return { a, c, s: scoreAd(a, c) } })
    .filter(({ a, c, s }) => (!q || `${a.text} ${a.account} ${c?.name ?? ''}`.toLowerCase().includes(q.toLowerCase()))
      && (plat === 'all' || a.platform === plat) && (status === 'all' || a.status === status) && (!risky || s.zone !== 'green') && (!manual || a.source === 'manual'))
    .sort((x, y) => y.s.score - x.s.score), [ads, complexes, q, plat, status, risky, manual])
  const plats = (Object.keys(platformMeta) as Platform[]).filter((p) => ads.some((a) => a.platform === p))

  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Лента публикаций']]} title="Мониторинг рекламы"
        sub={<>Подсветка: <mark>продажа</mark>, <mark className="fund">сбор денег</mark>, <mark className="safe">признаки законности</mark>. Сортировка — по риску.</>}
        actions={<><button className="btn g m" onClick={() => notify('Сбор публикаций запущен (демо)')}><IcRefresh size={20} />Запустить сбор</button><Link to="/ads/new" className="btn y m"><IcPlus size={20} />Добавить вручную</Link></>} />

      <div className="cols">
        <div className="stack sticky">
          <div className="card">
            <div className="field">
              <span className="mk c2"><IcSearch /></span>
              <div className="body"><input placeholder="Текст, аккаунт или ЖК" value={q} onChange={(e) => setQ(e.target.value)} /></div>
              {q && <button className="c3" onClick={() => setQ('')}><IcClose size={18} /></button>}
            </div>
          </div>
          <div className="card">
            <div className="tiles">
              <button className={`tile ${plat === 'all' ? 'on' : ''}`} onClick={() => setPlat('all')} style={{ minHeight: 84 }}>
                <span className="pic" style={{ height: 28 }}><span className="zone-pic all" style={{ width: 28, height: 28, borderRadius: 9 }}><IcGrid size={16} /></span></span>
                <span className="nm">Все</span><span className="vl num">{ads.length}</span>
              </button>
              {plats.map((p) => (
                <button key={p} className={`tile ${plat === p ? 'on' : ''}`} onClick={() => setPlat(p)} style={{ minHeight: 84 }}>
                  <span className="pic" style={{ height: 28 }}><PlatformIcon p={p} /></span>
                  <span className="nm">{platformMeta[p].name}</span><span className="vl num">{ads.filter((a) => a.platform === p).length}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="card">
            {(['all', ...ORDER] as const).map((st) => (
              <Cell key={st} onClick={() => setStatus(st)} icon={st === 'all' ? <span className="zdot" style={{ background: 'var(--text-3)' }} /> : <StatusDot s={st} />}
                title={st === 'all' ? 'Все статусы' : statusMeta[st].name}
                end={<><span className="num">{st === 'all' ? ads.length : ads.filter((a) => a.status === st).length}</span>{status === st && <IcCheck size={20} strokeWidth={2.5} style={{ color: 'var(--text)' }} />}</>} />
            ))}
          </div>
          <div className="card">
            <Cell title="Только с риском" sub="Жёлтая и красная оценка" end={<Toggle on={risky} onChange={setRisky} />} />
            <Cell title="Добавлены вручную" end={<Toggle on={manual} onChange={setManual} />} />
          </div>
        </div>

        <div>
          <div className="row between" style={{ padding: '4px 4px 12px' }}>
            <span className="t-h3">{rows.length} публикаций</span>
            <Seg value={view} onChange={setView} items={[['feed', <><IcList size={18} />Лента</>], ['grid', <><IcGrid size={18} />Карточки</>]]} />
          </div>
          {rows.length === 0 && <div className="card empty">Ничего не найдено</div>}
          {view === 'feed' ? (
            <div className="stack fade">
              {rows.map(({ a, c, s }) => (
                <Link key={a.id} to={`/ads/${a.id}`} className="card" style={{ display: 'grid', gridTemplateColumns: '150px 1fr', gap: 20, padding: 8 }}>
                  {a.image ? <div className="ph" style={{ borderRadius: 18, aspectRatio: '1' }}><img src={a.image} alt="" /></div>
                    : <div style={{ borderRadius: 18, aspectRatio: '1', background: 'var(--bg)', display: 'grid', placeItems: 'center' }}><PlatformIcon p={a.platform} size={44} /></div>}
                  <div style={{ padding: '10px 16px 10px 0', display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0 }}>
                    <div className="row between">
                      <div className="row" style={{ gap: 8, minWidth: 0 }}>
                        <PlatformIcon p={a.platform} size={24} />
                        <span className="t-sm bold" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.account}</span>
                        <span className="t-sm c3">{fmtDT(a.date)}</span>
                      </div>
                      <span className="t-xs c3 num">{adKey(a.id)}</span>
                    </div>
                    <p className="clamp3" style={{ lineHeight: '22px' }}><Highlight text={a.text} /></p>
                    <div className="row wrap-f" style={{ gap: 8, marginTop: 'auto' }}>
                      <Score value={s.score} zone={s.zone} />
                      <StatusTag s={a.status} />
                      <span className="tag gray"><IcBuilding size={13} />{c ? c.name : 'ЖК не определён'}</span>
                      {a.source === 'manual' && <span className="tag outline">вручную</span>}
                      {a.reach && <span className="t-xs c3" style={{ marginLeft: 'auto' }}>охват ~{(a.reach / 1000).toFixed(1)} тыс.</span>}
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          ) : (
            <div className="gcards fade">
              {rows.map(({ a, c, s }) => (
                <div key={a.id} className="gcard" onClick={() => nav(`/ads/${a.id}`)}>
                  <div className="ph">
                    {a.image ? <img src={a.image} alt="" /> : <div style={{ height: '100%', display: 'grid', placeItems: 'center' }}><PlatformIcon p={a.platform} size={48} /></div>}
                    <div className="tl"><PlatformIcon p={a.platform} /></div>
                    <div className="tr"><Score value={s.score} zone={s.zone} /></div>
                  </div>
                  <div className="b">
                    <div className="t-sm c2">{a.account}</div>
                    <p className="t-sm clamp3" style={{ lineHeight: '20px' }}><Highlight text={a.text} /></p>
                    <div className="row between" style={{ marginTop: 'auto', paddingTop: 10 }}><span className="t-xs c3">{c?.name ?? '—'}</span><StatusTag s={a.status} /></div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  )
}
