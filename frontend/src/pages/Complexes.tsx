import { useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { NeedsCheck, Score, ZoneTag, fmtDate } from '../components/ui'
import { developers } from '../data/demo'
import type { Stage, Tri, Zone } from '../data/types'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcCheck, IcChevronDown, IcChevronRight, IcClose, IcGrid, IcHeart, IcList, IcMap, IcSearch, IcSliders } from '../ui/icons'

const STAGES: Stage[] = ['котлован', 'фундамент', 'каркас', 'кровля', 'отделка', 'введён']
const triText = (v: Tri) => (v === 'yes' ? 'есть' : v === 'no' ? 'нет' : 'нет сведений')
const quarter = (d: string) => { const x = new Date(d); return `${['I', 'II', 'III', 'IV'][Math.floor(x.getMonth() / 3)]} кв. ${x.getFullYear()}` }
type Sort = 'risk' | 'end' | 'progress'

/** Выдача ЖК по образцу realty.yandex.ru/…/novostrojka: полоса фильтров, чипы, сортировка, карточки */
export default function Complexes() {
  const { complexes, ads } = useStore()
  const nav = useNavigate()
  const [sp] = useSearchParams()
  const [view, setView] = useState<'grid' | 'list'>('grid')
  const [q, setQ] = useState('')
  const [zone, setZone] = useState<Zone | ''>((sp.get('zone') as Zone) || '')
  const [district, setDistrict] = useState('')
  const [stage, setStage] = useState<Stage | ''>('')
  const [chip, setChip] = useState<string>('')
  const [sort, setSort] = useState<Sort>('risk')
  const [open, setOpen] = useState<string | null>(null)
  const [fav, setFav] = useState<string[]>([])

  const all = useMemo(() => complexes.map((c) => ({ c, r: scoreComplex(c, ads), dev: developers.find((d) => d.id === c.developerId), nAds: ads.filter((a) => a.complexId === c.id).length })), [complexes, ads])
  const chips: [string, string, (x: (typeof all)[number]) => boolean][] = [
    ['check', 'Нужна проверка', (x) => x.r.needsCheck],
    ['nolist', 'Нет в перечне ГАСК', (x) => x.c.inPublishedList === 'no'],
    ['idle', 'Стройка стоит', (x) => x.c.idleDays >= 30],
    ['ads', 'С рекламой', (x) => x.nAds > 0],
    ['built', 'Сданные', (x) => x.c.stage === 'введён'],
  ]
  const rows = all.filter((x) =>
    (!q || `${x.c.name} ${x.c.address} ${x.dev?.name} ${x.dev?.bin}`.toLowerCase().includes(q.toLowerCase()))
    && (!zone || x.r.zone === zone) && (!district || x.c.district === district) && (!stage || x.c.stage === stage)
    && (!chip || chips.find((ch) => ch[0] === chip)![2](x)))
    .sort((a, b) => sort === 'risk' ? b.r.score - a.r.score : sort === 'end' ? a.c.plannedEnd.localeCompare(b.c.plannedEnd) : b.c.progress - a.c.progress)
  const districts = [...new Set(complexes.map((c) => c.district))]
  const dirty = zone || district || stage || chip || q

  const drop = (key: string, label: string, value: string, opts: [string, string][], set: (v: string) => void): ReactNode => (
    <div className="nav-item">
      <button className={`fbtn ${value ? 'on' : ''}`} onClick={() => setOpen(open === key ? null : key)}>{value ? opts.find((o) => o[0] === value)?.[1] : label}<IcChevronDown /></button>
      {open === key && (
        <>
          <div className="menu-scrim" onClick={() => setOpen(null)} />
          <div className="menu fade" style={{ minWidth: 240 }}>
            {[['', `Любая ${label.toLowerCase()}`] as [string, string], ...opts].map(([v, l]) => (
              <button key={v || 'any'} onClick={() => { set(v); setOpen(null) }}><span className="lbl">{l}</span>{value === v && <IcCheck />}</button>
            ))}
          </div>
        </>
      )}
    </div>
  )

  return (
    <>
      <div className="fbar" style={{ marginTop: 12 }}>
        <button className="fbtn gray" onClick={() => { setZone(''); setDistrict(''); setStage(''); setChip(''); setQ('') }}><IcSearch />{dirty ? 'Сбросить' : 'Все объекты'}</button>
        <button className="fbtn icon" aria-label="Все фильтры"><IcSliders /></button>
        {drop('zone', 'Зона', zone, [['red', 'Красная зона'], ['yellow', 'Жёлтая зона'], ['green', 'Зелёная зона']], (v) => setZone(v as Zone))}
        {drop('district', 'Район', district, districts.map((d) => [d, d]), setDistrict)}
        {drop('stage', 'Стадия', stage, STAGES.map((s) => [s, s[0].toUpperCase() + s.slice(1)]), (v) => setStage(v as Stage))}
        <label className="fsearch">
          <input placeholder="ЖК, адрес, застройщик или БИН" value={q} onChange={(e) => setQ(e.target.value)} />
          {q ? <button className="c3" onClick={() => setQ('')}><IcClose size={16} /></button> : <Link to="/" className="link t-sm bold">На карте</Link>}
        </label>
      </div>

      <div className="page-title" style={{ alignItems: 'center' }}>
        <div>
          <h1 className="t-h1">Новостройки Усть-Каменогорска на контроле</h1>
          <div className="chips" style={{ marginTop: 16 }}>
            {chips.map(([k, l, fn]) => <button key={k} className={`chip ${chip === k ? 'on' : ''}`} onClick={() => setChip(chip === k ? '' : k)}>{l}<span className="n">{all.filter(fn).length}</span></button>)}
          </div>
        </div>
        <Link to="/" className="ph" style={{ width: 330, height: 100, borderRadius: 24, flex: 'none', background: 'url(/img/city-mountain.jpg) center/cover' }}>
          <span className="btn w m" style={{ position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%, -50%)' }}><IcMap size={16} />Смотреть на карте</span>
        </Link>
      </div>

      <div className="row between">
        <div className="sorter">
          <b>{rows.length} {rows.length === 1 ? 'новостройка' : rows.length < 5 ? 'новостройки' : 'новостроек'}:</b>
          {([['risk', 'по риску'], ['end', 'по сроку сдачи'], ['progress', 'по готовности']] as const).map(([k, l]) => <button key={k} className={sort === k ? 'on' : ''} onClick={() => setSort(k)}>{l}</button>)}
        </div>
        <div className="row" style={{ gap: 16 }}>
          <Link to="/ads" className="row bold" style={{ gap: 4, fontSize: 16 }}>{ads.length} публикаций в ленте<IcChevronRight size={16} /></Link>
          <div className="seg"><button className={view === 'grid' ? 'on' : ''} onClick={() => setView('grid')} aria-label="Карточки"><IcGrid /></button><button className={view === 'list' ? 'on' : ''} onClick={() => setView('list')} aria-label="Списком"><IcList /></button></div>
        </div>
      </div>

      {rows.length === 0 && <div className="empty">Нет объектов под эти фильтры</div>}
      {view === 'grid' ? (
        <div className="gcards fade">
          {rows.map(({ c, r, dev, nAds }) => (
            <div key={c.id} className="gcard" onClick={() => nav(`/complexes/${c.id}`)}>
              <div className="ph">
                <img src={c.cover} alt="" />
                <div className="tl"><span className="tag white">{c.stage === 'введён' ? 'сдан' : c.stage}</span>{r.needsCheck && <span className="tag white">нужна проверка</span>}{c.idleDays >= 30 && <span className="tag white">стоит {c.idleDays} дн.</span>}</div>
                <button className="tr fav" aria-label="В избранное" onClick={(e) => { e.stopPropagation(); setFav((f) => f.includes(c.id) ? f.filter((x) => x !== c.id) : [...f, c.id]) }} style={fav.includes(c.id) ? { background: 'var(--red)' } : undefined}><IcHeart size={18} /></button>
              </div>
              <div className="b">
                <span className="dev">{dev?.name}</span>
                <div className="row between" style={{ alignItems: 'flex-start' }}><span className="nm">{c.name}</span><Score value={r.score} zone={r.zone} /></div>
                <span className="meta">{c.stage === 'введён' ? 'Сдан' : 'Строится'} · {c.floors} эт. · {c.progress}% · сдача {quarter(c.plannedEnd)}</span>
                <span className="meta row" style={{ gap: 6 }}><span className={`zdot ${r.zone}`} />{c.address}, {c.district}</span>
                <div className="rows">
                  <span>Гарантия ЕО</span><span className={c.basis.guarantee === 'no' ? 'bold' : ''} style={{ color: c.basis.guarantee === 'no' ? 'var(--red)' : undefined }}>{triText(c.basis.guarantee)}</span>
                  <span>Разрешение МИО</span><span>{triText(c.basis.mio)}</span>
                  <span>Банк (БВУ)</span><span>{triText(c.basis.bvu)}</span>
                  <span>Перечень ГАСК</span><span>{c.inPublishedList === 'yes' ? 'в перечне' : triText(c.inPublishedList)}</span>
                  <span>Реклама</span><span>{nAds ? `${nAds} публ.` : '—'}</span>
                </div>
                <div className="acts">
                  <button className="btn y" onClick={(e) => { e.stopPropagation(); nav(`/complexes/${c.id}`) }}>Открыть</button>
                  <button className="btn g" onClick={(e) => { e.stopPropagation(); nav(`/complexes/${c.id}?tab=ads`) }}>Реклама</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="card fade">
          {rows.map(({ c, r, dev }) => (
            <button key={c.id} className="cell" onClick={() => nav(`/complexes/${c.id}`)} style={{ display: 'grid', gridTemplateColumns: '88px minmax(0,1.3fr) minmax(0,1fr) 170px 60px', gap: 20, minHeight: 92 }}>
              <img className="th" src={c.cover} alt="" style={{ width: 88, height: 64 }} />
              <div style={{ minWidth: 0 }}>
                <div className="t-xs c2">{dev?.name}</div>
                <div className="bold" style={{ fontSize: 17 }}>{c.name}</div>
                <div className="t-sm c2">{c.address} · {c.district}</div>
              </div>
              <div className="row wrap-f" style={{ gap: 6 }}><ZoneTag zone={r.zone} />{r.needsCheck && <NeedsCheck />}</div>
              <div>
                <div className="t-sm">{c.stage} · <span className="num">{c.progress}%</span></div>
                <div className="hbar" style={{ margin: '6px 0', height: 4 }}><i style={{ width: `${c.progress}%`, background: '#000' }} /></div>
                <div className="t-xs c2">сдача {fmtDate(c.plannedEnd)}</div>
              </div>
              <div style={{ justifySelf: 'end' }}><Score value={r.score} zone={r.zone} /></div>
            </button>
          ))}
        </div>
      )}
    </>
  )
}
