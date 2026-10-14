import { Button, DataTable, Table, TableBody, TableCell, TableContainer, TableHead, TableHeader, TableRow, Tag, Toggle } from '@carbon/react'
import { Renew } from '@carbon/icons-react'
import { PageHeader } from '../components/ui'
import { publishedListMeta } from '../data/demo'
import { useStore } from '../lib/store'

type St = 'on' | 'demo' | 'plan'
const SRC: { id: string; name: string; kind: string; st: St; note: string; req?: boolean }[] = [
  { id: 's1', name: 'Ручной ввод аналитика', kind: 'Публикации', st: 'on', note: 'Ссылка, текст, скриншот + OCR', req: true },
  { id: 's2', name: 'Локальный набор данных (CSV/XLSX)', kind: 'ЖК, застройщики', st: 'on', note: 'Импорт картотеки, демо-набор 14 ЖК', req: true },
  { id: 's3', name: 'Перечень ГАСК (Instagram)', kind: 'Разрешённые ЖК', st: 'demo', note: `Обновлён ${publishedListMeta.updated}, ${publishedListMeta.count} объектов` },
  { id: 's4', name: 'Instagram — публичные профили', kind: 'Публикации', st: 'demo', note: 'Публичные посты по словарю; персонализированная реклама не охватывается' },
  { id: 's5', name: 'TikTok', kind: 'Публикации', st: 'demo', note: 'Описание видео + OCR кадров' },
  { id: 's6', name: 'Krisha.kz / OLX / KN.kz', kind: 'Объявления', st: 'plan', note: 'Раздел «Новостройки», Усть-Каменогорск' },
  { id: 's7', name: 'Геопортал / 2ГИС', kind: 'Координаты, контуры', st: 'plan', note: 'Подложка — OpenStreetMap / CARTO' },
]
const stTag = { on: <Tag type="green">Подключён</Tag>, demo: <Tag className="tag-yellow">Демо-режим</Tag>, plan: <Tag type="cool-gray">Планируется</Tag> }

export default function Sources() {
  const { notify } = useStore()
  const headers = [{ key: 'name', header: 'Источник' }, { key: 'kind', header: 'Данные' }, { key: 'note', header: 'Описание' }, { key: 'st', header: 'Статус' }, { key: 'on', header: 'Сбор' }, { key: 'act', header: '' }]
  return (
    <>
      <PageHeader crumbs={[['SAQ', '/'], ['Источники данных']]} title="Источники данных" sub="Система работает без закрытых государственных API. Обязательные источники — локальные данные и ручной ввод; парсеры соцсетей подключаются дополнительно." />
      <DataTable rows={SRC.map((s) => ({ ...s, on: s.st, act: '' }))} headers={headers}>
        {({ rows, headers: h, getHeaderProps, getTableProps }) => (
          <TableContainer>
            <Table {...getTableProps()} size="lg">
              <TableHead><TableRow>{h.map((x) => <TableHeader {...getHeaderProps({ header: x })} key={x.key}>{x.header}</TableHeader>)}</TableRow></TableHead>
              <TableBody>
                {rows.map((r) => {
                  const s = SRC.find((x) => x.id === r.id)!
                  return (
                    <TableRow key={r.id}>
                      <TableCell><span className="cell-title">{s.name}</span>{s.req && <div className="cell-sub">обязательный по ТЗ</div>}</TableCell>
                      <TableCell>{s.kind}</TableCell>
                      <TableCell>{s.note}</TableCell>
                      <TableCell>{stTag[s.st]}</TableCell>
                      <TableCell><Toggle id={`t-${s.id}`} size="sm" labelText="" hideLabel defaultToggled={s.st !== 'plan'} disabled={s.st === 'plan'} labelA="" labelB="" /></TableCell>
                      <TableCell><Button kind="ghost" size="sm" renderIcon={Renew} disabled={s.st === 'plan'} onClick={() => notify(`Синхронизация: ${s.name}`, 'info')}>Обновить</Button></TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </DataTable>
    </>
  )
}
