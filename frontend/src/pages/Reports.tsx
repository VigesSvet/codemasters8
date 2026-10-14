import { FileDown, Printer } from 'lucide-react'
import { PageHead, Score, ZoneBadge } from '../components/ui'
import { developers } from '../data/demo'
import type { Platform } from '../data/types'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { platformMeta } from '../components/ui'

export default function Reports() {
  const { complexes, ads, notify } = useStore()
  const scored = complexes.map((c) => ({ c, r: scoreComplex(c, ads) })).sort((a, b) => b.r.score - a.r.score)
  const byPlat = (Object.keys(platformMeta) as Platform[]).map((p) => ({ p, n: ads.filter((a) => a.platform === p).length })).filter((x) => x.n).sort((a, b) => b.n - a.n)
  const maxP = Math.max(...byPlat.map((x) => x.n))
  const weeks = [3, 5, 4, 7, 6, 9, 8, 12]

  return (
    <div className="page">
      <PageHead idx="M5" label="Отчётность" title={<>Еженедельная <span className="serif-i">сводка</span></>}
        aside={<><button className="btn ghost" onClick={() => window.print()}><Printer size={14} />Печать</button><button className="btn primary" onClick={() => notify('Отчёт сформирован (PDF, демо)')}><FileDown size={14} />Скачать PDF</button></>}>
        <div className="label" style={{ marginTop: 18 }}>Период: 07.10.2026 — 14.10.2026 · Усть-Каменогорск</div>
      </PageHead>

      <div className="grid g-2" style={{ gap: 48 }}>
        <div>
          <div className="label" style={{ marginBottom: 16 }}>Публикации с признаками продажи · по неделям</div>
          <div style={{ display: 'flex', alignItems: 'end', gap: 10, height: 220, borderBottom: '1px solid var(--line-strong)' }}>
            {weeks.map((v, i) => (
              <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
                <span className="mono" style={{ fontSize: 11 }}>{v}</span>
                <div style={{ width: '100%', height: v * 15, background: i === weeks.length - 1 ? 'var(--accent)' : 'var(--line)' }} />
              </div>
            ))}
          </div>
          <div className="row label" style={{ justifyContent: 'space-between', marginTop: 8 }}><span>нед. 34</span><span>нед. 41</span></div>
        </div>
        <div>
          <div className="label" style={{ marginBottom: 16 }}>По площадкам</div>
          {byPlat.map(({ p, n }) => (
            <div key={p} style={{ display: 'grid', gridTemplateColumns: '110px 1fr 30px', gap: 12, alignItems: 'center', padding: '8px 0' }}>
              <span className="mono" style={{ fontSize: 12 }}>{platformMeta[p].name}</span>
              <div style={{ height: 14, background: 'var(--bg-2)' }}><div style={{ width: `${(n / maxP) * 100}%`, height: '100%', background: platformMeta[p].color }} /></div>
              <span className="mono" style={{ textAlign: 'right' }}>{n}</span>
            </div>
          ))}
        </div>
      </div>

      <h2 className="display d-l" style={{ margin: '56px 0 16px' }}>Рейтинг <span className="serif-i">риска</span></h2>
      <table className="t" style={{ borderTop: '1px solid var(--line-strong)' }}>
        <thead><tr><th>#</th><th>ЖК</th><th>Застройщик</th><th>Факторы</th><th>Скор</th><th>Зона</th></tr></thead>
        <tbody>
          {scored.map(({ c, r }, i) => (
            <tr key={c.id}>
              <td className="mono">{String(i + 1).padStart(2, '0')}</td>
              <td className="name">{c.name}</td>
              <td>{developers.find((d) => d.id === c.developerId)?.name}</td>
              <td><div className="row" style={{ gap: 4 }}>{r.factors.filter((f) => f.state === 'hit').map((f) => <span key={f.code} className="chip" style={{ height: 20, fontSize: 10 }}>{f.code}</span>)}</div></td>
              <td><Score value={r.score} zone={r.zone} solid /></td>
              <td><ZoneBadge zone={r.zone} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
