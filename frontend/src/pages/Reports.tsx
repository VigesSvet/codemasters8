import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PageTitle, Score, Seg, ZoneTag } from '../components/ui'
import { developers } from '../data/demo'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcDownload } from '../ui/icons'

const WEEKS = [3, 5, 4, 7, 6, 9, 8, 12]

export default function Reports() {
  const nav = useNavigate()
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
          <div key={k} className="box stat"><span className="t-sm c2">{k}</span><span className="v">{v}</span></div>
        ))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 16, alignItems: 'start' }}>
        <div className="box">
          <h3 className="t-h3" style={{ marginBottom: 20 }}>Публикации с признаками продажи</h3>
          <div className="bars">{WEEKS.map((v, i) => <div key={i}><span className="num">{v}</span><b style={{ height: v * 11 }} /><span className="t-xs c3">н{34 + i}</span></div>)}</div>
        </div>
        <div>
          <h3 className="t-h3" style={{ marginBottom: 4 }}>Рейтинг риска</h3>
          <table className="ytable">
            <thead><tr><th>#</th><th>Жилой комплекс</th><th>Факторы</th><th className="r">Балл</th></tr></thead>
            <tbody>
              {scored.map(({ c, r }, i) => (
                <tr key={c.id} onClick={() => nav(`/complexes/${c.id}`)}>
                  <td className="c3 num">{i + 1}</td>
                  <td><span className="link">ЖК «{c.name}»</span><div className="t-sm c2">{developers.find((d) => d.id === c.developerId)?.name}</div></td>
                  <td className="t-sm c2">{r.factors.filter((f) => f.state === 'hit').map((f) => f.code).join(' · ') || '—'}</td>
                  <td className="r"><div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}><ZoneTag zone={r.zone} /><Score value={r.score} zone={r.zone} /></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}
