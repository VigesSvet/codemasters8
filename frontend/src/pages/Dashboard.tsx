import { useCallback, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Button, Search, Tab, TabList, Tabs } from '@carbon/react'
import { ArrowLeft, ArrowRight } from '@carbon/icons-react'
import MapView from '../components/MapView'
import { Highlight, NeedsCheck, PlatformIcon, Score, StatusTag, TriTag, ZoneDot, ZoneTag, fmtShort } from '../components/ui'
import { developers } from '../data/demo'
import type { Zone } from '../data/types'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'

/** Карта объектов — по образцу 2ГИС: карта на весь экран, слева панель поиска и карточка объекта */
export default function Dashboard() {
  const { complexes, ads } = useStore()
  const [q, setQ] = useState('')
  const [zf, setZf] = useState<Zone | 'all'>('all')
  const [sel, setSel] = useState<string | undefined>()
  const [tab, setTab] = useState(0)

  const scored = useMemo(() => complexes.map((c) => { const r = scoreComplex(c, ads); return { c, r, zone: r.zone, score: r.score } }).sort((a, b) => b.score - a.score), [complexes, ads])
  const visible = useMemo(() => scored.filter((x) => (zf === 'all' || x.zone === zf) && (!q || `${x.c.name} ${x.c.address}`.toLowerCase().includes(q.toLowerCase()))), [scored, zf, q])
  const count = (z: Zone) => scored.filter((x) => x.zone === z).length
  const s = scored.find((x) => x.c.id === sel)
  const onSelect = useCallback((id: string) => { setSel(id); setTab(0) }, [])
  const dev = s && developers.find((d) => d.id === s.c.developerId)
  const linked = s ? ads.filter((a) => a.complexId === s.c.id) : []

  return (
    <div className="map-page">
      <MapView items={visible} selected={sel} onSelect={onSelect} controls padLeft={420} />

      <div className="map-panel">
        {!s ? (
          <>
            <div className="map-panel__head">
              <Search size="lg" labelText="Поиск" placeholder="ЖК, адрес, застройщик, БИН" value={q} onChange={(e) => setQ(e.target.value)} closeButtonLabelText="Очистить" />
              <div className="map-cats">
                <button className={`map-cat ${zf === 'all' ? 'on' : ''}`} onClick={() => setZf('all')}><span className="ic">{scored.length}</span>Все</button>
                <button className={`map-cat red ${zf === 'red' ? 'on' : ''}`} onClick={() => setZf('red')}><span className="ic">{count('red')}</span>Красные</button>
                <button className={`map-cat yellow ${zf === 'yellow' ? 'on' : ''}`} onClick={() => setZf('yellow')}><span className="ic">{count('yellow')}</span>Жёлтые</button>
                <button className={`map-cat green ${zf === 'green' ? 'on' : ''}`} onClick={() => setZf('green')}><span className="ic">{count('green')}</span>Зелёные</button>
              </div>
            </div>
            <div className="map-panel__body">
              <div className="label-01" style={{ padding: '12px 16px 4px' }}>Усть-Каменогорск · {visible.length} объектов, по убыванию риска</div>
              {visible.map(({ c, r }) => (
                <button key={c.id} className="obj-row" onClick={() => onSelect(c.id)}>
                  <img src={c.cover} alt="" />
                  <div style={{ minWidth: 0 }}>
                    <div className="t">ЖК «{c.name}»</div>
                    <div className="a">{c.address}</div>
                    <div className="a" style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 6 }}><ZoneDot zone={r.zone} />{c.stage} · {c.progress}%{r.needsCheck && ' · нужна проверка'}</div>
                  </div>
                  <Score value={r.score} zone={r.zone} />
                </button>
              ))}
            </div>
          </>
        ) : (
          <div className="map-panel__body firm" key={s.c.id}>
            <div className="cover">
              <img src={s.c.cover} alt="" />
              <div className="tags"><ZoneTag zone={s.zone} size="sm" /></div>
              <Button kind="secondary" size="sm" hasIconOnly renderIcon={ArrowLeft} iconDescription="Назад к списку" onClick={() => setSel(undefined)} style={{ position: 'absolute', right: 12, top: 12 }} />
            </div>
            <div className="body">
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
                <div>
                  <h3>ЖК «{s.c.name}»</h3>
                  <div className="muted" style={{ marginTop: 4 }}>{s.c.address} · {s.c.district}</div>
                </div>
                <Score value={s.score} zone={s.zone} />
              </div>
              <p style={{ marginTop: 12 }}>{s.r.summary}</p>
              {s.r.needsCheck && <div style={{ marginTop: 8 }}><NeedsCheck /></div>}
            </div>
            <Tabs selectedIndex={tab} onChange={({ selectedIndex }) => setTab(selectedIndex)}>
              <TabList aria-label="Разделы" contained fullWidth>
                <Tab>Обзор</Tab>
                <Tab>Реклама · {linked.length}</Tab>
                <Tab>Риск</Tab>
              </TabList>
            </Tabs>
            <div className="body">
              {tab === 0 && (
                <div className="kv" style={{ marginTop: 0 }}>
                  <span>Застройщик</span><span style={{ textAlign: 'right' }}>{dev?.name}</span>
                  <span>Гарантия ЕО</span><TriTag v={s.c.basis.guarantee} />
                  <span>Разрешение МИО</span><TriTag v={s.c.basis.mio} />
                  <span>Банк (БВУ)</span><TriTag v={s.c.basis.bvu} />
                  <span>Перечень ГАСК</span><TriTag v={s.c.inPublishedList} label={s.c.inPublishedList === 'yes' ? 'В перечне' : undefined} />
                  <span>Стадия</span><span>{s.c.stage}, {s.c.progress}%</span>
                  <span>Этажность</span><span>{s.c.floors} эт. · {s.c.flats} кв.</span>
                </div>
              )}
              {tab === 1 && (linked.length ? linked.map((a) => (
                <Link key={a.id} to={`/ads/${a.id}`} style={{ display: 'flex', gap: 10, padding: '10px 0', borderBottom: '1px solid var(--cds-border-subtle-01)', textDecoration: 'none', color: 'inherit' }}>
                  <PlatformIcon p={a.platform} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="label-01">{a.account} · {fmtShort(a.date)}</div>
                    <div style={{ fontSize: 13, marginTop: 2, display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}><Highlight text={a.text} /></div>
                    <div style={{ marginTop: 6 }}><StatusTag s={a.status} /></div>
                  </div>
                </Link>
              )) : <p className="muted">Публикаций не найдено</p>)}
              {tab === 2 && s.r.factors.map((f) => (
                <div key={f.code} className={`factor ${f.state}`}><span className="code">{f.code}</span><div>{f.title}<div className="e">{f.evidence}</div></div><span className="pts">+{f.points}</span></div>
              ))}
              <Button as={Link} to={`/complexes/${s.c.id}`} renderIcon={ArrowRight} style={{ width: '100%', maxWidth: 'none', marginTop: 16 }}>Открыть карточку объекта</Button>
            </div>
          </div>
        )}
      </div>

      <div className="map-legend">
        <span><ZoneDot zone="red" />Красная — 60+ и R1+R2</span>
        <span><ZoneDot zone="yellow" />Жёлтая — 25–59</span>
        <span><ZoneDot zone="green" />Зелёная — до 25</span>
      </div>
    </div>
  )
}
