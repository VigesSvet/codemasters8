import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Cell, Modal, NeedsCheck, PageTitle, Score, Seg, Toggle, ZoneTag, fmtDate } from '../components/ui'
import { developers } from '../data/demo'
import type { Stage, Tri, Zone } from '../data/types'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcBuilding, IcCheck, IcDownload, IcGrid, IcHelp, IcList, IcMap, IcPlus, IcSearch, IcClose, IcCrane } from '../ui/icons'

const STAGES: Stage[] = ['котлован', 'фундамент', 'каркас', 'кровля', 'отделка', 'введён']
const triCls = (v: Tri) => (v === 'yes' ? 'green' : v === 'no' ? 'red' : 'gray')

export default function Complexes() {
  const { complexes, ads, notify } = useStore()
  const nav = useNavigate()
  const [sp] = useSearchParams()
  const [view, setView] = useState<'list' | 'grid'>('list')
  const [q, setQ] = useState('')
  const [zone, setZone] = useState<Zone | 'all'>((sp.get('zone') as Zone) || 'all')
  const [district, setDistrict] = useState('')
  const [stage, setStage] = useState<Stage | ''>('')
  const [onlyCheck, setOnlyCheck] = useState(false)
  const [noList, setNoList] = useState(false)
  const [modal, setModal] = useState<'district' | 'stage' | null>(null)

  const all = useMemo(() => complexes.map((c) => ({ c, r: scoreComplex(c, ads), dev: developers.find((d) => d.id === c.developerId) })).sort((a, b) => b.r.score - a.r.score), [complexes, ads])
  const rows = all.filter(({ c, r, dev }) =>
    (!q || `${c.name} ${c.address} ${dev?.name} ${dev?.bin}`.toLowerCase().includes(q.toLowerCase()))
    && (zone === 'all' || r.zone === zone) && (!district || c.district === district) && (!stage || c.stage === stage)
    && (!onlyCheck || r.needsCheck) && (!noList || c.inPublishedList === 'no'))
  const districts = [...new Set(complexes.map((c) => c.district))]
  const cnt = (z: Zone | 'all') => all.filter((x) => z === 'all' || x.r.zone === z).length
  const dirty = zone !== 'all' || district || stage || onlyCheck || noList || q

  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Реестр ЖК']]} title="Реестр жилых комплексов"
        sub="Все строящиеся и недавно сданные объекты Усть-Каменогорска. Основания сверяются с перечнем ГАСК и картотекой — отсутствие сведений не считается нарушением."
        actions={<><button className="btn g m" onClick={() => notify('Выгрузка XLSX сформирована')}><IcDownload size={20} />Экспорт</button><button className="btn y m"><IcPlus size={20} />Добавить ЖК</button></>} />

      <div className="cols">
        <div className="stack sticky">
          <div className="card">
            <div className="field">
              <span className="mk c2"><IcSearch /></span>
              <div className="body"><input placeholder="ЖК, адрес, застройщик, БИН" value={q} onChange={(e) => setQ(e.target.value)} /></div>
              {q && <button className="c3" onClick={() => setQ('')}><IcClose size={18} /></button>}
            </div>
          </div>
          <div className="card">
            <div className="tiles" style={{ gridTemplateColumns: '1fr 1fr' }}>
              {(['all', 'red', 'yellow', 'green'] as const).map((z) => (
                <button key={z} className={`tile ${zone === z ? 'on' : ''}`} onClick={() => setZone(z)} style={{ minHeight: 84 }}>
                  <span className="pic" style={{ height: 30 }}><span className={`zone-pic ${z}`} style={{ width: 30, height: 30, borderRadius: 10 }}><IcBuilding size={18} /></span></span>
                  <span className="nm">{z === 'all' ? 'Все' : z === 'red' ? 'Красная' : z === 'yellow' ? 'Жёлтая' : 'Зелёная'}</span>
                  <span className="vl num">{cnt(z)}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="card">
            <Cell icon={<span className="ico"><IcMap size={20} /></span>} title="Район" end={district || 'любой'} chevron onClick={() => setModal('district')} inset />
            <Cell icon={<span className="ico"><IcCrane size={20} /></span>} title="Стадия" end={stage || 'любая'} chevron onClick={() => setModal('stage')} inset />
          </div>
          <div className="card">
            <Cell title="Нужна проверка" sub="Есть факторы без сведений" end={<Toggle on={onlyCheck} onChange={setOnlyCheck} />} />
            <Cell title="Нет в перечне ГАСК" sub="Само по себе не нарушение" end={<Toggle on={noList} onChange={setNoList} />} />
          </div>
          {dirty && <button className="btn g full" onClick={() => { setZone('all'); setDistrict(''); setStage(''); setOnlyCheck(false); setNoList(false); setQ('') }}>Сбросить фильтры</button>}
        </div>

        <div>
          <div className="row between" style={{ padding: '4px 4px 12px' }}>
            <span className="t-h3">{rows.length} {rows.length === 1 ? 'объект' : rows.length < 5 ? 'объекта' : 'объектов'}</span>
            <Seg value={view} onChange={setView} items={[['list', <><IcList size={18} />Список</>], ['grid', <><IcGrid size={18} />Карточки</>]]} />
          </div>
          {view === 'list' ? (
            <div className="card fade">
              {rows.length === 0 && <div className="empty">Нет объектов под эти фильтры</div>}
              {rows.map(({ c, r, dev }) => (
                <button key={c.id} className="cell" onClick={() => nav(`/complexes/${c.id}`)} style={{ display: 'grid', gridTemplateColumns: '72px minmax(0,1.4fr) minmax(0,1fr) 150px 64px', gap: 18, minHeight: 96 }}>
                  <img className="th" src={c.cover} alt="" style={{ width: 72, height: 72, borderRadius: 16 }} />
                  <div style={{ minWidth: 0 }}>
                    <div className="ttl bold">ЖК «{c.name}»</div>
                    <div className="sub">{c.address} · {c.district}</div>
                    <div className="row wrap-f" style={{ gap: 6, marginTop: 8 }}>
                      <ZoneTag zone={r.zone} />
                      {r.needsCheck && <NeedsCheck />}
                    </div>
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div className="t-sm" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{dev?.name}</div>
                    <div className="t-xs c3 num">БИН {dev?.bin}</div>
                    <div className="row wrap-f" style={{ gap: 4, marginTop: 8 }}>
                      {([['ЕО', c.basis.guarantee], ['МИО', c.basis.mio], ['БВУ', c.basis.bvu], ['ГАСК', c.inPublishedList]] as const).map(([l, v]) => (
                        <span key={l} className={`tag ${triCls(v)}`} title={l} style={{ height: 22, padding: '0 7px' }}>{v === 'yes' ? <IcCheck size={12} strokeWidth={3} /> : v === 'no' ? <IcClose size={12} strokeWidth={3} /> : <IcHelp size={12} />}{l}</span>
                      ))}
                    </div>
                  </div>
                  <div>
                    <div className="t-sm">{c.stage} · <span className="num">{c.progress}%</span></div>
                    <div className="hbar" style={{ margin: '8px 0 6px', height: 6 }}><i style={{ width: `${c.progress}%`, background: 'var(--text)' }} /></div>
                    <div className="t-xs c3">сдача {fmtDate(c.plannedEnd)}</div>
                  </div>
                  <div style={{ justifySelf: 'end' }}><Score value={r.score} zone={r.zone} /></div>
                </button>
              ))}
            </div>
          ) : (
            <div className="gcards fade">
              {rows.map(({ c, r, dev }) => (
                <div key={c.id} className="gcard" onClick={() => nav(`/complexes/${c.id}`)}>
                  <div className="ph"><img src={c.cover} alt="" /><div className="tl"><span className="tag dark">{c.stage}</span></div><div className="tr"><Score value={r.score} zone={r.zone} /></div></div>
                  <div className="b">
                    <div className="t-h3">ЖК «{c.name}»</div>
                    <div className="t-sm c2">{c.address}</div>
                    <div className="row wrap-f" style={{ gap: 6, marginTop: 6 }}><ZoneTag zone={r.zone} />{r.needsCheck && <NeedsCheck />}</div>
                    <div className="t-xs c3" style={{ marginTop: 'auto', paddingTop: 10 }}>{dev?.name}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {modal && (
        <Modal title={modal === 'district' ? 'Район' : 'Стадия строительства'} onClose={() => setModal(null)}>
          {[''].concat(modal === 'district' ? districts : STAGES).map((v) => {
            const cur = modal === 'district' ? district : stage
            return (
              <Cell key={v || 'any'} title={v || (modal === 'district' ? 'Любой район' : 'Любая стадия')}
                end={cur === v ? <IcCheck size={22} strokeWidth={2.5} /> : undefined}
                onClick={() => { if (modal === 'district') setDistrict(v); else setStage(v as Stage); setModal(null) }} />
            )
          })}
        </Modal>
      )}
    </>
  )
}
