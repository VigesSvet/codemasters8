import { useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { CoverPh } from '../components/Gallery'
import { galleryOf, photoSrc } from '../lib/gallery'
import { OpenDataList } from '../components/OpenData'
import { OFFLINE_FILE } from '../data/openData'
import { OpenDataNote, Origin } from '../components/Provenance'
import { Disclaimer, NoScreening, ScreeningPic, ScreeningTag } from '../components/Screening'
import { FilterPop, Modal, NeedsCheck, RiskScore, RiskZone, SafeImg, Seg } from '../components/ui'
import type { Stage, Tri, Zone } from '../data/types'
import { findComplex, useSearchText } from '../lib/find'
import { useT, type I18n } from '../lib/i18n'
import { from } from '../lib/nav'
import { NO_DATA, isAdvertised, isDemo, isRegistry, placeText, stageText, useRegistry } from '../lib/provenance'
import { NOT_ASSESSED, isAssessed } from '../lib/risk'
import {
  SCREENING_ORDER, anyScreening, compareScreening, hasPoint, inScreening, parseScreeningParam, reasonShort, statusOf, topReason,
} from '../lib/screening'
import type { ScreeningStatus } from '../data/types'
import { useStore } from '../lib/store'
import { IcChevronRight, IcClose, IcGrid, IcList, IcMap, IcSliders } from '../ui/icons'

const STAGES: Stage[] = ['котлован', 'фундамент', 'каркас', 'кровля', 'отделка', 'введён']
const triText = (t: I18n['t'], v: Tri) => t(v === 'yes' ? 'common.yesLow' : v === 'no' ? 'common.noLow' : 'common.noInfoLow')
const quarter = ({ t, tr }: I18n, d: string) => { if (!d) return tr(NO_DATA); const x = new Date(d); return t('cx.quarter', { q: ['I', 'II', 'III', 'IV'][Math.floor(x.getMonth() / 3)], y: x.getFullYear() }) }
/** Стадия с заглавной: «Каркас» / «Қаңқа» */
const cap = (x: string) => x[0].toUpperCase() + x.slice(1)
type Sort = 'screening' | 'risk' | 'end' | 'progress'
/** control — ЖК на контроле, open — объекты открытых данных */
type Layer = 'control' | 'open'
const ZONES: Zone[] = ['red', 'yellow', 'green']

/** Выдача ЖК по образцу realty.yandex.ru/…/novostrojka: полоса фильтров, чипы, сортировка, карточки */
export default function Complexes() {
  const { mode, complexes, ads, developers, riskOf } = useStore()
  const nav = useNavigate()
  const i18n = useT()
  const { t, tn, tr } = i18n
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
  const go = (to: string) => nav(to, { state: from(t('nav.complexes'), `/complexes${sp.toString() ? `?${sp}` : ''}`) })

  const all = useMemo(() => complexes.map((c) => ({ c, r: riskOf(c), dev: developers.find((d) => d.id === c.developerId), nAds: ads.filter((a) => a.complexId === c.id).length })), [complexes, ads, developers, riskOf])
  const chips: [string, string, (x: (typeof all)[number]) => boolean][] = [
    ['na', t('cx.chipNA'), (x) => !isAssessed(x.r)],
    ['check', t('common.needsCheck'), (x) => x.r.needsCheck],
    ['nolist', t('cx.chipNoList'), (x) => x.c.inPublishedList === 'no'],
    ['idle', t('cx.chipIdle'), (x) => x.c.idleDays >= 30],
    ['ads', t('cx.chipAds'), (x) => x.nAds > 0],
    ['built', t('cx.chipBuilt'), (x) => x.c.stage === 'введён'],
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
  const devName = devId ? developers.find((d) => d.id === devId)?.name ?? t('cx.devNotFound') : ''
  // строку поиска очищает её крестик; «Сбросить» — когда выбран хоть один фильтр
  const dirty = !!(zone.length || district.length || stage.length || chip || devId || screening.length)
  const reset = () => setQ('', { zone: '', district: '', stage: '', chip: '', dev: '', screening: '' })
  const scrCount = (k: ScreeningStatus) => all.filter((x) => statusOf(x.c) === k).length

  const layerSeg = (
    <Seg value={layer} onChange={setLayer} items={[
      ['control', <>{anyDemo && !anyReg ? t('cx.layerDemo') : t('cx.layerControl')}<span style={{ opacity: 0.56 }}>{complexes.length}</span></>],
      ['open', <>{t('cx.layerOpen')}{reg.total !== undefined && <span style={{ opacity: 0.56 }}>{reg.total}</span>}</>],
    ]} />
  )

  if (layer === 'open') return (
    <>
      <div className="fbar" style={{ marginTop: 12 }}>
        {layerSeg}
        <label className="fsearch">
          <input placeholder={t('cx.openSearch')} value={q} onChange={(e) => setQ(e.target.value)} />
          {q ? <button className="c3" onClick={() => setQ('')} aria-label={t('common.clear')}><IcClose size={16} /></button> : <Link to="/" className="link t-sm bold">{t('common.onMap')}</Link>}
        </label>
      </div>
      <div className="page-title" style={{ alignItems: 'center' }}>
        <div>
          <h1 className="t-h1">{t('cx.openTitle')}</h1>
          <p className="c2" style={{ marginTop: 8, maxWidth: 820 }}>{t('cx.openLead')} <Link className="link" to="/sources">{t('cx.sourcesLink')}</Link></p>
        </div>
      </div>
      <div style={{ marginBottom: 24 }}><OpenDataNote /></div>
      {mode === 'snapshot' && <div className="ai-note" style={{ marginBottom: 16 }}>{t('cx.openSnapshot', { file: OFFLINE_FILE })}</div>}
      {reg.data ? <>
          {reg.err && <div className="alog-err t-sm">{t('cx.notUpdated', { e: reg.err })} <button className="link" onClick={reg.retry}>{t('common.retry')}</button></div>}
          <OpenDataList data={reg.data} q={q} />
        </>
          : reg.err ? <div className="empty">{t('cx.openFailed', { e: reg.err })} <button className="link" onClick={reg.retry}>{t('common.retry')}</button></div>
            : <div className="empty">{t('cx.openLoading')}</div>}
    </>
  )

  return (
    <>
      <div className="fbar" style={{ marginTop: 12 }}>
        {layerSeg}
        <span className="vsep" />
        {dirty && <><button className="fbtn gray" onClick={reset}><IcClose />{t('common.reset')}</button><span className="vsep" /></>}
        <button className="fbtn icon" aria-label={t('cx.allFilters')} onClick={() => setAllF(true)}><IcSliders /></button>
        {screenOn && <FilterPop label={t('cx.fScr')} title={t('scr.title')} multi value={screening} onChange={setScreening} count={(v) => all.filter((x) => match(x, { screening: v })).length}
          options={SCREENING_ORDER.map((k) => [k, t(`scr.filter.${k}`)] as [ScreeningStatus, string])} />}
        <FilterPop label={t('cx.fZone')} title={t('cx.fZoneTitle')} multi value={zone} onChange={setZone} count={(v) => all.filter((x) => match(x, { zone: v })).length}
          options={ZONES.map((z) => [z, t(`zone.short.${z}`)] as [Zone, string])} />
        <FilterPop label={t('cx.fDistrict')} title={t('cx.fDistrictTitle')} multi value={district} onChange={setDistrict} count={(v) => all.filter((x) => match(x, { district: v })).length}
          options={districts.map((d) => [d, d] as [string, string])} />
        <FilterPop label={t('cx.fStage')} title={t('cx.fStageTitle')} multi value={stage} onChange={setStage} count={(v) => all.filter((x) => match(x, { stage: v })).length}
          options={STAGES.map((s) => [s, cap(tr(s))] as [Stage, string])} />
        <label className="fsearch">
          <input placeholder={t('cx.search')} value={q} onChange={(e) => setQ(e.target.value)} />
          {q ? <button className="c3" onClick={() => setQ('')} aria-label={t('common.clear')}><IcClose size={16} /></button> : <Link to="/" className="link t-sm bold">{t('common.onMap')}</Link>}
        </label>
      </div>

      <div className="page-title" style={{ alignItems: 'center' }}>
        <div>
          <h1 className="t-h1">{t('cx.title')}</h1>
          {anyDemo && <p className="t-sm c2 row wrap-f" style={{ marginTop: 8, gap: 8 }}><Origin kind="demo" label={t('cx.demoLabel')} />{t('cx.demoText')}{!anyReg && t('cx.demoReal')}</p>}
          {anyReg && <p className="t-sm c2 row wrap-f" style={{ marginTop: 8, gap: 8 }}><Origin kind="registry" />{t('cx.registryText', { na: tr(NOT_ASSESSED).toLowerCase() })}</p>}
          {complexes.some(isAdvertised) && <p className="t-sm c2 row wrap-f" style={{ marginTop: 8, gap: 8 }}><Origin kind="advertised" />{t('cx.advertisedText')} <Link className="link" to="/discovered">{t('cx.discLink')}</Link></p>}
          {screenOn ? (
            <div className="scr-chips" style={{ marginTop: 16, alignItems: 'center' }} role="group" aria-label={t('scr.title')}>
              <span className="t-sm bold" style={{ marginRight: 4 }}>{t('scr.titleColon')}</span>
              {SCREENING_ORDER.map((k) => {
                const on = screening.length === 1 && screening[0] === k
                return (
                  <button key={k} className={`chip ${on ? 'on' : ''}`} aria-pressed={on} title={t(`scr.hint.${k}`)} onClick={() => setScreening(on ? [] : [k])}>
                    <ScreeningPic status={k} />{t(`scr.filter.${k}`)}<span className="n num">{scrCount(k)}</span>
                  </button>
                )
              })}
              <Disclaimer />
            </div>
          ) : mode === 'snapshot' && <div style={{ marginTop: 12, maxWidth: 560 }}><NoScreening /></div>}
          <div className="chips" style={{ marginTop: 16 }}>
            {devId && <button className="chip on" onClick={() => put({ dev: '' })} title={t('cx.devChipHint')}>{t('cx.devChip', { name: devName })}<IcClose size={14} /></button>}
            {shownChips.map(([k, l, fn]) => <button key={k} className={`chip ${chip === k ? 'on' : ''}`} onClick={() => setChip(chip === k ? '' : k)}>{l}<span className="n">{all.filter(fn).length}</span></button>)}
          </div>
        </div>
        <Link to="/" className="ph" style={{ width: 330, height: 100, borderRadius: 24, flex: 'none', background: 'url(/img/city-mountain.jpg) center/cover' }}>
          <span className="btn w m" style={{ position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%, -50%)' }}><IcMap size={16} />{t('cx.seeOnMap')}</span>
        </Link>
      </div>

      <div className="row between">
        <div className="sorter">
          <b>{tn('cx.countN', rows.length)}:</b>
          {([...(screenOn ? [['screening', t('cx.sortScreening')]] as const : []), ['risk', t('cx.sortRisk')], ['end', t('cx.sortEnd')], ['progress', t('cx.sortProgress')]] as const).map(([k, l]) => <button key={k} className={sort === k ? 'on' : ''} onClick={() => setSort(k)}>{l}</button>)}
        </div>
        <div className="row" style={{ gap: 16 }}>
          <Link to="/ads" className="row bold" style={{ gap: 4, fontSize: 16 }}>{tn('cx.feedN', ads.length)}<IcChevronRight size={16} /></Link>
          <div className="seg"><button className={view === 'grid' ? 'on' : ''} onClick={() => setView('grid')} aria-label={t('cx.viewGrid')}><IcGrid /></button><button className={view === 'list' ? 'on' : ''} onClick={() => setView('list')} aria-label={t('cx.viewList')}><IcList /></button></div>
        </div>
      </div>

      {rows.length === 0 && <div className="empty">{t('cx.none')}</div>}
      {view === 'grid' ? (
        <div className="gcards fade">
          {rows.map(({ c, r, dev, nAds }) => (
            <div key={c.id} className="gcard" onClick={() => go(`/complexes/${c.id}`)}>
              <CoverPh c={c}>
                <div className="tl">{c.screening && <ScreeningTag s={c.screening} short />}{isDemo(c) && <Origin kind="demo" label={t('cx.demo')} onPhoto title={t('cx.demoHint')} />}{isRegistry(c) && <Origin kind="registry" label={t('cx.registry')} onPhoto />}{isAdvertised(c) && <Origin kind="advertised" label={t('cx.advertised')} onPhoto />}<span className="tag white">{tr(stageText(c))}</span>{r.needsCheck && <span className="tag white">{t('common.needsCheckLow')}</span>}{c.idleDays >= 30 && <span className="tag white">{t('cx.idleN', { n: c.idleDays })}</span>}</div>
              </CoverPh>
              <div className="b">
                <span className="dev">{dev?.name}</span>
                <div className="row between" style={{ alignItems: 'flex-start' }}><span className="nm">{c.name}</span><RiskScore r={r} /></div>
                <span className="meta">{c.stage ? `${c.stage === 'введён' ? t('cx.built') : t('cx.building')} · ${t('cx.floors', { n: c.floors })} · ${c.progress}%` : t('cx.stageUnknown')} · {t('cx.due', { d: quarter(i18n, c.plannedEnd) })}</span>
                <span className="meta row" style={{ gap: 6 }}><span className={`zdot ${isAssessed(r) ? r.zone : 'na'}`} />{placeText(c, ', ') || t('common.noAddress')}{!hasPoint(c) && t('common.noCoords')}</span>
                {c.screening && (() => {
                  const why = topReason(c.screening)
                  return <span className="meta clamp2" title={c.screening.disclaimer}><b className="bold" style={{ color: 'var(--text)' }}>{tr(c.screening.label)}</b>{why && <> · {reasonShort(why)}</>}</span>
                })()}
                <div className="rows">
                  <span>{t('basis.guarantee')}</span><span className={c.basis.guarantee === 'no' ? 'bold' : ''} style={{ color: c.basis.guarantee === 'no' ? 'var(--red)' : undefined }}>{triText(t, c.basis.guarantee)}</span>
                  <span>{t('basis.mio')}</span><span>{triText(t, c.basis.mio)}</span>
                  <span>{t('basis.bvu')}</span><span>{triText(t, c.basis.bvu)}</span>
                  <span>{t('basis.gask')}</span><span>{c.inPublishedList === 'yes' ? t('cx.inList') : triText(t, c.inPublishedList)}</span>
                  <span>{t('cx.ads')}</span><span title={!nAds && c.screening?.counts.ads ? t('cx.mentionsHint') : undefined}>{nAds ? t('common.adsN', { n: nAds }) : c.screening?.counts.ads ? t('cx.mentions', { n: c.screening.counts.ads }) : '—'}</span>
                </div>
                <div className="acts">
                  <button className="btn y" onClick={(e) => { e.stopPropagation(); go(`/complexes/${c.id}`) }}>{t('common.open')}</button>
                  <button className="btn g" onClick={(e) => { e.stopPropagation(); go(`/complexes/${c.id}?tab=ads`) }}>{t('cx.ads')}</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="card fade">
          {rows.map(({ c, r, dev }) => (
            <button key={c.id} className="cell cx-row" onClick={() => go(`/complexes/${c.id}`)} style={{ display: 'grid', gridTemplateColumns: screenOn ? '88px minmax(0,1.3fr) minmax(0,1.1fr) minmax(0,1fr) 170px 60px' : '88px minmax(0,1.3fr) minmax(0,1fr) 170px 60px', gap: 20, minHeight: 92 }}>
              {(() => { const p = galleryOf(c)[0]; const box = <span className="th" style={{ width: 88, height: 64, display: 'block', background: 'var(--fill)' }} />; return p ? <SafeImg className="th" src={photoSrc(p)} alt="" loading="lazy" style={{ width: 88, height: 64, objectFit: 'cover' }} fallback={box} /> : box })()}
              <div style={{ minWidth: 0 }}>
                <div className="t-xs c2">{dev?.name}</div>
                <div className="bold row" style={{ fontSize: 17, gap: 8 }}>{c.name}{isDemo(c) && <Origin kind="demo" label={t('cx.demo')} title={t('cx.demoHint')} />}{isRegistry(c) && <Origin kind="registry" label={t('cx.registry')} />}{isAdvertised(c) && <Origin kind="advertised" label={t('cx.advertised')} />}</div>
                <div className="t-sm c2">{placeText(c) || t('common.noAddress')}{!hasPoint(c) && t('common.noCoords')}</div>
              </div>
              {screenOn && (
                <div style={{ minWidth: 0 }}>
                  {c.screening ? <>
                    <ScreeningTag s={c.screening} />
                    {(() => { const why = topReason(c.screening); return why && <div className="t-xs c2 clamp2" style={{ marginTop: 4 }}>{reasonShort(why)}</div> })()}
                  </> : <span className="t-sm c2">{t('cx.noScore')}</span>}
                </div>
              )}
              <div className="row wrap-f" style={{ gap: 6 }}><RiskZone r={r} />{r.needsCheck && <NeedsCheck />}</div>
              <div>
                <div className="t-sm">{c.stage ? <>{tr(c.stage)} · <span className="num">{c.progress}%</span></> : t('cx.stageUnknownLow')}</div>
                <div className="hbar" style={{ margin: '6px 0', height: 4 }}><i style={{ width: `${c.progress}%`, background: '#000' }} /></div>
                <div className="t-xs c2">{t('cx.due', { d: c.plannedEnd ? i18n.date(c.plannedEnd) : tr(NO_DATA) })}</div>
              </div>
              <div style={{ justifySelf: 'end' }}><RiskScore r={r} /></div>
            </button>
          ))}
        </div>
      )}
      {allF && (
        <Modal wide title={t('cx.allFilters')} onClose={() => setAllF(false)}
          footer={<><button className="btn g" onClick={reset}>{t('common.reset')}</button><button className="btn y" onClick={() => setAllF(false)}>{tn('cx.showN', rows.length)}</button></>}>
          <div style={{ padding: '0 24px' }}>
            {([...(screenOn ? [[t('scr.title'), [...SCREENING_ORDER], screening, setScreening, Object.fromEntries(SCREENING_ORDER.map((k) => [k, t(`scr.filter.${k}`)]))]] : []),
              [t('cx.fZone'), ZONES, zone, setZone, Object.fromEntries(ZONES.map((z) => [z, t(`zone.short.${z}`)]))],
              [t('cx.fDistrict'), districts, district, setDistrict, null],
              [t('cx.fStage'), STAGES, stage, setStage, Object.fromEntries(STAGES.map((x) => [x, cap(tr(x))]))]] as [string, string[], string[], (v: never[]) => void, Record<string, string> | null][]).map(([l, opts, val, set, names]) => (
              <div className="frow top" key={l}>
                <label>{l}</label>
                <div className="chips">{opts.map((o) => <button key={o} className={`chip ${val.includes(o) ? 'on' : ''}`} onClick={() => set((val.includes(o) ? val.filter((x) => x !== o) : [...val, o]) as never[])}>{names?.[o] ?? o[0].toUpperCase() + o.slice(1)}</button>)}</div>
              </div>
            ))}
            <div className="frow top">
              <label>{t('cx.signs')}</label>
              <div className="chips">{shownChips.map(([k, l, fn]) => <button key={k} className={`chip ${chip === k ? 'on' : ''}`} onClick={() => setChip(chip === k ? '' : k)}>{l}<span className="n">{all.filter(fn).length}</span></button>)}</div>
            </div>
          </div>
        </Modal>
      )}
    </>
  )
}
