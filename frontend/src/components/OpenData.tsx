// Открытые данные (ТЗ §4, решение 1 SPEC-E): объекты реестра — отдельный слой, не смешивается с ЖК на контроле.
// Только атрибутированные сведения источников: без балла, зон, сигналов и «нужна проверка».
import type { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import type { RegistryList, RegistryObject } from '../data/types'
import { findObject } from '../lib/find'
import { from } from '../lib/nav'
import { fundingName, onMap, sourceName, when } from '../lib/provenance'
import { IcChevronRight, IcDatabase } from '../ui/icons'
import { Origin } from './Provenance'

/** Значение — факт источника: с иконкой и подсказкой, откуда оно */
export function Fact({ children, from }: { children: ReactNode; from: string }) {
  return <span title={`Факт из источника: ${from}`}>{children}<IcDatabase size={12} className="fact-i" /></span>
}

/** Карточка объекта открытых данных в реестре: нейтральная, без балла */
export function OpenObjectCard({ o }: { o: RegistryObject }) {
  const loc = useLocation()
  const src = `${o.sources.map((s) => sourceName(s)).join(', ')}${o.collectedAt ? `, собрано ${when(o.collectedAt)}` : ''}`
  return (
    <Link to={`/registry/${encodeURIComponent(o.key)}`} state={from('Открытые данные', loc.pathname + loc.search)} className="devcard" style={{ gap: 12 }}>
      <div className="row between" style={{ gap: 8, alignItems: 'flex-start' }}>
        <div className="od-chips">{o.sources.map((s) => <span key={s} className="tag gray">{sourceName(s, true)}</span>)}</div>
        <Origin kind="source" source={o.sources.map((s) => sourceName(s)).join(', ')} at={o.collectedAt} />
      </div>
      <div>
        <div className="t-h3" style={{ fontSize: 19, lineHeight: '23px' }}>{o.name}</div>
        <div className="t-sm c2" style={{ marginTop: 4 }}>{o.address || 'адрес источником не указан'}</div>
      </div>
      <div className="kv t-sm" style={{ gridTemplateColumns: '150px 1fr', gap: '8px 12px' }}>
        <span>Застройщик</span>
        <span>{o.developer ? <Fact from={src}>{o.developer}{o.developerBin && <span className="c2 num"> · БИН {o.developerBin}</span>}</Fact> : <span className="c3">не указан источником</span>}</span>
        <span>Привлечение средств</span>
        <span>{o.fundingMethods.length ? <Fact from={src}>{o.fundingMethods.map(fundingName).join(', ')}</Fact> : <span className="c3">не указано источником</span>}</span>
        {o.flags?.sourceSection && <><span>Раздел источника</span><span>«{o.flags.sourceSection}»</span></>}
        {!!o.flags?.coordsFarKm && <><span>Координаты</span><span className="c2">в {o.flags.coordsFarKm} км от города — как у источника, на карте не показаны</span></>}
        <span>Записей</span><span>{o.records}{o.collectedAt && <span className="c2"> · собрано {when(o.collectedAt)}</span>}</span>
      </div>
      <span className="row link t-sm" style={{ gap: 2, marginTop: 'auto' }}>Записи источников<IcChevronRight size={14} /></span>
    </Link>
  )
}

/** Список объектов открытых данных: с координатами и отдельно — без координат */
export function OpenDataList({ data, q }: { data: RegistryList; q: string }) {
  // порядок — как у сервера (по нормализованному названию); поиск — как в шапке: название, адрес, застройщик, БИН
  const sort = (xs: RegistryObject[]) => xs.filter((o) => findObject(q, o))
  const geo = sort(data.objects.filter(onMap)), nogeo = sort([...data.objects.filter((o) => !onMap(o)), ...data.withoutCoords])
  if (!geo.length && !nogeo.length) return <div className="empty">{q ? 'Ничего не найдено' : 'Открытых данных пока нет — загрузите файл на странице «Источники»'}</div>
  return (
    <>
      <h2 className="t-h2" style={{ margin: '8px 0 16px' }}>С координатами <span className="c3">· {geo.length}</span></h2>
      {geo.length ? <div className="gcards fade" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 16 }}>{geo.map((o) => <OpenObjectCard key={o.key} o={o} />)}</div>
        : <div className="c2" style={{ padding: '8px 0' }}>Нет объектов под этот поиск</div>}
      <h2 className="t-h2" style={{ margin: '40px 0 4px' }}>Без координат <span className="c3">· {nogeo.length}</span></h2>
      <p className="t-sm c2" style={{ marginBottom: 16 }}>Источник не дал координат (или дал «0»), либо его координаты далеко от города — на карте эти объекты не показываются.</p>
      {nogeo.length ? <div className="gcards fade" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 16 }}>{nogeo.map((o) => <OpenObjectCard key={o.key} o={o} />)}</div>
        : <div className="c2" style={{ padding: '8px 0' }}>Нет объектов под этот поиск</div>}
    </>
  )
}
