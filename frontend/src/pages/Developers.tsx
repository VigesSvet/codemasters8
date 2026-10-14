import { useState } from 'react'
import { Cell, Modal, PageTitle, Score, ZoneTag, fmtDate } from '../components/ui'
import { developers } from '../data/demo'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcUsers } from '../ui/icons'

export default function Developers() {
  const { complexes, ads } = useStore()
  const [open, setOpen] = useState<string | null>(null)
  const data = developers.map((d) => {
    const objs = complexes.filter((c) => c.developerId === d.id).map((c) => ({ c, r: scoreComplex(c, ads) }))
    const max = objs.reduce<(typeof objs)[number] | undefined>((m, x) => (!m || x.r.score > m.r.score ? x : m), undefined)
    return { d, objs, max, adsCount: ads.filter((a) => objs.some((o) => o.c.id === a.complexId)).length }
  }).sort((a, b) => (b.max?.r.score ?? 0) - (a.max?.r.score ?? 0))
  const cur = data.find((x) => x.d.id === open)

  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Застройщики']]} title="Застройщики" sub="Портфель объектов, история завершения и максимальный риск. БИН — для сверки с реестром юрлиц. Данные демонстрационные." />
      <div className="card">
        {data.map(({ d, objs, max, adsCount }) => (
          <button key={d.id} className="cell" onClick={() => setOpen(d.id)} style={{ display: 'grid', gridTemplateColumns: '48px minmax(0,1.5fr) repeat(3, 110px) 300px', gap: 16, minHeight: 84 }}>
            <span className="ico" style={{ width: 48, height: 48, borderRadius: 14 }}><IcUsers size={22} /></span>
            <div style={{ minWidth: 0 }}><div className="ttl bold">{d.name}</div><div className="sub num">БИН {d.bin} · {d.director}</div></div>
            <div><div className="t-xs c2">Объектов</div><div className="t-h3 num">{d.objects}</div></div>
            <div><div className="t-xs c2">Не завершены</div><div className="t-h3 num" style={{ color: d.unfinished ? 'var(--red)' : undefined }}>{d.unfinished}</div></div>
            <div><div className="t-xs c2">Публикаций</div><div className="t-h3 num">{adsCount}</div></div>
            <div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}>{max && <><ZoneTag zone={max.r.zone} /><Score value={max.r.score} zone={max.r.zone} /></>}<span className="c3 t-xs" style={{ whiteSpace: 'nowrap' }}>{objs.length} на контроле</span></div>
          </button>
        ))}
      </div>

      {cur && (
        <Modal title={cur.d.name.replace('ТОО ', '')} onClose={() => setOpen(null)}>
          {[['БИН', cur.d.bin], ['Руководитель', cur.d.director], ['Регистрация', fmtDate(cur.d.registered)], ['Сайт', cur.d.site ?? '—'], ['Instagram', cur.d.instagram ?? '—'], ['Незавершённых объектов', String(cur.d.unfinished)]].map(([k, v]) => (
            <Cell key={k} title={<span className="c2 t-sm">{k}</span>} end={<span style={{ color: 'var(--text)' }}>{v}</span>} />
          ))}
          <div className="card-head" style={{ paddingTop: 16 }}><h3>Объекты на контроле</h3></div>
          {cur.objs.map(({ c, r }) => (
            <Cell key={c.id} inset to={`/complexes/${c.id}`} icon={<img className="th" src={c.cover} alt="" style={{ width: 48, height: 48, borderRadius: 12 }} />} title={`ЖК «${c.name}»`} sub={`${c.stage} · ${c.progress}%`} end={<Score value={r.score} zone={r.zone} />} chevron />
          ))}
        </Modal>
      )}
    </>
  )
}
