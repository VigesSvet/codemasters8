import { Button, StructuredListBody, StructuredListCell, StructuredListHead, StructuredListRow, StructuredListWrapper, Tag, Tile } from '@carbon/react'
import { DocumentPdf, Printer } from '@carbon/icons-react'
import { PageHeader, Score, ZoneTag, platformMeta } from '../components/ui'
import { developers } from '../data/demo'
import type { Platform } from '../data/types'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'

const WEEKS = [3, 5, 4, 7, 6, 9, 8, 12]

export default function Reports() {
  const { complexes, ads, notify } = useStore()
  const scored = complexes.map((c) => ({ c, r: scoreComplex(c, ads) })).sort((a, b) => b.r.score - a.r.score)
  const byPlat = (Object.keys(platformMeta) as Platform[]).map((p) => ({ p, n: ads.filter((a) => a.platform === p).length })).filter((x) => x.n).sort((a, b) => b.n - a.n)
  const maxP = Math.max(...byPlat.map((x) => x.n))
  return (
    <>
      <PageHeader crumbs={[['SAQ', '/'], ['Отчёты']]} title="Еженедельная сводка" sub="Период 07.10.2026 — 14.10.2026 · Усть-Каменогорск"
        actions={<><Button kind="tertiary" renderIcon={Printer} onClick={() => window.print()}>Печать</Button><Button renderIcon={DocumentPdf} onClick={() => notify('Отчёт сформирован (PDF, демо)', 'info')}>Скачать PDF</Button></>} />
      <div className="page">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <Tile>
            <div className="label-01" style={{ marginBottom: 16 }}>Публикации с признаками продажи, по неделям</div>
            <div className="bars">{WEEKS.map((v, i) => <div key={i}><span>{v}</span><b style={{ height: v * 13, opacity: i === WEEKS.length - 1 ? 1 : 0.45 }} /></div>)}</div>
            <div className="label-01" style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }}><span>нед. 34</span><span>нед. 41</span></div>
          </Tile>
          <Tile>
            <div className="label-01" style={{ marginBottom: 16 }}>По площадкам</div>
            {byPlat.map(({ p, n }) => (
              <div key={p} style={{ display: 'grid', gridTemplateColumns: '100px 1fr 30px', gap: 12, alignItems: 'center', padding: '6px 0' }}>
                <span>{platformMeta[p].name}</span>
                <div style={{ height: 16, background: 'var(--cds-layer-02)' }}><div style={{ width: `${(n / maxP) * 100}%`, height: '100%', background: 'var(--cds-interactive)' }} /></div>
                <span className="mono" style={{ textAlign: 'right' }}>{n}</span>
              </div>
            ))}
          </Tile>
        </div>
        <h2 className="section-title">Рейтинг риска</h2>
        <StructuredListWrapper aria-label="Рейтинг">
          <StructuredListHead><StructuredListRow head>{['#', 'ЖК', 'Застройщик', 'Факторы', 'Балл', 'Зона'].map((h) => <StructuredListCell head key={h}>{h}</StructuredListCell>)}</StructuredListRow></StructuredListHead>
          <StructuredListBody>
            {scored.map(({ c, r }, i) => (
              <StructuredListRow key={c.id}>
                <StructuredListCell className="mono">{String(i + 1).padStart(2, '0')}</StructuredListCell>
                <StructuredListCell noWrap>ЖК «{c.name}»</StructuredListCell>
                <StructuredListCell>{developers.find((d) => d.id === c.developerId)?.name}</StructuredListCell>
                <StructuredListCell>{r.factors.filter((f) => f.state === 'hit').map((f) => <Tag key={f.code} size="sm" type="outline">{f.code}</Tag>)}</StructuredListCell>
                <StructuredListCell><Score value={r.score} zone={r.zone} /></StructuredListCell>
                <StructuredListCell><ZoneTag zone={r.zone} size="sm" /></StructuredListCell>
              </StructuredListRow>
            ))}
          </StructuredListBody>
        </StructuredListWrapper>
      </div>
    </>
  )
}
