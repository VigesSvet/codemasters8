import { useState } from 'react'
import { Cell, PageTitle, Score, Seg, ZoneTag } from '../components/ui'
import { developers } from '../data/demo'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcDownload } from '../ui/icons'

const WEEKS = [3, 5, 4, 7, 6, 9, 8, 12]

export default function Reports() {
  const { complexes, ads, notify } = useStore()
  const [period, setPeriod] = useState<'w' | 'm' | 'q'>('w')
  const scored = complexes.map((c) => ({ c, r: scoreComplex(c, ads) })).sort((a, b) => b.r.score - a.r.score)
  const confirmed = ads.filter((a) => a.status === 'confirmed').length
  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Отчёты']]} title="Сводка для руководства" sub="Период: 7–14 октября 2026 · Усть-Каменогорск"
        actions={<><Seg value={period} onChange={setPeriod} items={[['w', 'Неделя'], ['m', 'Месяц'], ['q', 'Квартал']]} /><button className="btn y m" onClick={() => notify('Отчёт сформирован (PDF, демо)')}><IcDownload size={20} />Скачать PDF</button></>} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 16 }}>
        {[['Новых публикаций', ads.length], ['Подтверждено нарушений', confirmed], ['Объектов в красной зоне', scored.filter((x) => x.r.zone === 'red').length], ['Среднее время решения', '1,4 дн.']].map(([k, v]) => (
          <div key={k} className="card stat"><span className="t-sm c2">{k}</span><span className="v">{v}</span></div>
        ))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 16, alignItems: 'start' }}>
        <div className="card card-pad">
          <h3 className="t-h3" style={{ marginBottom: 20 }}>Публикации с признаками продажи</h3>
          <div className="bars">{WEEKS.map((v, i) => <div key={i}><span className="num">{v}</span><b style={{ height: v * 11 }} /><span className="t-xs c3">н{34 + i}</span></div>)}</div>
        </div>
        <div className="card">
          <div className="card-head"><h3>Рейтинг риска</h3></div>
          {scored.map(({ c, r }, i) => (
            <Cell key={c.id} to={`/complexes/${c.id}`} icon={<span className="t-sm c3 num" style={{ width: 20 }}>{i + 1}</span>}
              title={`ЖК «${c.name}»`} sub={developers.find((d) => d.id === c.developerId)?.name}
              end={<><span className="t-xs c3">{r.factors.filter((f) => f.state === 'hit').map((f) => f.code).join(' ')}</span><ZoneTag zone={r.zone} /><Score value={r.score} zone={r.zone} /></>} />
          ))}
        </div>
      </div>
    </>
  )
}
