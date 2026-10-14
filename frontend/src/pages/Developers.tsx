import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, X } from 'lucide-react'
import { PageHead, Score, ZoneBadge, fmtDate } from '../components/ui'
import { developers } from '../data/demo'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'

export default function Developers() {
  const { complexes, ads } = useStore()
  const [open, setOpen] = useState<string | null>(null)
  const rows = developers.map((d) => {
    const objs = complexes.filter((c) => c.developerId === d.id).map((c) => ({ c, r: scoreComplex(c, ads) }))
    const max = objs.reduce((m, x) => (x.r.score > m.r.score ? x : m), objs[0])
    return { d, objs, max, adsCount: ads.filter((a) => objs.some((o) => o.c.id === a.complexId)).length }
  }).sort((a, b) => (b.max?.r.score ?? 0) - (a.max?.r.score ?? 0))
  const cur = rows.find((x) => x.d.id === open)

  return (
    <div className="page">
      <PageHead idx="05" label="Профили" title={<>Застрой<span className="serif-i">щики</span></>}>
        <p className="ink2" style={{ maxWidth: 620, marginTop: 18 }}>Сводка по застройщикам: объекты, история завершения, максимальный риск по портфелю. БИН и реквизиты — для сверки с реестром юрлиц.</p>
      </PageHead>
      <div style={{ borderTop: '1px solid var(--line-strong)' }}>
        {rows.map(({ d, objs, max, adsCount }, i) => (
          <div key={d.id} onClick={() => setOpen(d.id)} style={{ display: 'grid', gridTemplateColumns: '60px 1.6fr 1fr 1fr 1fr 140px', gap: 20, alignItems: 'center', padding: '26px 0', borderBottom: '1px solid var(--line)', cursor: 'pointer' }}>
            <span className="label">{String(i + 1).padStart(2, '0')}</span>
            <div><div className="display d-m">{d.name.replace('ТОО ', '')}</div><div className="label" style={{ marginTop: 6 }}>БИН {d.bin} · с {new Date(d.registered).getFullYear()}</div></div>
            <div><div className="label">Объекты</div><div className="num" style={{ fontSize: 30 }}>{d.objects}<span className="muted" style={{ fontSize: 14 }}> · {objs.length} на контроле</span></div></div>
            <div><div className="label">Незавершённые</div><div className="num" style={{ fontSize: 30, color: d.unfinished ? 'var(--red)' : undefined }}>{d.unfinished}</div></div>
            <div><div className="label">Публикаций</div><div className="num" style={{ fontSize: 30 }}>{adsCount}</div></div>
            <div className="row" style={{ justifyContent: 'flex-end' }}>{max && <><ZoneBadge zone={max.r.zone} /><Score value={max.r.score} zone={max.r.zone} solid /></>}</div>
          </div>
        ))}
      </div>

      {cur && (
        <>
          <div className="scrim" onClick={() => setOpen(null)} />
          <div className="drawer">
            <div style={{ padding: 28 }} className="stack">
              <div className="row" style={{ justifyContent: 'space-between' }}><span className="label">Профиль застройщика</span><button className="btn icon sm ghost" style={{ width: 28 }} onClick={() => setOpen(null)}><X size={14} /></button></div>
              <h2 className="display d-l">{cur.d.name}</h2>
              <div className="grid g-2" style={{ gap: 0 }}>
                {[['БИН', cur.d.bin], ['Руководитель', cur.d.director], ['Регистрация', fmtDate(cur.d.registered)], ['Сайт', cur.d.site ?? '—'], ['Instagram', cur.d.instagram ?? '—'], ['Незавершённых', cur.d.unfinished]].map(([k, v]) => (
                  <div className="cap" key={String(k)} style={{ paddingRight: 12 }}><span className="label">{k}</span><span>{v}</span></div>
                ))}
              </div>
              <div className="label" style={{ marginTop: 18 }}>Объекты на контроле</div>
              {cur.objs.map(({ c, r }) => (
                <Link key={c.id} to={`/complexes/${c.id}`} className="row" style={{ padding: '10px 0', borderBottom: '1px solid var(--line)' }}>
                  <img src={c.cover} alt="" style={{ width: 80, height: 56, objectFit: 'cover', filter: 'var(--img-filter)' }} />
                  <div style={{ flex: 1 }}><div style={{ fontWeight: 500 }}>{c.name}</div><div className="label" style={{ fontSize: 10 }}>{c.stage} · {c.progress}%</div></div>
                  <Score value={r.score} zone={r.zone} solid /><ArrowUpRight size={14} />
                </Link>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
