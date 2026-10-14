import { useState } from 'react'
import { Cell, Modal, PageTitle, Score, ZoneTag, fmtDate } from '../components/ui'
import { developers } from '../data/demo'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'

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
      <table className="ytable">
        <thead><tr><th>Застройщик</th><th>БИН</th><th className="r">Объектов</th><th className="r">Не завершены</th><th className="r">Публикаций</th><th>Макс. риск</th><th /></tr></thead>
        <tbody>
          {data.map(({ d, objs, max, adsCount }) => (
            <tr key={d.id} onClick={() => setOpen(d.id)}>
              <td><span className="link">{d.name}</span><div className="t-sm c2" style={{ marginTop: 2 }}>{d.director} · {objs.length} на контроле</div></td>
              <td className="num c2">{d.bin}</td>
              <td className="r b">{d.objects}</td>
              <td className="r b" style={{ color: d.unfinished ? 'var(--red)' : undefined }}>{d.unfinished}</td>
              <td className="r b">{adsCount}</td>
              <td>{max && <div className="row" style={{ gap: 8 }}><Score value={max.r.score} zone={max.r.zone} /><ZoneTag zone={max.r.zone} /></div>}</td>
              <td className="r"><span className="btn g m">Профиль</span></td>
            </tr>
          ))}
        </tbody>
      </table>

      {cur && (
        <Modal title={cur.d.name.replace('ТОО ', '')} onClose={() => setOpen(null)}>
          {[['БИН', cur.d.bin], ['Руководитель', cur.d.director], ['Регистрация', fmtDate(cur.d.registered)], ['Сайт', cur.d.site ?? '—'], ['Instagram', cur.d.instagram ?? '—'], ['Незавершённых объектов', String(cur.d.unfinished)]].map(([k, v]) => (
            <Cell key={k} title={<span className="c2 t-sm">{k}</span>} end={<span style={{ color: 'var(--text)' }}>{v}</span>} />
          ))}
          <div className="t-h3" style={{ padding: '20px 24px 4px' }}>Объекты на контроле</div>
          {cur.objs.map(({ c, r }) => (
            <Cell key={c.id} inset to={`/complexes/${c.id}`} icon={<img className="th" src={c.cover} alt="" style={{ width: 48, height: 48, borderRadius: 12 }} />} title={`ЖК «${c.name}»`} sub={`${c.stage} · ${c.progress}%`} end={<Score value={r.score} zone={r.zone} />} chevron />
          ))}
        </Modal>
      )}
    </>
  )
}
