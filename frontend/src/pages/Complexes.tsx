import { useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { OpenDataList } from '../components/OpenData'
import { OFFLINE_FILE } from '../data/openData'
import { OpenDataNote, Origin } from '../components/Provenance'
import { FilterPop, Modal, NeedsCheck, Score, Seg, ZoneTag, fmtDate } from '../components/ui'
import type { Stage, Tri, Zone } from '../data/types'
import { isDemo, useRegistry } from '../lib/provenance'
import { useStore } from '../lib/store'
import { IcChevronRight, IcClose, IcGrid, IcHeart, IcList, IcMap, IcSearch, IcSliders } from '../ui/icons'

const STAGES: Stage[] = ['котлован', 'фундамент', 'каркас', 'кровля', 'отделка', 'введён']
const triText = (v: Tri) => (v === 'yes' ? 'есть' : v === 'no' ? 'нет' : 'нет сведений')
const quarter = (d: string) => { const x = new Date(d); return `${['I', 'II', 'III', 'IV'][Math.floor(x.getMonth() / 3)]} кв. ${x.getFullYear()}` }
type Sort = 'risk' | 'end' | 'progress'
type Layer = 'demo' | 'open'

/** Выдача ЖК по образцу realty.yandex.ru/…/novostrojka: полоса фильтров, чипы, сортировка, карточки */
export default function Complexes() {
  const { mode, complexes, ads, developers, riskOf } = useStore()
  const nav = useNavigate()
  const [sp, setSp] = useSearchParams()
  // слой — в адресе: «назад» из карточки объекта возвращает в тот же список
  const layer: Layer = sp.get('layer') === 'open' ? 'open' : 'demo'
  const setLayer = (l: Layer) => setSp((p) => { const n = new URLSearchParams(p); if (l === 'open') n.set('layer', 'open'); else n.delete('layer'); return n }, { replace: true })
  const reg = useRegistry()
  const [view, setView] = useState<'grid' | 'list'>('grid')
  const [q, setQ] = useState('')
  const [zone, setZone] = useState<Zone[]>(sp.get('zone') ? [sp.get('zone') as Zone] : [])
  const [district, setDistrict] = useState<string[]>([])
  const [stage, setStage] = useState<Stage[]>([])
  const [chip, setChip] = useState<string>('')
  const [sort, setSort] = useState<Sort>('risk')
  const [allF, setAllF] = useState(false)
  const [fav, setFav] = useState<string[]>([])

  const all = useMemo(() => complexes.map((c) => ({ c, r: riskOf(c), dev: developers.find((d) => d.id === c.developerId), nAds: ads.filter((a) => a.complexId === c.id).length })), [complexes, ads, developers, riskOf])
  const chips: [string, string, (x: (typeof all)[number]) => boolean][] = [
    ['check', 'Нужна проверка', (x) => x.r.needsCheck],
    ['nolist', 'Нет в перечне ГАСК', (x) => x.c.inPublishedList === 'no'],
    ['idle', 'Стройка стоит', (x) => x.c.idleDays >= 30],
    ['ads', 'С рекламой', (x) => x.nAds > 0],
    ['built', 'Сданные', (x) => x.c.stage === 'введён'],
  ]
  const match = (x: (typeof all)[number], f: { zone?: Zone[]; district?: string[]; stage?: Stage[] } = {}) => {
    const z = f.zone ?? zone, d = f.district ?? district, st = f.stage ?? stage
    return (!q || `${x.c.name} ${x.c.address} ${x.dev?.name} ${x.dev?.bin}`.toLowerCase().includes(q.toLowerCase()))
      && (!z.length || z.includes(x.r.zone)) && (!d.length || d.includes(x.c.district)) && (!st.length || st.includes(x.c.stage))
      && (!chip || chips.find((ch) => ch[0] === chip)![2](x))
  }
  const rows = all.filter((x) => match(x))
    .sort((a, b) => sort === 'risk' ? b.r.score - a.r.score : sort === 'end' ? a.c.plannedEnd.localeCompare(b.c.plannedEnd) : b.c.progress - a.c.progress)
  const districts = [...new Set(complexes.map((c) => c.district))]
  const dirty = zone.length || district.length || stage.length || chip || q
  const reset = () => { setZone([]); setDistrict([]); setStage([]); setChip(''); setQ('') }

  const layerSeg = (
    <Seg value={layer} onChange={setLayer} items={[
      ['demo', <>Сценарии (демо)<span style={{ opacity: 0.56 }}>{complexes.length}</span></>],
      ['open', <>Открытые данные{reg.total !== undefined && <span style={{ opacity: 0.56 }}>{reg.total}</span>}</>],
    ]} />
  )

  if (layer === 'open') return (
    <>
      <div className="fbar" style={{ marginTop: 12 }}>
        {layerSeg}
        <label className="fsearch">
          <input placeholder="Название, адрес, застройщик или БИН" value={q} onChange={(e) => setQ(e.target.value)} />
          {q ? <button className="c3" onClick={() => setQ('')}><IcClose size={16} /></button> : <Link to="/" className="link t-sm bold">На карте</Link>}
        </label>
      </div>
      <div className="page-title" style={{ alignItems: 'center' }}>
        <div>
          <h1 className="t-h1">Открытые данные: объекты долевого строительства</h1>
          <p className="c2" style={{ marginTop: 8, maxWidth: 820 }}>Реальные объекты Усть-Каменогорска из Homeportal и Казреестра (CSV Ashyq Data) — как их опубликовал источник, со ссылкой и временем сбора.
            Записи загружены файлом, автоматической синхронизации с реестрами нет. <Link className="link" to="/sources">Источники и импорт</Link></p>
        </div>
      </div>
      <div style={{ marginBottom: 24 }}><OpenDataNote /></div>
      {mode === 'demo' && <div className="ai-note" style={{ marginBottom: 16 }}>Демо-режим без бэкенда: показана встроенная копия открытых данных ({OFFLINE_FILE}) — те же записи, что загружает сервер. Импорт новых файлов — с бэкендом (./dev.sh).</div>}
      {reg.data ? <>
          {reg.err && <div className="alog-err t-sm">Список не обновился: {reg.err} <button className="link" onClick={reg.retry}>Повторить</button></div>}
          <OpenDataList data={reg.data} q={q} />
        </>
          : reg.err ? <div className="empty">Открытые данные не загрузились: {reg.err} <button className="link" onClick={reg.retry}>Повторить</button></div>
            : <div className="empty">Загружаем открытые данные…</div>}
    </>
  )

  return (
    <>
      <div className="fbar" style={{ marginTop: 12 }}>
        {layerSeg}
        <span className="vsep" />
        <button className="fbtn gray" onClick={reset}><IcSearch />{dirty ? 'Сбросить' : 'Лёгкий поиск'}</button>
        <span className="vsep" />
        <button className="fbtn icon" aria-label="Все фильтры" onClick={() => setAllF(true)}><IcSliders /></button>
        <FilterPop label="Зона" title="Зона риска" multi value={zone} onChange={setZone} count={(v) => all.filter((x) => match(x, { zone: v })).length}
          options={[['red', 'Красная'], ['yellow', 'Жёлтая'], ['green', 'Зелёная']]} />
        <FilterPop label="Район" title="Район города" multi value={district} onChange={setDistrict} count={(v) => all.filter((x) => match(x, { district: v })).length}
          options={districts.map((d) => [d, d] as [string, string])} />
        <FilterPop label="Стадия" title="Стадия строительства" multi value={stage} onChange={setStage} count={(v) => all.filter((x) => match(x, { stage: v })).length}
          options={STAGES.map((s) => [s, s[0].toUpperCase() + s.slice(1)] as [Stage, string])} />
        <label className="fsearch">
          <input placeholder="ЖК, адрес, застройщик или БИН" value={q} onChange={(e) => setQ(e.target.value)} />
          {q ? <button className="c3" onClick={() => setQ('')}><IcClose size={16} /></button> : <Link to="/" className="link t-sm bold">На карте</Link>}
        </label>
      </div>

      <div className="page-title" style={{ alignItems: 'center' }}>
        <div>
          <h1 className="t-h1">Новостройки Усть-Каменогорска на контроле</h1>
          <p className="t-sm c2 row wrap-f" style={{ marginTop: 8, gap: 8 }}><Origin kind="demo" label="Демо-сценарии" />Вымышленные ЖК и застройщики — на них показана модель риска (ТЗ §4, §5). Реальные объекты — во вкладке «Открытые данные», без оценки риска.</p>
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
                <div className="tl">{isDemo(c) && <Origin kind="demo" label="демо" onPhoto title="Демонстрационные данные: вымышленный ЖК" />}<span className="tag white">{c.stage === 'введён' ? 'сдан' : c.stage}</span>{r.needsCheck && <span className="tag white">нужна проверка</span>}{c.idleDays >= 30 && <span className="tag white">стоит {c.idleDays} дн.</span>}</div>
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
                <div className="bold row" style={{ fontSize: 17, gap: 8 }}>{c.name}{isDemo(c) && <Origin kind="demo" label="демо" title="Демонстрационные данные: вымышленный ЖК" />}</div>
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
      {allF && (
        <Modal wide title="Все фильтры" onClose={() => setAllF(false)}
          footer={<><button className="btn g" onClick={reset}>Сбросить</button><button className="btn y" onClick={() => setAllF(false)}>Показать {rows.length} {rows.length === 1 ? 'новостройку' : rows.length < 5 ? 'новостройки' : 'новостроек'}</button></>}>
          <div style={{ padding: '0 24px' }}>
            {([['Зона', ['red', 'yellow', 'green'], zone, setZone, { red: 'Красная', yellow: 'Жёлтая', green: 'Зелёная' }],
              ['Район', districts, district, setDistrict, null],
              ['Стадия', STAGES, stage, setStage, null]] as [string, string[], string[], (v: never[]) => void, Record<string, string> | null][]).map(([l, opts, val, set, names]) => (
              <div className="frow top" key={l}>
                <label>{l}</label>
                <div className="chips">{opts.map((o) => <button key={o} className={`chip ${val.includes(o) ? 'on' : ''}`} onClick={() => set((val.includes(o) ? val.filter((x) => x !== o) : [...val, o]) as never[])}>{names?.[o] ?? o[0].toUpperCase() + o.slice(1)}</button>)}</div>
              </div>
            ))}
            <div className="frow top">
              <label>Признаки</label>
              <div className="chips">{chips.map(([k, l, fn]) => <button key={k} className={`chip ${chip === k ? 'on' : ''}`} onClick={() => setChip(chip === k ? '' : k)}>{l}<span className="n">{all.filter(fn).length}</span></button>)}</div>
            </div>
          </div>
        </Modal>
      )}
    </>
  )
}
