import { useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { OpenDataList } from '../components/OpenData'
import { OFFLINE_FILE } from '../data/openData'
import { OpenDataNote, Origin } from '../components/Provenance'
import { Disclaimer, NoScreening, ScreeningPic, ScreeningTag } from '../components/Screening'
import { FilterPop, Modal, NeedsCheck, RiskScore, RiskZone, Seg, fmtDate, plural } from '../components/ui'
import type { Stage, Tri, Zone } from '../data/types'
import { findComplex, useSearchText } from '../lib/find'
import { from } from '../lib/nav'
import { NO_DATA, isAdvertised, isDemo, isRegistry, placeText, stageText, useRegistry } from '../lib/provenance'
import { NOT_ASSESSED, isAssessed } from '../lib/risk'
import {
  SCREENING_FILTER_LABEL, SCREENING_HINT, SCREENING_ORDER, anyScreening, compareScreening, hasPoint, inScreening, parseScreeningParam, reasonShort, statusOf, topReason,
} from '../lib/screening'
import type { ScreeningStatus } from '../data/types'
import { useStore } from '../lib/store'
import { IcChevronRight, IcClose, IcGrid, IcList, IcMap, IcSliders } from '../ui/icons'

const STAGES: Stage[] = ['котлован', 'фундамент', 'каркас', 'кровля', 'отделка', 'введён']
const triText = (v: Tri) => (v === 'yes' ? 'есть' : v === 'no' ? 'нет' : 'нет сведений')
const quarter = (d: string) => { if (!d) return NO_DATA; const x = new Date(d); return `${['I', 'II', 'III', 'IV'][Math.floor(x.getMonth() / 3)]} кв. ${x.getFullYear()}` }
type Sort = 'screening' | 'risk' | 'end' | 'progress'
/** control — ЖК на контроле, open — объекты открытых данных */
type Layer = 'control' | 'open'
const ZONES: Zone[] = ['red', 'yellow', 'green']
const NEW = ['новостройка', 'новостройки', 'новостроек'] as const

/** Выдача ЖК по образцу realty.yandex.ru/…/novostrojka: полоса фильтров, чипы, сортировка, карточки */
export default function Complexes() {
  const { mode, complexes, ads, developers, riskOf } = useStore()
  const nav = useNavigate()
  // слой, поиск, фильтры, сортировка и вид — в адресе: «назад» из карточки возвращает в тот же список
  const [sp, setSp] = useSearchParams()
  const put = (patch: Record<string, string | string[]>) => setSp((p) => {
    const n = new URLSearchParams(p)
    for (const [k, v] of Object.entries(patch)) { const x = Array.isArray(v) ? v.join(',') : v; if (x) n.set(k, x); else n.delete(k) }
    return n
  }, { replace: true })
  const list = (k: string) => sp.get(k)?.split(',').filter(Boolean) ?? []
  const layer: Layer = sp.get('layer') === 'open' ? 'open' : 'control'
  const view = sp.get('view') === 'list' ? 'list' : 'grid'
  const [q, setQ] = useSearchText()
  const zone = list('zone').filter((z): z is Zone => ZONES.includes(z as Zone))
  const district = list('district')
  const stage = list('stage').filter((x): x is Stage => STAGES.includes(x as Stage))
  const chip = sp.get('chip') ?? ''
  const screening = parseScreeningParam(sp.get('screening'))
  // «Проверка по рекламе» считается на сервере: есть — по умолчанию подозрение первыми, нет (сервер старой версии) — по риску
  const screenOn = anyScreening(complexes)
  const defSort: Sort = screenOn ? 'screening' : 'risk'
  const sortParam = sp.get('sort')
  const sort: Sort = sortParam === 'end' || sortParam === 'progress' || sortParam === 'risk' || (sortParam === 'screening' && screenOn) ? sortParam : defSort
  const devId = sp.get('dev') ?? ''
  const setLayer = (l: Layer) => put({ layer: l === 'open' ? 'open' : '' })
  const setView = (v: 'grid' | 'list') => put({ view: v === 'list' ? 'list' : '' })
  const setZone = (v: Zone[]) => put({ zone: v })
  const setDistrict = (v: string[]) => put({ district: v })
  const setStage = (v: Stage[]) => put({ stage: v })
  const setChip = (v: string) => put({ chip: v })
  const setSort = (v: Sort) => put({ sort: v === defSort ? '' : v })
  const setScreening = (v: ScreeningStatus[]) => put({ screening: v })
  const reg = useRegistry()
  const [allF, setAllF] = useState(false)
  // карточка ЖК покажет «← Новостройки» с этой же выборкой
  const go = (to: string) => nav(to, { state: from('Новостройки', `/complexes${sp.toString() ? `?${sp}` : ''}`) })

  const all = useMemo(() => complexes.map((c) => ({ c, r: riskOf(c), dev: developers.find((d) => d.id === c.developerId), nAds: ads.filter((a) => a.complexId === c.id).length })), [complexes, ads, developers, riskOf])
  const chips: [string, string, (x: (typeof all)[number]) => boolean][] = [
    ['na', 'Риск не оценивается', (x) => !isAssessed(x.r)],
    ['check', 'Нужна проверка', (x) => x.r.needsCheck],
    ['nolist', 'Нет в перечне ГАСК', (x) => x.c.inPublishedList === 'no'],
    ['idle', 'Стройка стоит', (x) => x.c.idleDays >= 30],
    ['ads', 'С рекламой', (x) => x.nAds > 0],
    ['built', 'Сданные', (x) => x.c.stage === 'введён'],
  ]
  const match = (x: (typeof all)[number], f: { zone?: Zone[]; district?: string[]; stage?: Stage[]; screening?: ScreeningStatus[] } = {}) => {
    const z = f.zone ?? zone, d = f.district ?? district, st = f.stage ?? stage, scr = f.screening ?? screening
    return (!q || findComplex(q, x.c, x.dev)) && (!devId || x.c.developerId === devId) && inScreening(statusOf(x.c), scr)
      && (!z.length || (isAssessed(x.r) && z.includes(x.r.zone))) && (!d.length || d.includes(x.c.district))
      && (!st.length || (!!x.c.stage && st.includes(x.c.stage)))
      && (!chip || (chips.find((ch) => ch[0] === chip)?.[2](x) ?? true))
  }
  // по риску: оценённые выше не оценённых (у них нет балла, а не балл 0); без срока сдачи — в конце
  const byRisk = (a: (typeof all)[number], b: (typeof all)[number]) => (Number(isAssessed(b.r)) - Number(isAssessed(a.r))) || b.r.score - a.r.score
  const rows = all.filter((x) => match(x))
    .sort((a, b) => sort === 'screening' ? compareScreening(a.c.screening, b.c.screening) || byRisk(a, b)
      : sort === 'risk' ? byRisk(a, b)
      : sort === 'end' ? (a.c.plannedEnd || '9999').localeCompare(b.c.plannedEnd || '9999') : b.c.progress - a.c.progress)
  const districts = [...new Set(complexes.map((c) => c.district).filter(Boolean))]
  // «Риск не оценивается» — только когда такие ЖК есть (карточки из открытого реестра без проверенных сведений)
  const shownChips = chips.filter(([k, , fn]) => k !== 'na' || chip === k || all.some(fn))
  // карточки из открытого реестра, по рекламе и (в старых БД) прежние демо-сценарии — от этого подписи слоя и заголовка
  const anyDemo = complexes.some(isDemo), anyReg = complexes.some(isRegistry)
  const devName = devId ? developers.find((d) => d.id === devId)?.name ?? 'не найден' : ''
  // строку поиска очищает её крестик; «Сбросить» — когда выбран хоть один фильтр
  const dirty = !!(zone.length || district.length || stage.length || chip || devId || screening.length)
  const reset = () => setQ('', { zone: '', district: '', stage: '', chip: '', dev: '', screening: '' })
  const scrCount = (k: ScreeningStatus) => all.filter((x) => statusOf(x.c) === k).length

  const layerSeg = (
    <Seg value={layer} onChange={setLayer} items={[
      ['control', <>{anyDemo && !anyReg ? 'Сценарии (демо)' : 'ЖК на контроле'}<span style={{ opacity: 0.56 }}>{complexes.length}</span></>],
      ['open', <>Открытые данные{reg.total !== undefined && <span style={{ opacity: 0.56 }}>{reg.total}</span>}</>],
    ]} />
  )

  if (layer === 'open') return (
    <>
      <div className="fbar" style={{ marginTop: 12 }}>
        {layerSeg}
        <label className="fsearch">
          <input placeholder="Название, адрес, застройщик или БИН" value={q} onChange={(e) => setQ(e.target.value)} />
          {q ? <button className="c3" onClick={() => setQ('')}><IcClose size={16} /></button> : <Link to="/" className="link t-sm bold">На карте</Link>}
        </label>
      </div>
      <div className="page-title" style={{ alignItems: 'center' }}>
        <div>
          <h1 className="t-h1">Открытые данные: объекты долевого строительства</h1>
          <p className="c2" style={{ marginTop: 8, maxWidth: 820 }}>Реальные объекты Усть-Каменогорска из Homeportal и Казреестра (CSV Ashyq Data) — как их опубликовал источник, со ссылкой и временем сбора.
            Записи загружены файлом, автоматической синхронизации с реестрами нет. <Link className="link" to="/sources">Источники и импорт</Link></p>
        </div>
      </div>
      <div style={{ marginBottom: 24 }}><OpenDataNote /></div>
      {mode === 'snapshot' && <div className="ai-note" style={{ marginBottom: 16 }}>Офлайн-копия: показана встроенная копия открытых данных ({OFFLINE_FILE}) — те же записи, что загружает сервер. Импорт новых файлов — с сервером (./dev.sh).</div>}
      {reg.data ? <>
          {reg.err && <div className="alog-err t-sm">Список не обновился: {reg.err} <button className="link" onClick={reg.retry}>Повторить</button></div>}
          <OpenDataList data={reg.data} q={q} />
        </>
          : reg.err ? <div className="empty">Открытые данные не загрузились: {reg.err} <button className="link" onClick={reg.retry}>Повторить</button></div>
            : <div className="empty">Загружаем открытые данные…</div>}
    </>
  )

  return (
    <>
      <div className="fbar" style={{ marginTop: 12 }}>
        {layerSeg}
        <span className="vsep" />
        {dirty && <><button className="fbtn gray" onClick={reset}><IcClose />Сбросить</button><span className="vsep" /></>}
        <button className="fbtn icon" aria-label="Все фильтры" onClick={() => setAllF(true)}><IcSliders /></button>
        {screenOn && <FilterPop label="Проверка" title="Проверка по рекламе" multi value={screening} onChange={setScreening} count={(v) => all.filter((x) => match(x, { screening: v })).length}
          options={SCREENING_ORDER.map((k) => [k, SCREENING_FILTER_LABEL[k]] as [ScreeningStatus, string])} />}
        <FilterPop label="Зона" title="Зона риска" multi value={zone} onChange={setZone} count={(v) => all.filter((x) => match(x, { zone: v })).length}
          options={[['red', 'Красная'], ['yellow', 'Жёлтая'], ['green', 'Зелёная']]} />
        <FilterPop label="Район" title="Район города" multi value={district} onChange={setDistrict} count={(v) => all.filter((x) => match(x, { district: v })).length}
          options={districts.map((d) => [d, d] as [string, string])} />
        <FilterPop label="Стадия" title="Стадия строительства" multi value={stage} onChange={setStage} count={(v) => all.filter((x) => match(x, { stage: v })).length}
          options={STAGES.map((s) => [s, s[0].toUpperCase() + s.slice(1)] as [Stage, string])} />
        <label className="fsearch">
          <input placeholder="ЖК, адрес, застройщик или БИН" value={q} onChange={(e) => setQ(e.target.value)} />
          {q ? <button className="c3" onClick={() => setQ('')}><IcClose size={16} /></button> : <Link to="/" className="link t-sm bold">На карте</Link>}
        </label>
      </div>

      <div className="page-title" style={{ alignItems: 'center' }}>
        <div>
          <h1 className="t-h1">Новостройки Усть-Каменогорска на контроле</h1>
          {anyDemo && <p className="t-sm c2 row wrap-f" style={{ marginTop: 8, gap: 8 }}><Origin kind="demo" label="Демо-сценарии" />Вымышленные ЖК и застройщики — на них показана модель риска (ТЗ §4, §5).{!anyReg && ' Реальные объекты — во вкладке «Открытые данные», без оценки риска.'}</p>}
          {anyReg && <p className="t-sm c2 row wrap-f" style={{ marginTop: 8, gap: 8 }}><Origin kind="registry" />Карточки реальных ЖК по открытому реестру — {NOT_ASSESSED.toLowerCase()}, пока нет подтверждённой привязки публикации, сверенного документа, обращения или снимка площадки.</p>}
          {complexes.some(isAdvertised) && <p className="t-sm c2 row wrap-f" style={{ marginTop: 8, gap: 8 }}><Origin kind="advertised" />ЖК, которых нет в открытом реестре: карточки созданы по рекламе, данные не проверены. <Link className="link" to="/discovered">Объекты из рекламы</Link></p>}
          {screenOn ? (
            <div className="scr-chips" style={{ marginTop: 16, alignItems: 'center' }} role="group" aria-label="Проверка по рекламе">
              <span className="t-sm bold" style={{ marginRight: 4 }}>Проверка по рекламе:</span>
              {SCREENING_ORDER.map((k) => {
                const on = screening.length === 1 && screening[0] === k
                return (
                  <button key={k} className={`chip ${on ? 'on' : ''}`} aria-pressed={on} title={SCREENING_HINT[k]} onClick={() => setScreening(on ? [] : [k])}>
                    <ScreeningPic status={k} />{SCREENING_FILTER_LABEL[k]}<span className="n num">{scrCount(k)}</span>
                  </button>
                )
              })}
              <Disclaimer />
            </div>
          ) : mode === 'snapshot' && <div style={{ marginTop: 12, maxWidth: 560 }}><NoScreening /></div>}
          <div className="chips" style={{ marginTop: 16 }}>
            {devId && <button className="chip on" onClick={() => put({ dev: '' })} title="Убрать фильтр по застройщику">Застройщик: {devName}<IcClose size={14} /></button>}
            {shownChips.map(([k, l, fn]) => <button key={k} className={`chip ${chip === k ? 'on' : ''}`} onClick={() => setChip(chip === k ? '' : k)}>{l}<span className="n">{all.filter(fn).length}</span></button>)}
          </div>
        </div>
        <Link to="/" className="ph" style={{ width: 330, height: 100, borderRadius: 24, flex: 'none', background: 'url(/img/city-mountain.jpg) center/cover' }}>
          <span className="btn w m" style={{ position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%, -50%)' }}><IcMap size={16} />Смотреть на карте</span>
        </Link>
      </div>

      <div className="row between">
        <div className="sorter">
          <b>{rows.length} {plural(rows.length, NEW)}:</b>
          {([...(screenOn ? [['screening', 'сначала подозрение']] as const : []), ['risk', 'по риску'], ['end', 'по сроку сдачи'], ['progress', 'по готовности']] as const).map(([k, l]) => <button key={k} className={sort === k ? 'on' : ''} onClick={() => setSort(k)}>{l}</button>)}
        </div>
        <div className="row" style={{ gap: 16 }}>
          <Link to="/ads" className="row bold" style={{ gap: 4, fontSize: 16 }}>{ads.length} {plural(ads.length, ['публикация', 'публикации', 'публикаций'])} в ленте<IcChevronRight size={16} /></Link>
          <div className="seg"><button className={view === 'grid' ? 'on' : ''} onClick={() => setView('grid')} aria-label="Карточки"><IcGrid /></button><button className={view === 'list' ? 'on' : ''} onClick={() => setView('list')} aria-label="Списком"><IcList /></button></div>
        </div>
      </div>

      {rows.length === 0 && <div className="empty">Нет объектов под эти фильтры</div>}
      {view === 'grid' ? (
        <div className="gcards fade">
          {rows.map(({ c, r, dev, nAds }) => (
            <div key={c.id} className="gcard" onClick={() => go(`/complexes/${c.id}`)}>
              <div className="ph">
                <img src={c.cover || undefined} alt="" />
                <div className="tl">{c.screening && <ScreeningTag s={c.screening} short />}{isDemo(c) && <Origin kind="demo" label="демо" onPhoto title="Демонстрационные данные: вымышленный ЖК" />}{isRegistry(c) && <Origin kind="registry" label="реестр" onPhoto />}{isAdvertised(c) && <Origin kind="advertised" label="по рекламе" onPhoto />}<span className="tag white">{stageText(c)}</span>{r.needsCheck && <span className="tag white">нужна проверка</span>}{c.idleDays >= 30 && <span className="tag white">стоит {c.idleDays} дн.</span>}</div>
              </div>
              <div className="b">
                <span className="dev">{dev?.name}</span>
                <div className="row between" style={{ alignItems: 'flex-start' }}><span className="nm">{c.name}</span><RiskScore r={r} /></div>
                <span className="meta">{c.stage ? `${c.stage === 'введён' ? 'Сдан' : 'Строится'} · ${c.floors} эт. · ${c.progress}%` : 'Стадия не известна'} · сдача {quarter(c.plannedEnd)}</span>
                <span className="meta row" style={{ gap: 6 }}><span className={`zdot ${isAssessed(r) ? r.zone : 'na'}`} />{placeText(c, ', ') || 'адрес не указан'}{!hasPoint(c) && ' · без координат'}</span>
                {c.screening && (() => {
                  const why = topReason(c.screening)
                  return <span className="meta clamp2" title={c.screening.disclaimer}><b className="bold" style={{ color: 'var(--text)' }}>{c.screening.label}</b>{why && <> · {reasonShort(why)}</>}</span>
                })()}
                <div className="rows">
                  <span>Гарантия ЕО</span><span className={c.basis.guarantee === 'no' ? 'bold' : ''} style={{ color: c.basis.guarantee === 'no' ? 'var(--red)' : undefined }}>{triText(c.basis.guarantee)}</span>
                  <span>Разрешение МИО</span><span>{triText(c.basis.mio)}</span>
                  <span>Банк (БВУ)</span><span>{triText(c.basis.bvu)}</span>
                  <span>Перечень ГАСК</span><span>{c.inPublishedList === 'yes' ? 'в перечне' : triText(c.inPublishedList)}</span>
                  <span>Реклама</span><span title={!nAds && c.screening?.counts.ads ? 'Публикации отнесены к ЖК по упоминаниям (гипотезы ИИ), привязку подтверждает аналитик' : undefined}>{nAds ? `${nAds} публ.` : c.screening?.counts.ads ? `${c.screening.counts.ads} упом. · гипотезы` : '—'}</span>
                </div>
                <div className="acts">
                  <button className="btn y" onClick={(e) => { e.stopPropagation(); go(`/complexes/${c.id}`) }}>Открыть</button>
                  <button className="btn g" onClick={(e) => { e.stopPropagation(); go(`/complexes/${c.id}?tab=ads`) }}>Реклама</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="card fade">
          {rows.map(({ c, r, dev }) => (
            <button key={c.id} className="cell" onClick={() => go(`/complexes/${c.id}`)} style={{ display: 'grid', gridTemplateColumns: screenOn ? '88px minmax(0,1.3fr) minmax(0,1.1fr) minmax(0,1fr) 170px 60px' : '88px minmax(0,1.3fr) minmax(0,1fr) 170px 60px', gap: 20, minHeight: 92 }}>
              <img className="th" src={c.cover || undefined} alt="" style={{ width: 88, height: 64 }} />
              <div style={{ minWidth: 0 }}>
                <div className="t-xs c2">{dev?.name}</div>
                <div className="bold row" style={{ fontSize: 17, gap: 8 }}>{c.name}{isDemo(c) && <Origin kind="demo" label="демо" title="Демонстрационные данные: вымышленный ЖК" />}{isRegistry(c) && <Origin kind="registry" label="реестр" />}{isAdvertised(c) && <Origin kind="advertised" label="по рекламе" />}</div>
                <div className="t-sm c2">{placeText(c) || 'адрес не указан'}{!hasPoint(c) && ' · без координат'}</div>
              </div>
              {screenOn && (
                <div style={{ minWidth: 0 }}>
                  {c.screening ? <>
                    <ScreeningTag s={c.screening} />
                    {(() => { const why = topReason(c.screening); return why && <div className="t-xs c2 clamp2" style={{ marginTop: 4 }}>{reasonShort(why)}</div> })()}
                  </> : <span className="t-sm c2">нет оценки</span>}
                </div>
              )}
              <div className="row wrap-f" style={{ gap: 6 }}><RiskZone r={r} />{r.needsCheck && <NeedsCheck />}</div>
              <div>
                <div className="t-sm">{c.stage ? <>{c.stage} · <span className="num">{c.progress}%</span></> : 'стадия не известна'}</div>
                <div className="hbar" style={{ margin: '6px 0', height: 4 }}><i style={{ width: `${c.progress}%`, background: '#000' }} /></div>
                <div className="t-xs c2">сдача {c.plannedEnd ? fmtDate(c.plannedEnd) : NO_DATA}</div>
              </div>
              <div style={{ justifySelf: 'end' }}><RiskScore r={r} /></div>
            </button>
          ))}
        </div>
      )}
      {allF && (
        <Modal wide title="Все фильтры" onClose={() => setAllF(false)}
          footer={<><button className="btn g" onClick={reset}>Сбросить</button><button className="btn y" onClick={() => setAllF(false)}>Показать {rows.length} {plural(rows.length, ['новостройку', 'новостройки', 'новостроек'])}</button></>}>
          <div style={{ padding: '0 24px' }}>
            {([...(screenOn ? [['Проверка по рекламе', [...SCREENING_ORDER], screening, setScreening, SCREENING_FILTER_LABEL]] : []),
              ['Зона', ['red', 'yellow', 'green'], zone, setZone, { red: 'Красная', yellow: 'Жёлтая', green: 'Зелёная' }],
              ['Район', districts, district, setDistrict, null],
              ['Стадия', STAGES, stage, setStage, null]] as [string, string[], string[], (v: never[]) => void, Record<string, string> | null][]).map(([l, opts, val, set, names]) => (
              <div className="frow top" key={l}>
                <label>{l}</label>
                <div className="chips">{opts.map((o) => <button key={o} className={`chip ${val.includes(o) ? 'on' : ''}`} onClick={() => set((val.includes(o) ? val.filter((x) => x !== o) : [...val, o]) as never[])}>{names?.[o] ?? o[0].toUpperCase() + o.slice(1)}</button>)}</div>
              </div>
            ))}
            <div className="frow top">
              <label>Признаки</label>
              <div className="chips">{shownChips.map(([k, l, fn]) => <button key={k} className={`chip ${chip === k ? 'on' : ''}`} onClick={() => setChip(chip === k ? '' : k)}>{l}<span className="n">{all.filter(fn).length}</span></button>)}</div>
            </div>
          </div>
        </Modal>
      )}
    </>
  )
}
