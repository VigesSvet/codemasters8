import { useNavigate } from 'react-router-dom'
import {
  DataTable, Table, TableBody, TableCell, TableContainer, TableExpandHeader, TableExpandRow, TableExpandedRow, TableHead, TableHeader, TableRow,
} from '@carbon/react'
import { PageHeader, Score, ZoneTag, fmtDate } from '../components/ui'
import { developers } from '../data/demo'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'

export default function Developers() {
  const { complexes, ads } = useStore()
  const nav = useNavigate()
  const data = developers.map((d) => {
    const objs = complexes.filter((c) => c.developerId === d.id).map((c) => ({ c, r: scoreComplex(c, ads) }))
    const max = objs.reduce((m, x) => (!m || x.r.score > m.r.score ? x : m), objs[0])
    return { d, objs, max, adsCount: ads.filter((a) => objs.some((o) => o.c.id === a.complexId)).length }
  }).sort((a, b) => (b.max?.r.score ?? 0) - (a.max?.r.score ?? 0))
  const byId = Object.fromEntries(data.map((x) => [x.d.id, x]))
  const headers = [{ key: 'name', header: 'Застройщик' }, { key: 'bin', header: 'БИН' }, { key: 'reg', header: 'Регистрация' }, { key: 'objects', header: 'Объектов' }, { key: 'unfinished', header: 'Незавершённых' }, { key: 'ads', header: 'Публикаций' }, { key: 'risk', header: 'Макс. риск' }]
  const rows = data.map(({ d, max, adsCount }) => ({ id: d.id, name: d.name, bin: d.bin, reg: d.registered, objects: d.objects, unfinished: d.unfinished, ads: adsCount, risk: max?.r.score ?? 0 }))

  return (
    <>
      <PageHeader crumbs={[['SAQ', '/'], ['Застройщики']]} title="Застройщики" sub="Портфель объектов, история завершения и максимальный риск. БИН — для сверки с реестром юрлиц. Данные демонстрационные." />
      <DataTable rows={rows} headers={headers} isSortable>
        {({ rows: drows, headers: dh, getHeaderProps, getRowProps, getTableProps, getExpandHeaderProps }) => (
          <TableContainer>
            <Table {...getTableProps()} size="lg">
              <TableHead><TableRow><TableExpandHeader {...getExpandHeaderProps()} />{dh.map((h) => <TableHeader {...getHeaderProps({ header: h })} key={h.key}>{h.header}</TableHeader>)}</TableRow></TableHead>
              <TableBody>
                {drows.map((row) => {
                  const { d, objs, max, adsCount } = byId[row.id]
                  return [
                    <TableExpandRow {...getRowProps({ row })} key={row.id}>
                      <TableCell><span className="cell-title">{d.name}</span><div className="cell-sub">{d.director}</div></TableCell>
                      <TableCell className="mono">{d.bin}</TableCell>
                      <TableCell>{fmtDate(d.registered)}</TableCell>
                      <TableCell>{d.objects}</TableCell>
                      <TableCell style={{ color: d.unfinished ? 'var(--cds-support-error)' : undefined }}>{d.unfinished}</TableCell>
                      <TableCell>{adsCount}</TableCell>
                      <TableCell>{max && <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><Score value={max.r.score} zone={max.r.zone} /><ZoneTag zone={max.r.zone} size="sm" /></div>}</TableCell>
                    </TableExpandRow>,
                    <TableExpandedRow key={`${row.id}-x`} colSpan={dh.length + 1}>
                      <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', padding: '8px 0' }}>
                        {objs.map(({ c, r }) => (
                          <div key={c.id} className="pcard" onClick={() => nav(`/complexes/${c.id}`)}>
                            <div className="ph" style={{ aspectRatio: '16/9' }}><img src={c.cover} alt="" /><Score value={r.score} zone={r.zone} /></div>
                            <div className="b"><div className="n">ЖК «{c.name}»</div><div className="a">{c.stage} · {c.progress}% · {c.district}</div></div>
                          </div>
                        ))}
                      </div>
                    </TableExpandedRow>,
                  ]
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </DataTable>
    </>
  )
}
