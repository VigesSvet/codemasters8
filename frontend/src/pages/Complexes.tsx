import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Button, ContentSwitcher, DataTable, IconSwitch, Pagination, Table, TableBody, TableCell, TableContainer, TableHead, TableHeader, TableRow,
  TableToolbar, TableToolbarContent, TableToolbarSearch,
} from '@carbon/react'
import { Add, Download, Grid, List } from '@carbon/icons-react'
import FilterBar, { type Applied, type FilterDef } from '../components/FilterBar'
import { NeedsCheck, PageHeader, Score, TriTag, ZoneTag, fmtDate } from '../components/ui'
import { developers } from '../data/demo'
import type { Stage } from '../data/types'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'

const STAGES: Stage[] = ['котлован', 'фундамент', 'каркас', 'кровля', 'отделка', 'введён']

export default function Complexes() {
  const { complexes, ads } = useStore()
  const nav = useNavigate()
  const [view, setView] = useState(0)
  const [q, setQ] = useState('')
  const [applied, setApplied] = useState<Applied[]>([])
  const [page, setPage] = useState({ page: 1, size: 25 })

  const defs: FilterDef[] = [
    { key: 'zone', label: 'Зона', options: [{ value: 'red', label: 'Красная' }, { value: 'yellow', label: 'Жёлтая' }, { value: 'green', label: 'Зелёная' }] },
    { key: 'district', label: 'Район', options: [...new Set(complexes.map((c) => c.district))].map((d) => ({ value: d, label: d })) },
    { key: 'stage', label: 'Стадия', options: STAGES.map((s) => ({ value: s, label: s })) },
    { key: 'guarantee', label: 'Гарантия ЕО', options: [{ value: 'yes', label: 'Есть' }, { value: 'no', label: 'Нет' }, { value: 'unknown', label: 'Нет сведений' }] },
    { key: 'check', label: 'Проверка', options: [{ value: 'need', label: 'Нужна проверка' }] },
  ]

  const all = useMemo(() => complexes.map((c) => ({ c, r: scoreComplex(c, ads), dev: developers.find((d) => d.id === c.developerId) })).sort((a, b) => b.r.score - a.r.score), [complexes, ads])
  const rows = all.filter(({ c, r, dev }) => {
    if (q && !`${c.name} ${c.address} ${dev?.name} ${dev?.bin}`.toLowerCase().includes(q.toLowerCase())) return false
    return applied.every((a) => {
      const v = a.key === 'zone' ? r.zone : a.key === 'district' ? c.district : a.key === 'stage' ? c.stage : a.key === 'guarantee' ? c.basis.guarantee : r.needsCheck ? 'need' : ''
      return a.values.includes(v)
    })
  })
  const byId = Object.fromEntries(rows.map((x) => [x.c.id, x]))
  const headers = [
    { key: 'name', header: 'Объект' }, { key: 'dev', header: 'Застройщик' }, { key: 'guarantee', header: 'Основания' }, { key: 'progress', header: 'Готовность' }, { key: 'end', header: 'Сдача' },
    { key: 'score', header: 'Риск' }, { key: 'zone', header: 'Зона' },
  ]
  const pageRows = rows.slice((page.page - 1) * page.size, page.page * page.size)
  const tableRows = pageRows.map(({ c, r }) => ({ id: c.id, name: c.name, dev: c.developerId, guarantee: c.basis.guarantee, mio: c.basis.mio, bvu: c.basis.bvu, list: c.inPublishedList, progress: c.progress, end: c.plannedEnd, score: r.score, zone: r.zone }))

  return (
    <>
      <PageHeader crumbs={[['SAQ', '/'], ['Реестр ЖК']]} title="Реестр жилых комплексов"
        sub={`Усть-Каменогорск · ${complexes.length} объектов · основания сверяются с перечнем ГАСК и картотекой. Отсутствие сведений не считается нарушением.`} />

      <FilterBar defs={defs} applied={applied} onChange={(a) => { setApplied(a); setPage((p) => ({ ...p, page: 1 })) }}
        right={<>
          <span className="label-01">{rows.length} из {complexes.length}</span>
          <ContentSwitcher size="sm" selectedIndex={view} onChange={({ index }) => setView(index ?? 0)} style={{ width: 'auto' }}>
            <IconSwitch name="table" text="Таблица"><List /></IconSwitch>
            <IconSwitch name="grid" text="Карточки"><Grid /></IconSwitch>
          </ContentSwitcher>
        </>} />

      {view === 0 ? (
        <DataTable rows={tableRows} headers={headers} isSortable>
          {({ rows: drows, headers: dheaders, getHeaderProps, getRowProps, getTableProps, getTableContainerProps }) => (
            <TableContainer {...getTableContainerProps()}>
              <TableToolbar>
                <TableToolbarContent>
                  <TableToolbarSearch persistent placeholder="Поиск: ЖК, адрес, застройщик, БИН" onChange={(e) => setQ(typeof e === 'string' ? '' : (e.target as HTMLInputElement).value)} />
                  <Button kind="ghost" renderIcon={Download}>Экспорт XLSX</Button>
                  <Button renderIcon={Add}>Добавить ЖК</Button>
                </TableToolbarContent>
              </TableToolbar>
              <Table {...getTableProps()} size="lg">
                <TableHead>
                  <TableRow>{dheaders.map((h) => <TableHeader {...getHeaderProps({ header: h })} key={h.key}>{h.header}</TableHeader>)}</TableRow>
                </TableHead>
                <TableBody>
                  {drows.map((row) => {
                    const { c, r, dev } = byId[row.id]
                    return (
                      <TableRow {...getRowProps({ row })} key={row.id} onClick={() => nav(`/complexes/${c.id}`)} style={{ cursor: 'pointer' }}>
                        <TableCell className="col-obj">
                          <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                            <img className="thumb" src={c.cover} alt="" />
                            <div><div className="cell-title">ЖК «{c.name}»</div><div className="cell-sub">{c.address} · {c.district}</div></div>
                          </div>
                        </TableCell>
                        <TableCell><div>{dev?.name}</div><div className="cell-sub mono">БИН {dev?.bin}</div></TableCell>
                        <TableCell>
                          <div className="basis-dots">
                            <span title="Гарантия ЕО"><i className={c.basis.guarantee} />ЕО</span>
                            <span title="Разрешение МИО"><i className={c.basis.mio} />МИО</span>
                            <span title="Банк второго уровня"><i className={c.basis.bvu} />БВУ</span>
                            <span title="Перечень ГАСК"><i className={c.inPublishedList} />ГАСК</span>
                          </div>
                        </TableCell>
                        <TableCell><div className="progress"><span className="bar"><b style={{ width: `${c.progress}%` }} /></span><span className="mono">{c.progress}%</span></div><div className="cell-sub">{c.stage}</div></TableCell>
                        <TableCell className="mono">{fmtDate(c.plannedEnd)}</TableCell>
                        <TableCell><Score value={r.score} zone={r.zone} /></TableCell>
                        <TableCell><div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-start' }}><ZoneTag zone={r.zone} size="sm" />{r.needsCheck && <NeedsCheck />}</div></TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
              <Pagination page={page.page} pageSize={page.size} pageSizes={[10, 25, 50]} totalItems={rows.length} onChange={({ page: p, pageSize }) => setPage({ page: p, size: pageSize })}
                itemsPerPageText="Строк на странице:" itemRangeText={(min, max, total) => `${min}–${max} из ${total}`} pageRangeText={(_c, total) => `из ${total}`} backwardText="Назад" forwardText="Вперёд" />
            </TableContainer>
          )}
        </DataTable>
      ) : (
        <div className="page">
          <div className="cards">
            {rows.map(({ c, r, dev }) => (
              <div key={c.id} className="pcard" onClick={() => nav(`/complexes/${c.id}`)}>
                <div className="ph">
                  <img src={c.cover} alt="" />
                  <div className="tags"><ZoneTag zone={r.zone} size="sm" /></div>
                  <Score value={r.score} zone={r.zone} />
                </div>
                <div className="b">
                  <div className="n">ЖК «{c.name}»</div>
                  <div className="a">{c.address}</div>
                  <div className="a">{c.district} · {c.stage} · сдача {new Date(c.plannedEnd).getFullYear()}</div>
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 8 }}>
                    <TriTag v={c.basis.guarantee} label={`Гарантия ЕО: ${c.basis.guarantee === 'yes' ? 'есть' : c.basis.guarantee === 'no' ? 'нет' : '?'}`} />
                    {r.needsCheck && <NeedsCheck />}
                  </div>
                  <div className="dev">{dev?.name}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  )
}
