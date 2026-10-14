import { useCallback, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import MapView from '../components/MapView'
import { Cell, Highlight, PlatformIcon, Score, StatusTag, Toggle, ZoneTag, fmtShort } from '../components/ui'
import { developers } from '../data/demo'
import type { Tri, Zone } from '../data/types'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcBuilding, IcCamera, IcCheck, IcChevronLeft, IcChevronRight, IcClose, IcHelp, IcList, IcMegaphone, IcSearch, IcTraffic, IcUsers } from '../ui/icons'

type F = Zone | 'all' | 'check'

/** Карта: панель как у Такси слева, метки-ценники как у Недвижимости, категории как у Карт */
export default function Dashboard() {
  const { complexes, ads } = useStore()
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const [f, setF] = useState<F>('all')
  const [sel, setSel] = useState<string | undefined>()
  const [labels, setLabels] = useState(false)
  const [withAds, setWithAds] = useState(false)
  const [idle, setIdle] = useState(false)

  const scored = useMemo(() => complexes.map((c) => { const r = scoreComplex(c, ads); return { c, r, zone: r.zone, score: r.score } }).sort((a, b) => b.score - a.score), [complexes, ads])
  const visible = useMemo(() => scored.filter((x) =>
    (f === 'all' || (f === 'check' ? x.r.needsCheck : x.zone === f))
    && (!q || `${x.c.name} ${x.c.address} ${developers.find((d) => d.id === x.c.developerId)?.bin}`.toLowerCase().includes(q.toLowerCase()))
    && (!withAds || ads.some((a) => a.complexId === x.c.id && a.status !== 'dismissed'))
    && (!idle || x.c.idleDays >= 30)), [scored, f, q, withAds, idle, ads])
  const n = (z: F) => scored.filter((x) => (z === 'all' ? true : z === 'check' ? x.r.needsCheck : x.zone === z)).length
  const queue = ads.filter((a) => a.status === 'new' || a.status === 'needs_check').length
  const s = scored.find((x) => x.c.id === sel)
  const onSelect = useCallback((id: string) => setSel(id), [])
  const dev = s && developers.find((d) => d.id === s.c.developerId)
  const linked = s ? ads.filter((a) => a.complexId === s.c.id) : []

  const cats: [F, string, string][] = [['all', 'Все', 'all'], ['red', 'Красная', 'red'], ['yellow', 'Жёлтая', 'yellow'], ['green', 'Зелёная', 'green'], ['check', 'Проверка', 'blue']]
  const basis = (name: string, v: Tri) => (
    <div className="tile" style={{ minHeight: 0, background: 'var(--fill)', flexDirection: 'row', alignItems: 'center', gap: 10 }}>
      <span className={`zone-pic ${v === 'yes' ? 'green' : v === 'no' ? 'red' : 'gray'}`} style={{ width: 28, height: 28 }}>
        {v === 'yes' ? <IcCheck size={14} /> : v === 'no' ? <IcClose size={14} /> : <IcHelp size={14} />}
      </span>
      <div><div className="t-xs c2">{name}</div><div className="bold t-sm">{v === 'yes' ? 'Есть' : v === 'no' ? 'Нет' : 'Нет сведений'}</div></div>
    </div>
  )

  return (
    <div className="mapscreen">
      <MapView items={visible} selected={sel} onSelect={onSelect} controls labels={labels} padLeft={400} />

      <div className="left">
        {!s ? (
          <div className="scroll">
            <div className="card" style={{ padding: 16 }}>
              <div className="fsearch" style={{ height: 44 }}>
                <IcSearch size={18} />
                <input placeholder="ЖК, адрес или БИН застройщика" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Поиск" />
                {q && <button onClick={() => setQ('')} className="c3" aria-label="Очистить"><IcClose size={16} /></button>}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 4, marginTop: 16 }}>
                {cats.map(([k, label, color]) => (
                  <button key={k} onClick={() => setF(k)} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, padding: '6px 0', borderRadius: 12, background: f === k ? 'var(--fill)' : undefined }}>
                    <span className={`zone-pic ${color}`} style={{ width: 44, height: 44 }}>{k === 'check' ? <IcHelp /> : k === 'all' ? <IcTraffic /> : <IcBuilding />}</span>
                    <span className="t-xs" style={{ fontWeight: f === k ? 600 : 400 }}>{label}</span>
                    <span className="t-xs c2 num" style={{ marginTop: -4 }}>{n(k)}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="card">
              <div className="card-head" style={{ paddingBottom: 4 }}><h3 style={{ fontSize: 17 }}>{visible.length} объектов</h3><span className="t-xs c2">по риску</span></div>
              {visible.length === 0 && <div className="empty">Ничего не найдено</div>}
              {visible.map(({ c, r }) => (
                <Cell key={c.id} onClick={() => setSel(c.id)} inset
                  icon={<img className="th" src={c.cover} alt="" style={{ width: 44, height: 44, borderRadius: 10 }} />}
                  title={<span className="bold">{c.name}</span>} sub={`${c.address} · ${c.stage}`}
                  end={<Score value={r.score} zone={r.zone} />} />
              ))}
            </div>

            <div className="card">
              <Cell title="Названия на карте" end={<Toggle on={labels} onChange={setLabels} label="Названия" />} />
              <Cell title="Только с рекламой" end={<Toggle on={withAds} onChange={setWithAds} label="Только с рекламой" />} />
              <Cell title="Стройка стоит 30+ дней" end={<Toggle on={idle} onChange={setIdle} label="Простой" />} />
            </div>
          </div>
        ) : (
          <div className="scroll fade" key={s.c.id}>
            <div className="card">
              <Cell onClick={() => setSel(undefined)} icon={<IcChevronLeft />} title="Все объекты" />
              <div className="ph" style={{ height: 180, margin: '0 12px', borderRadius: 16 }}>
                <img src={s.c.cover} alt="" />
                <div className="tl"><span className="tag white">{s.c.stage}</span>{s.r.needsCheck && <span className="tag white">нужна проверка</span>}</div>
              </div>
              <div className="card-pad" style={{ paddingTop: 14 }}>
                <div className="t-xs c2">{dev?.name}</div>
                <div className="row between" style={{ alignItems: 'flex-start', marginTop: 2 }}>
                  <h2 className="t-h2">ЖК «{s.c.name}»</h2>
                  <Score value={s.score} zone={s.zone} />
                </div>
                <div className="t-sm c2" style={{ marginTop: 4 }}>{s.c.address} · {s.c.district}</div>
                <div style={{ marginTop: 10 }}><ZoneTag zone={s.zone} /></div>
                <p className="t-sm" style={{ marginTop: 10, lineHeight: '19px' }}>{s.r.summary}</p>
              </div>
            </div>
            <div className="card" style={{ padding: 8 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                {basis('Гарантия ЕО', s.c.basis.guarantee)}{basis('Разрешение МИО', s.c.basis.mio)}
                {basis('Банк (БВУ)', s.c.basis.bvu)}{basis('Перечень ГАСК', s.c.inPublishedList)}
              </div>
            </div>
            <div className="card">
              <Cell icon={<span className="ico"><IcMegaphone size={18} /></span>} title="Реклама" sub={linked.length ? `${linked.length} публ. · последняя ${fmtShort(linked[0].date)}` : 'Не найдено'} chevron to={`/complexes/${s.c.id}?tab=ads`} inset />
              <Cell icon={<span className="ico"><IcCamera size={18} /></span>} title="Фото-мониторинг" sub={s.c.idleDays ? `Нет активности ${s.c.idleDays} дн.` : `${s.c.photos.length} снимков · стройка идёт`} chevron to={`/complexes/${s.c.id}?tab=photos`} inset />
              <Cell icon={<span className="ico"><IcUsers size={18} /></span>} title="Застройщик" sub={`БИН ${dev?.bin}`} chevron to="/developers" inset />
            </div>
            {linked.slice(0, 2).map((a) => (
              <Link key={a.id} to={`/ads/${a.id}`} className="card card-pad" style={{ display: 'block' }}>
                <div className="row between"><div className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={22} /><span className="t-sm c2">{a.account}</span></div><StatusTag s={a.status} /></div>
                <p className="t-sm clamp3" style={{ marginTop: 10, lineHeight: '19px' }}><Highlight text={a.text} /></p>
              </Link>
            ))}
          </div>
        )}
        <div className="dock">
          {s
            ? <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}><button className="btn y" onClick={() => nav(`/complexes/${s.c.id}`)}>Карточка ЖК</button><button className="btn g" onClick={() => nav(`/complexes/${s.c.id}?tab=ads`)}>Реклама · {linked.length}</button></div>
            : <button className="btn y full" onClick={() => nav('/queue')}>Открыть очередь проверки · {queue}</button>}
        </div>
      </div>

      <Link to="/complexes" className="mapbtn" style={{ left: 412, top: 16 }}><IcList size={18} />Показать {visible.length} ЖК списком</Link>

      <Link to="/queue" className="promo dark" style={{ right: 16, bottom: 32, width: 280 }}>
        <div className="tx" style={{ padding: '18px 20px' }}>
          <div style={{ fontSize: 22, lineHeight: '24px', fontWeight: 800, textTransform: 'uppercase', letterSpacing: '-0.01em' }}>{queue} публикаций<br />ждут решения</div>
          <span className="mini">Открыть очередь <IcChevronRight size={14} /></span>
        </div>
      </Link>
    </div>
  )
}
