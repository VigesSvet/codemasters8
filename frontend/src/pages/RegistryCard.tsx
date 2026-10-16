import { useEffect, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import MapView from '../components/MapView'
import { OpenDataNote, Origin, SrcLink } from '../components/Provenance'
import { PageTitle } from '../components/ui'
import { OFFLINE_FILE } from '../data/openData'
import type { RegistryCard as Card, SourceRecord } from '../data/types'
import { ApiError, errorText } from '../lib/api'
import { fundingName, onMap, sourceName, when } from '../lib/provenance'
import { useStore } from '../lib/store'

/** Загрузка карточки: чья (ключ) и в каком режиме — с сервера или из встроенной копии */
type Load = { key: string; mode: string; data?: Card; err?: string; missing?: boolean }
type Field = [string, ReactNode]

const num = (v: number) => v.toLocaleString('ru-RU', { maximumFractionDigits: 6 })
const coords = (r: SourceRecord) => (r.lat != null && r.lng != null ? `${num(r.lat)}, ${num(r.lng)}` : null)
const funding = (r: SourceRecord) => r.fundingText || (r.fundingMethod ? fundingName(r.fundingMethod) : null)

type Maybe = [string, ReactNode | null | undefined]
/** Поле карточки: подпись и значение (пустое — не показываем) */
const fl = (k: string, v: ReactNode | null | undefined): Maybe => [k, v]
/** Поля записи в порядке файла источника; пустое — источник не предоставил */
const recordFields = (r: SourceRecord): Maybe[] => [
  fl('ЖК / объект', r.name),
  fl('Адрес', r.address),
  fl('Застройщик', r.developer),
  fl('БИН застройщика', r.developerBin && <span className="num">{r.developerBin}</span>),
  fl('Уполномоченная компания', r.agent),
  fl('БИН уполномоченной компании', r.agentBin && <span className="num">{r.agentBin}</span>),
  fl('Стадия по источнику', r.stage),
  fl('Способ привлечения средств', funding(r)),
  fl('Номер документа', r.docNumber),
  fl('Дата выдачи документа', r.docDate && when(r.docDate)),
  fl('Начало строительства', r.start && when(r.start)),
  fl('Плановое завершение', r.plannedEnd && when(r.plannedEnd)),
  fl('Номер акта ввода', r.actNumber),
  fl('Дата акта ввода', r.actDate && when(r.actDate)),
  fl('Готовность по отчёту', r.readiness != null ? `${num(r.readiness)}%` : null),
  fl('Период отчёта', r.reportPeriod),
  fl('Координаты', coords(r)),
  fl('Кадастровый номер', r.cadastral && <span className="num">{r.cadastral}</span>),
  fl('Сайт ЖК', r.site && <SrcLink url={r.site}>{r.site.replace(/^https?:\/\//, '').slice(0, 48)}</SrcLink>),
  fl('Примечание источника', r.note),
]
/** Провенанс записи: откуда, где, когда */
const provFields = (r: SourceRecord): Maybe[] => [
  fl('Источник', r.sourceName || sourceName(r.source)),
  // раздел — только как атрибут источника: без цвета и оценок (T-31)
  fl('Раздел источника', r.sourceSection && `«${r.sourceSection}»`),
  fl('Ссылка на источник', r.sourceUrl && <SrcLink url={r.sourceUrl}>{r.sourceUrl.replace(/^https?:\/\//, '')}</SrcLink>),
  fl('ID в источнике', r.sourceRecordId && <span className="num">{r.sourceRecordId}</span>),
  fl('Когда собраны данные', r.collectedAt && <span title={r.collectedAt}>{when(r.collectedAt)}</span>),
  fl('Дата загрузки у поставщика', r.providerLoadedAt && <span title={r.providerLoadedAt}>{when(r.providerLoadedAt)}</span>),
  fl('Загружено в систему', r.importedAt && <>{when(r.importedAt)}{r.importFile && <span className="c2"> · файл «{r.importFile}»{r.row != null && `, строка ${r.row}`}</span>}</>),
]
const kv = (xs: Field[]) => xs.flatMap(([k, v]) => [<span key={`${k}k`}>{k}</span>, <span key={`${k}v`} style={{ overflowWrap: 'anywhere' }}>{v}</span>])

/** Одна запись источника: все поля, ссылка, ID, время сбора; пустые поля перечислены отдельно */
function RecordView({ r, i }: { r: SourceRecord; i: number }) {
  const all = recordFields(r)
  const filled = all.filter((x): x is Field => x[1] != null && x[1] !== '')
  const empty = all.filter(([, v]) => v == null || v === '').map(([k]) => k)
  const prov = provFields(r).filter((x): x is Field => x[1] != null && x[1] !== '')
  return (
    <section className="card od-rec">
      <div className="card-head">
        <h3>Запись {i + 1} · {sourceName(r.source, true)}{r.sourceRecordId && <span className="c2 num"> · ID {r.sourceRecordId}</span>}</h3>
        <Origin kind="source" source={r.sourceName || sourceName(r.source)} at={r.collectedAt} />
      </div>
      <div className="kv">{kv(prov)}</div>
      <div className="sig-h" style={{ margin: '18px 0 10px' }}>Сведения источника</div>
      <div className="kv">{kv(filled)}</div>
      {empty.length > 0 && <div className="od-empty-f">Источник не предоставил: {empty.join(', ')}</div>}
    </section>
  )
}

/** Объект открытых данных: только атрибутированные сведения источников — без риска, зон, сигналов и «нужна проверка» (T-31) */
export default function RegistryCard() {
  const { key = '' } = useParams()
  const { mode, registryObject, revision } = useStore()
  const [load, setLoad] = useState<Load | null>(null)
  const [ver, setVer] = useState(0)
  useEffect(() => {
    if (mode === 'loading' || !key) return
    let alive = true
    registryObject(key).then(
      (data) => { if (alive) setLoad({ key, mode, data }) },
      (e: unknown) => { if (alive) setLoad({ key, mode, err: errorText(e), missing: e instanceof ApiError && e.status === 404 }) },
    )
    return () => { alive = false }
  }, [key, mode, ver, revision]) // eslint-disable-line react-hooks/exhaustive-deps

  const crumbs: [string, string?][] = [['Карта', '/'], ['Новостройки', '/complexes'], ['Открытые данные', '/complexes?layer=open']]
  const mine = load?.key === key && load.mode === mode ? load : null
  if (!mine?.data) return (
    <>
      <PageTitle crumbs={[...crumbs, ['Объект']]} title="Объект открытых данных" />
      <div className="empty">
        {mode === 'loading' || !mine ? 'Загружаем записи источников…'
            : mine.missing ? <>Объекта с ключом «{key}» в открытых данных нет. <Link className="link" to="/complexes?layer=open">К списку</Link></>
              : <>Не загрузилось: {mine.err} <button className="link" onClick={() => setVer((v) => v + 1)}>Повторить</button></>}
      </div>
    </>
  )

  const { object: o, records } = mine.data
  const hasCoords = onMap(o)
  const summary: Maybe[] = [
    fl('Адрес', o.address),
    fl('Застройщик', o.developer && <>{o.developer}{o.developerBin && <span className="c2 num"> · БИН {o.developerBin}</span>}</>),
    fl('Способ привлечения средств', o.fundingMethods.length ? o.fundingMethods.map(fundingName).join(', ') : null),
    fl('Стадия по источнику', o.stage),
    fl('Плановое завершение', o.plannedEnd && when(o.plannedEnd)),
    fl('Раздел источника', o.flags?.sourceSection && `«${o.flags.sourceSection}»`),
    fl('Источники', o.sources.map((s) => sourceName(s)).join(', ')),
    fl('Когда собраны данные', o.collectedAt && when(o.collectedAt)),
  ]
  return (
    <>
      <PageTitle crumbs={[...crumbs, [o.name]]}
        title={<span className="row wrap-f" style={{ gap: 14 }}>{o.name}<Origin kind="source" source={o.sources.map((s) => sourceName(s)).join(', ')} at={o.collectedAt} /></span>}
        sub={<>{o.address || 'адрес источником не указан'} · {records.length} {records.length === 1 ? 'запись' : records.length < 5 ? 'записи' : 'записей'} источников</>} />

      <div className="cols-r">
        <div className="stack" style={{ gap: 16 }}>
          {mode === 'demo' && <div className="ai-note">Демо-режим без бэкенда: показана встроенная копия открытых данных ({OFFLINE_FILE}) — те же записи, что загружает сервер. Импорт новых файлов — с бэкендом (./dev.sh).</div>}
          <OpenDataNote>Поля ниже — дословно как у источника, с его ссылкой и временем сбора. Разные записи одного объекта (блоки, очереди, повторы в разных реестрах) показаны по отдельности.</OpenDataNote>
          {records.map((r, i) => <RecordView key={`${r.source}:${r.sourceRecordId ?? i}`} r={r} i={i} />)}
        </div>
        <aside className="stack sticky">
          <div className="card">
            <div className="row between"><span className="t-h3">Сводка по записям</span></div>
            <div className="kv t-sm" style={{ marginTop: 14, gridTemplateColumns: '130px 1fr' }}>{kv(summary.filter((x): x is Field => x[1] != null && x[1] !== ''))}</div>
            <p className="t-xs c2" style={{ marginTop: 16, lineHeight: '17px' }}>Это не вывод системы и не проверка аналитика: риск-скоринг, зоны и сигналы для реальных объектов не строятся.</p>
          </div>
          {hasCoords ? (
            <div className="ph" style={{ height: 200, borderRadius: 16 }}>
              <MapView items={[]} points={[{ key: o.key, name: o.name, lng: o.lng!, lat: o.lat! }]} center={[o.lng!, o.lat!]} zoom={14.5} interactive={false} labels />
            </div>
          ) : <div className="box t-sm c2">{o.flags?.coordsFarKm
            ? `Координаты источника — в ${o.flags.coordsFarKm} км от центра Усть-Каменогорска: оставлены как у источника, на карте не показаны.`
            : 'Координат у источника нет — объект в разделе «Без координат».'}</div>}
        </aside>
      </div>
    </>
  )
}
