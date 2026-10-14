import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowDownUp, Download, Grid3x3, List, Plus, X } from 'lucide-react'
import { NeedsCheck, PageHead, Score, TriMark, ZoneBadge } from '../components/ui'
import { developers } from '../data/demo'
import type { Stage, Zone } from '../data/types'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'

type SortKey = 'score' | 'name' | 'progress' | 'plannedEnd'

export default function Complexes() {
  const { complexes, ads } = useStore()
  const nav = useNavigate()
  const [view, setView] = useState<'table' | 'grid'>('table')
  const [q, setQ] = useState('')
  const [zones, setZones] = useState<Zone[]>([])
  const [district, setDistrict] = useState('')
  const [stage, setStage] = useState<Stage | ''>('')
  const [onlyCheck, setOnlyCheck] = useState(false)
  const [sort, setSort] = useState<{ k: SortKey; dir: 1 | -1 }>({ k: 'score', dir: -1 })

  const rows = useMemo(() => complexes
    .map((c) => ({ c, r: scoreComplex(c, ads), dev: developers.find((d) => d.id === c.developerId) }))
    .filter(({ c, r, dev }) =>
      (!q || `${c.name} ${c.address} ${dev?.name} ${dev?.bin}`.toLowerCase().includes(q.toLowerCase())) &&
      (zones.length === 0 || zones.includes(r.zone)) && (!district || c.district === district) && (!stage || c.stage === stage) && (!onlyCheck || r.needsCheck))
    .sort((a, b) => {
      const k = sort.k
      const va = k === 'score' ? a.r.score : k === 'name' ? a.c.name : k === 'progress' ? a.c.progress : a.c.plannedEnd
      const vb = k === 'score' ? b.r.score : k === 'name' ? b.c.name : k === 'progress' ? b.c.progress : b.c.plannedEnd
      return (va > vb ? 1 : va < vb ? -1 : 0) * sort.dir
    }), [complexes, ads, q, zones, district, stage, onlyCheck, sort])

  const districts = [...new Set(complexes.map((c) => c.district))]
  const stages: Stage[] = ['котлован', 'фундамент', 'каркас', 'кровля', 'отделка', 'введён']
  const th = (k: SortKey, label: string) => (
    <th className="sortable" onClick={() => setSort((s) => ({ k, dir: s.k === k ? (s.dir === 1 ? -1 : 1) : -1 }))}>
      {label} {sort.k === k && (sort.dir === 1 ? '↑' : '↓')}
    </th>
  )
  const activeFilters = zones.length + (district ? 1 : 0) + (stage ? 1 : 0) + (onlyCheck ? 1 : 0)

  return (
    <div className="page">
      <PageHead idx="02" label="Реестр" title={<>Жилые <span className="serif-i">комплексы</span></>}
        aside={<><button className="btn ghost"><Download size={14} />Экспорт XLSX</button><button className="btn primary"><Plus size={14} />Добавить ЖК</button></>}>
        <p className="ink2" style={{ maxWidth: 620, marginTop: 18 }}>Все строящиеся и недавно введённые объекты Усть-Каменогорска. Основания привлечения средств сверяются с перечнем ГАСК и картотекой; отсутствие сведений не считается нарушением.</p>
      </PageHead>

      <div className="table-wrap">
        <div className="toolbar">
          <input className="input" style={{ width: 280, height: 30, padding: '0 10px' }} placeholder="Поиск: ЖК, адрес, застройщик, БИН" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="filter">
            <span className="k">Зона</span>
            {(['red', 'yellow', 'green'] as Zone[]).map((z) => (
              <button key={z} onClick={() => setZones((xs) => xs.includes(z) ? xs.filter((x) => x !== z) : [...xs, z])} style={{ background: zones.includes(z) ? 'var(--bg-2)' : undefined }}>
                <span className={`zone z-${z}`} style={{ opacity: zones.length && !zones.includes(z) ? 0.35 : 1 }}><i /></span>
              </button>
            ))}
          </div>
          <div className="filter"><span className="k">Район</span>
            <select value={district} onChange={(e) => setDistrict(e.target.value)} style={{ border: 0, background: 'transparent', outline: 0 }}>
              <option value="">любой</option>{districts.map((d) => <option key={d}>{d}</option>)}
            </select>
          </div>
          <div className="filter"><span className="k">Стадия</span>
            <select value={stage} onChange={(e) => setStage(e.target.value as Stage)} style={{ border: 0, background: 'transparent', outline: 0 }}>
              <option value="">любая</option>{stages.map((d) => <option key={d}>{d}</option>)}
            </select>
          </div>
          <button className={`chip ${onlyCheck ? 'on' : ''}`} style={{ height: 30 }} onClick={() => setOnlyCheck((v) => !v)}>Только «нужна проверка»</button>
          {activeFilters > 0 && <button className="btn sm ghost" onClick={() => { setZones([]); setDistrict(''); setStage(''); setOnlyCheck(false) }}><X size={12} />Сбросить ({activeFilters})</button>}
          <div className="spacer" />
          <span className="label">{rows.length} из {complexes.length}</span>
          <div className="seg">
            <button className={view === 'table' ? 'on' : ''} onClick={() => setView('table')}><List size={13} /></button>
            <button className={view === 'grid' ? 'on' : ''} onClick={() => setView('grid')}><Grid3x3 size={13} /></button>
          </div>
        </div>

        {view === 'table' ? (
          <table className="t fade-in">
            <thead>
              <tr>
                <th style={{ width: 80 }} />
                {th('name', 'Объект')}
                <th>Застройщик</th>
                <th>Гарантия ЕО</th>
                <th>МИО</th>
                <th>БВУ</th>
                <th>Перечень</th>
                {th('progress', 'Готовность')}
                {th('plannedEnd', 'Сдача')}
                {th('score', 'Риск')}
                <th>Зона</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ c, r, dev }) => (
                <tr key={c.id} onClick={() => nav(`/complexes/${c.id}`)}>
                  <td><img className="thumb" src={c.cover} alt="" /></td>
                  <td>
                    <div className="name">{c.name}</div>
                    <div className="label" style={{ fontSize: 10, marginTop: 3 }}>{c.address} · {c.district}</div>
                  </td>
                  <td><div>{dev?.name}</div><div className="label" style={{ fontSize: 10 }}>БИН {dev?.bin}</div></td>
                  <td><TriMark v={c.basis.guarantee} /></td>
                  <td><TriMark v={c.basis.mio} /></td>
                  <td><TriMark v={c.basis.bvu} /></td>
                  <td><TriMark v={c.inPublishedList} label={c.inPublishedList === 'yes' ? 'В списке' : c.inPublishedList === 'no' ? 'Нет' : '—'} /></td>
                  <td><div className="row" style={{ gap: 8 }}><div className="progress"><b style={{ width: `${c.progress}%` }} /></div><span className="mono">{c.progress}%</span></div><div className="label" style={{ fontSize: 10, marginTop: 4 }}>{c.stage}</div></td>
                  <td className="mono">{new Date(c.plannedEnd).toLocaleDateString('ru-RU', { month: '2-digit', year: 'numeric' })}</td>
                  <td><Score value={r.score} zone={r.zone} /></td>
                  <td><div className="stack" style={{ gap: 6 }}><ZoneBadge zone={r.zone} />{r.needsCheck && <NeedsCheck />}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="grid g-4 fade-in" style={{ paddingTop: 24 }}>
            {rows.map(({ c, r, dev }) => (
              <div key={c.id} className="stack" style={{ gap: 10, cursor: 'pointer' }} onClick={() => nav(`/complexes/${c.id}`)}>
                <div className="ph-frame" style={{ aspectRatio: '4/5' }}>
                  <img className="img" src={c.cover} alt="" />
                  <div className="tag"><span className={`score solid z-${r.zone}`}>{r.score}</span></div>
                </div>
                <div className="row" style={{ justifyContent: 'space-between' }}><span className="label">{c.district}</span><ZoneBadge zone={r.zone} /></div>
                <div className="display d-m">{c.name}</div>
                <div className="muted" style={{ fontSize: 13 }}>{dev?.name}</div>
                <div className="row" style={{ gap: 8 }}><div className="progress" style={{ flex: 1 }}><b style={{ width: `${c.progress}%` }} /></div><span className="mono">{c.progress}%</span></div>
              </div>
            ))}
          </div>
        )}
        {rows.length === 0 && <div className="empty"><ArrowDownUp size={18} /><div>Нет объектов под эти фильтры</div></div>}
      </div>
    </div>
  )
}
