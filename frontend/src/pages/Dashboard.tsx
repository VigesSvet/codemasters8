import { coverSrc } from '../lib/gallery'
import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import HotspotPanel from '../components/Hotspots'
import { LinkMark } from '../components/Hypothesis'
import MapView, { type MapItem, type PlainPoint } from '../components/MapView'
import { Origin } from '../components/Provenance'
import RangeSlider from '../components/RangeSlider'
import { Disclaimer, NoScreening, ScreeningBlock, ScreeningPic, ScreeningTag } from '../components/Screening'
import { Cell, Highlight, PlatformIcon, RiskScore, RiskZone, StatusTag, Toggle } from '../components/ui'
import type { RiskResult, ScreeningStatus, Tri } from '../data/types'
import { findComplex } from '../lib/find'
import { heatPoints } from '../lib/hotspots'
import { countsLineT, useT, type I18n } from '../lib/i18n'
import { from } from '../lib/nav'
import { isAdvertised, isDemo, isRegistry, onMap, placeText, registryKeyOf, stageText, useRegistry } from '../lib/provenance'
import {
  RED_FROM, RISK_DEFAULT, RISK_MAX, SHOW_DEFAULT, SHOW_GROUPS, YELLOW_FROM, countShowGroups, isRiskDefault, isShowDefault,
  passesMapFilters, riskCounts, toggleStatus, type MapFilters, type RiskFilter, type ShowFlags,
} from '../lib/mapFilters'
import { bandGradient, rangeText } from '../lib/range'
import { isAssessed, linkConfirmed, model } from '../lib/risk'
import {
  DISCLAIMER, DISCLAIMER_SHORT, SCREENING_GLYPH, SCREENING_ORDER, SCREENING_TONE, anyScreening, compareScreening, countByStatus,
  hasPoint, labelOf, reasonShort, statusOf, topReason,
} from '../lib/screening'
import { useIsPhone } from '../lib/phone'
import { useStore } from '../lib/store'
import { IcCheck, IcChevronDown, IcChevronLeft, IcChevronRight, IcChevronUp, IcClose, IcHelp, IcInfo, IcList, IcMapPin, IcMegaphone, IcSearch, IcCamera, IcGlobe, IcUsers } from '../ui/icons'

/** Чипы «Проверки по рекламе» после «Все» — в порядке задачи: без замечаний, проверить, подозрение, нет данных; можно несколько */
const SCREEN_CHIPS: ScreeningStatus[] = ['ok', 'check', 'suspect', 'unknown']
/** Полоса балла формального риска — цвета зон по порогам модели: зелёная, жёлтая (янтарная, как балл), красная; мягкие переходы */
const RISK_TRACK = bandGradient([
  { from: 0, to: YELLOW_FROM, color: 'var(--green)' },
  { from: YELLOW_FROM, to: RED_FROM, color: 'var(--amber)' },
  { from: RED_FROM, to: RISK_MAX, color: 'var(--red)' },
], 0, RISK_MAX, 8)
const RISK_TICKS = [0, YELLOW_FROM, RED_FROM, RISK_MAX]
/** Подсказка к полосе: зоны по баллу из модели риска (красной зоне нужны ещё и подтверждённые R1 и R2); подписи модели — через tr */
const zonesHint = ({ t, tr }: I18n) => t('map.zonesHint', {
  list: model.zones.items.map((z) => `${tr(z.name).toLowerCase()} ${z.range}`).join(', '),
  rule: tr(model.zones.items.find((z) => z.zone === 'red')?.rule ?? ''),
})

// Сводки свёрнутых блоков — как screeningSummary / riskSummary / showSummary (lib/mapFilters), на языке интерфейса
const scrSummaryT = (t: I18n['t'], sel: readonly ScreeningStatus[]) =>
  sel.length ? SCREENING_ORDER.filter((x) => sel.includes(x)).map((x) => t(`scr.filter.${x}`)).join(' · ') : t('map.allStatuses')
function riskSummaryT(t: I18n['t'], f: RiskFilter): string {
  const parts = [f.min <= 0 && f.max >= RISK_MAX ? t('map.allScores') : t('map.scores', { a: f.min, b: f.max })]
  if (!f.withNA) parts.push(t('map.naHiddenShort'))
  if (f.needsCheck) parts.push(t('common.needsCheckLow'))
  return parts.join(' · ')
}
function showSummaryT(t: I18n['t'], f: ShowFlags): string {
  const off = SHOW_GROUPS.filter((g) => !f[g])
  return off.length ? t('map.hiddenGroups', { list: off.map((g) => t(`show.short.${g}`)).join(', ') }) : t('map.allGroups')
}

/** Флаг экрана карты, запомненный в этом браузере (легенда, панель); без доступа к хранилищу — значение по умолчанию */
function useFlag(key: string, initial: boolean): [boolean, (v: boolean) => void] {
  const [v, setV] = useState(() => {
    try { const x = localStorage.getItem(key); return x === null ? initial : x === '1' } catch { return initial }
  })
  const set = useCallback((nv: boolean) => {
    setV(nv)
    try { localStorage.setItem(key, nv ? '1' : '0') } catch { /* приватный режим — только на эту сессию */ }
  }, [key])
  return [v, set]
}

/** Сворачиваемый блок фильтров панели: заголовок, сводка выбора (когда свёрнут), «Сбросить» (выбор не по умолчанию) и
 *  «Свернуть / Развернуть» — как у списка объектов */
function FilterBlock({ id, title, summary, dirty, open, onOpen, onReset, children }: {
  id: string; title: string; summary: string; dirty: boolean; open: boolean; onOpen: (v: boolean) => void; onReset: () => void; children: ReactNode
}) {
  const { t } = useT()
  return (
    <section className="mf" aria-labelledby={`${id}-h`}>
      <div className="mf-head">
        <h4 id={`${id}-h`}>{title}</h4>
        {dirty && <button className="mf-reset" onClick={onReset} title={t('common.resetWhat', { what: title })}>{t('common.reset')}</button>}
        <button className="list-toggle" onClick={() => onOpen(!open)} aria-expanded={open} aria-controls={id}
          title={open ? t('map.collapseBlock', { title }) : t('map.expandBlock', { title })}>
          {open ? <>{t('common.collapse')}<IcChevronUp size={16} /></> : <>{t('common.expand')}<IcChevronDown size={16} /></>}
        </button>
      </div>
      {/* свёрнутый блок — сводка выбора во всю ширину под заголовком */}
      {!open && <div className={`mf-sum ${dirty ? 'on' : ''}`}>{summary}</div>}
      {open && <div className="mf-body" id={id}>{children}</div>}
    </section>
  )
}

/** Строка-переключатель блока фильтров: подпись со счётчиком и пояснением; нажатие на подпись тоже переключает */
function SwitchRow({ title, count, hint, on, onChange }: { title: string; count: number; hint?: string; on: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="mf-sw">
      <span className="tx"><span className="t">{title}<span className="n num">{count}</span></span>{hint && <span className="h">{hint}</span>}</span>
      <Toggle on={on} onChange={onChange} label={title} />
    </label>
  )
}

/** Карта: панель как у Такси слева, метки-ценники как у Недвижимости, категории как у Карт.
 *  Главный признак метки — «Проверка по рекламе» (цвет и знак); балл формального риска — вторичный, белым кружком */
export default function Dashboard() {
  const { mode, complexes, ads, developers, riskOf, stats } = useStore()
  const nav = useNavigate()
  const i18n = useT()
  const phone = useIsPhone() // телефон: панель — снизу, карта без отступа под неё
  const { t, tn, tr } = i18n
  const [q, setQ] = useState('')
  // три блока фильтров: выбор живёт, пока открыт экран карты; свёрнут ли блок — запоминается в браузере
  const [scrSel, setScrSel] = useState<ScreeningStatus[]>([])
  const [risk, setRisk] = useState<RiskFilter>(RISK_DEFAULT)
  const [show, setShow] = useState<ShowFlags>(SHOW_DEFAULT)
  const [fScr, setFScr] = useFlag('saq.map.f.screening', true)
  const [fRisk, setFRisk] = useFlag('saq.map.f.risk', true)
  const [fShow, setFShow] = useFlag('saq.map.f.show', true)
  const filters = useMemo<MapFilters>(() => ({ screening: scrSel, risk, show }), [scrSel, risk, show])
  // выбранный ЖК — в адресе (?sel=, без новой записи истории): «← Карта» и «назад» из карточки ЖК или публикации возвращают с выбором
  const [sp, setSp] = useSearchParams()
  const sel = sp.get('sel') ?? undefined
  const setSel = useCallback((id?: string) => setSp((p) => { const n = new URLSearchParams(p); if (id) n.set('sel', id); else n.delete('sel'); return n }, { replace: true }), [setSp])
  const [labels, setLabels] = useState(false)
  const [withAds, setWithAds] = useState(false)
  const [idle, setIdle] = useState(false)
  // легенда закрыта, пока её не откроют кнопкой; список объектов в панели сворачивается — оба выбора запоминаются в браузере
  const [legend, setLegend] = useFlag('saq.map.legend', false)
  const [listOpen, setListOpen] = useFlag('saq.map.list', true)
  const [hot, setHot] = useFlag('saq.map.hot', false)
  const heat = useMemo(() => (hot ? heatPoints(complexes) : undefined), [hot, complexes])
  // слой «Открытые данные»: реальные объекты серыми метками без балла; грузится, когда слой включён
  const [open, setOpen] = useState(false)
  const reg = useRegistry(open)
  // объект реестра, по которому уже есть карточка ЖК, на карте — меткой карточки, а не второй серой меткой
  const carded = useMemo(() => new Set(complexes.map(registryKeyOf).filter(Boolean)), [complexes])
  const points = useMemo<PlainPoint[]>(() => (open && reg.data ? reg.data.objects : [])
    .filter((o) => onMap(o) && !carded.has(o.key)).map((o) => ({ key: o.key, name: o.name, lng: o.lng!, lat: o.lat! })), [open, reg.data, carded])
  const onPoint = useCallback((key: string) => nav(`/registry/${encodeURIComponent(key)}`), [nav])

  // «Проверка по рекламе» с сервера (или из офлайн-копии): подозрение первыми. Без неё (сервер старой версии) — по формальному риску:
  // не оценённые (карточки реестра без проверенных сведений) без зоны — в зонах не считаются, на карте серые, в списке ниже
  const screenOn = anyScreening(complexes)
  const scored = useMemo(() => complexes.map((c): MapItem & { r: RiskResult; off: boolean } => {
    const r = riskOf(c)
    return { c, r, zone: r.zone, score: r.score, off: !isAssessed(r), screen: statusOf(c) }
  }).sort((a, b) => (screenOn ? compareScreening(a.c.screening, b.c.screening) : 0) || Number(a.off) - Number(b.off) || b.score - a.score), [complexes, riskOf, screenOn])
  // видимые ЖК — одни и те же для меток, списка, «Без координат» и «Показать N ЖК списком»: поиск ∧ проверка по рекламе ∧
  // формальный риск ∧ «Показывать» ∧ переключатели карты
  const visible = useMemo(() => scored.filter((x) =>
    passesMapFilters(x, filters)
    && (!q || findComplex(q, x.c, developers.find((d) => d.id === x.c.developerId)))
    && (!withAds || ads.some((a) => a.complexId === x.c.id && a.status !== 'dismissed'))
    && (!idle || x.c.idleDays >= 30)), [scored, filters, q, withAds, idle, ads, developers])
  const onMapItems = useMemo(() => visible.filter((x) => hasPoint(x.c)), [visible])
  const noCoords = visible.filter((x) => !hasPoint(x.c))
  // счётчики у чипов и переключателей — по всем ЖК на контроле, а не по уже отфильтрованным
  const sc = countByStatus(scored, (x) => x.screen)
  const rc = riskCounts(scored.map((x) => x.r), risk)
  const gc = countShowGroups(scored.map((x) => x.c))
  const anyDemo = complexes.some(isDemo)
  const queue = ads.filter((a) => a.status === 'new' || a.status === 'needs_check').length
  const s = scored.find((x) => x.c.id === sel)
  const dev = s && developers.find((d) => d.id === s.c.developerId)
  const linked = s ? ads.filter((a) => a.complexId === s.c.id) : []
  const back = from(t('nav.map'), s ? `/?sel=${s.c.id}` : '/')
  const devSc = stats?.screening?.developers ?? countByStatus(developers, statusOf)
  const discNew = stats?.screening?.discovered?.new
  // «Показать списком» — реестр с тем же поиском и проверкой по рекламе (реестр держит один признак-чип). Диапазона балла и
  // «Показывать» реестр не знает (его фильтр — по зоне, а не по баллу): тогда список шире карты — об этом подсказка у кнопки
  const listTo = (() => {
    const p = new URLSearchParams()
    if (q.trim()) p.set('q', q.trim())
    if (scrSel.length) p.set('screening', scrSel.join(','))
    const chip = risk.needsCheck ? 'check' : withAds ? 'ads' : idle ? 'idle' : ''
    if (chip) p.set('chip', chip)
    return `/complexes${p.toString() ? `?${p}` : ''}`
  })()
  const listWider = risk.min > 0 || risk.max < RISK_MAX || !risk.withNA || !isShowDefault(show) || [risk.needsCheck, withAds, idle].filter(Boolean).length > 1
  const basis = (name: string, v: Tri) => (
    <div className="tile" style={{ minHeight: 0, background: 'var(--fill)', flexDirection: 'row', alignItems: 'center', gap: 10 }}>
      <span className={`zone-pic ${v === 'yes' ? 'green' : v === 'no' ? 'red' : 'gray'}`} style={{ width: 28, height: 28 }}>
        {v === 'yes' ? <IcCheck size={14} /> : v === 'no' ? <IcClose size={14} /> : <IcHelp size={14} />}
      </span>
      <div><div className="t-xs c2">{name}</div><div className="bold t-sm">{t(v === 'yes' ? 'common.yes' : v === 'no' ? 'common.no' : 'common.noInfo')}</div></div>
    </div>
  )
  /** Строка ЖК в списке: статус, главная причина с цитатой, счётчики рекламы */
  const sub = (x: (typeof scored)[number]) => {
    const scr = x.c.screening
    if (!scr) return `${placeText(x.c) || t('common.noAddress')} · ${tr(stageText(x.c))}`
    const why = topReason(scr)
    return (
      <span className="scr-sub">
        <span className={`st ${SCREENING_TONE[scr.status]}`}>{tr(labelOf(scr))}</span>
        {why && <span className={`clamp2 ${why.quote && why.signal ? 'qt' : ''}`}>{reasonShort(why)}</span>}
        <span>{countsLineT(t, scr.counts, i18n.dayMonth)}</span>
      </span>
    )
  }

  return (
    <div className="mapscreen">
      <MapView items={onMapItems} heat={heat} points={points} onPoint={onPoint} selected={sel} onSelect={setSel} controls modes labels={labels} padLeft={phone ? 0 : 400} />

      <div className="left">
        {!s ? (
          <div className="scroll">
            <div className="card" style={{ padding: 16 }}>
              <div className="fsearch" style={{ height: 44 }}>
                <IcSearch size={18} />
                <input placeholder={t('map.search')} value={q} onChange={(e) => setQ(e.target.value)} aria-label={t('common.search')} />
                {q && <button onClick={() => setQ('')} className="c3" aria-label={t('common.clear')}><IcClose size={16} /></button>}
              </div>
              {screenOn ? (
                <FilterBlock id="mf-screening" title={t('scr.title')} summary={scrSummaryT(t, scrSel)} dirty={scrSel.length > 0}
                  open={fScr} onOpen={setFScr} onReset={() => setScrSel([])}>
                  <div className="scr-chips" role="group" aria-label={t('map.scrAria')}>
                    <button className={`chip all ${scrSel.length ? '' : 'on'}`} aria-pressed={!scrSel.length} onClick={() => setScrSel([])} title={t('map.allOnControl')}>
                      {t('common.all')}<span className="n num">{scored.length}</span>
                    </button>
                    {SCREEN_CHIPS.map((k) => (
                      <button key={k} className={`chip ${scrSel.includes(k) ? 'on' : ''}`} aria-pressed={scrSel.includes(k)} onClick={() => setScrSel(toggleStatus(scrSel, k))}
                        title={t('map.multi', { hint: t(`scr.hint.${k}`) })}>
                        <ScreeningPic status={k} />{t(`scr.filter.${k}`)}<span className="n num">{sc[k]}</span>
                      </button>
                    ))}
                  </div>
                  <div style={{ marginTop: 8 }}><Disclaimer text={stats?.screening?.disclaimer} /></div>
                </FilterBlock>
              ) : <div style={{ marginTop: 12 }}><NoScreening>{t('map.noScreeningTail')}</NoScreening></div>}

              <FilterBlock id="mf-risk" title={t('map.formalRisk')} summary={riskSummaryT(t, risk)} dirty={!isRiskDefault(risk)}
                open={fRisk} onOpen={setFRisk} onReset={() => setRisk(RISK_DEFAULT)}>
                <RangeSlider value={[risk.min, risk.max]} onChange={([min, max]) => setRisk((r) => ({ ...r, min, max }))} min={0} max={RISK_MAX}
                  track={RISK_TRACK} ticks={RISK_TICKS} labels={[t('map.minScore'), t('map.maxScore')]} fields={[t('map.from'), t('map.to')]} valueText={(n) => tn('map.score', n)} />
                <div className="mf-note" title={zonesHint(i18n)}>{t('map.scoreNote', { range: rangeText([risk.min, risk.max]), n: rc.inRange, m: rc.assessed })}</div>
                <div style={{ marginTop: 6 }}>
                  <SwitchRow title={t('map.showNA')} count={rc.na} on={risk.withNA} onChange={(v) => setRisk((r) => ({ ...r, withNA: v }))}
                    hint={risk.withNA ? t('map.naShown') : t('map.naHidden')} />
                </div>
                <div className="mini-chips" role="group" aria-label={t('common.needsCheck')}>
                  <button className={risk.needsCheck ? 'on' : ''} aria-pressed={risk.needsCheck} onClick={() => setRisk((r) => ({ ...r, needsCheck: !r.needsCheck }))}
                    title={t('map.needsCheckHint')}>
                    {t('map.onlyNeedsCheck')}<span className="n num">{rc.needsCheck}</span>
                  </button>
                </div>
              </FilterBlock>

              <FilterBlock id="mf-show" title={t('map.show')} summary={showSummaryT(t, show)} dirty={!isShowDefault(show)}
                open={fShow} onOpen={setFShow} onReset={() => setShow(SHOW_DEFAULT)}>
                {SHOW_GROUPS.map((g) => (
                  <SwitchRow key={g} title={t(`show.label.${g}`)} count={gc[g]} hint={t(`show.hint.${g}`)} on={show[g]} onChange={(v) => setShow((x) => ({ ...x, [g]: v }))} />
                ))}
                <div className="mf-note">{t('map.alwaysShown', { n: gc.other })}</div>
              </FilterBlock>
            </div>

            {screenOn && (
              <div className="card" style={{ padding: '6px 0' }}>
                <Cell inset to={`/developers?screening=suspect`} icon={<span className="ico"><IcUsers size={18} /></span>} title={t('nav.developers')}
                  sub={<>{devSc.suspect ? <b style={{ color: 'var(--red-ink)' }}>{t('map.devSuspect', { n: devSc.suspect })}</b> : t('map.devNoSuspect')} · {t('map.devCheck', { n: devSc.check })} · {t('map.devOk', { n: devSc.ok })}</>} chevron />
                <Cell inset to="/discovered" icon={<span className="ico"><IcMegaphone size={18} /></span>} title={t('foot.discovered')}
                  sub={discNew === undefined ? t('map.discSub') : discNew ? <b style={{ color: 'var(--amber-ink)' }}>{tn('map.discNew', discNew)}</b> : t('map.discNone')}
                  end={discNew ? <span className="badge">{discNew}</span> : undefined} chevron />
              </div>
            )}

            <div className="card">
              {/* список объектов сворачивается целиком (вместе с «Без координат»): фильтры и переключатели панели остаются */}
              <div className="card-head" style={{ paddingBottom: listOpen ? 4 : 16 }}>
                <h3 style={{ fontSize: 17 }}>{tn('map.objects', visible.length)}</h3>
                <button className="list-toggle" onClick={() => setListOpen(!listOpen)} aria-expanded={listOpen} aria-controls="map-objects"
                  title={listOpen ? t('map.collapseList') : t('map.expandList')}>
                  {listOpen ? <>{t('common.collapse')}<IcChevronUp size={16} /></> : <>{t('common.expand')}<IcChevronDown size={16} /></>}
                </button>
              </div>
              {listOpen && <div id="map-objects">
              <div className="row t-xs c2" style={{ gap: 6, padding: '0 24px 4px' }}>{anyDemo && <Origin kind="demo" label={t('map.demo')} title={t('map.demoHint')} />}{complexes.some(isRegistry) && <Origin kind="registry" label={t('map.registry')} />}{screenOn ? t('map.suspectFirst') : t('map.byRisk')}</div>
              {visible.length === 0 && <div className="empty">{t('map.nothing')}</div>}
              {visible.filter((x) => hasPoint(x.c)).map((x) => (
                <Cell key={x.c.id} onClick={() => setSel(x.c.id)} inset
                  icon={x.screen ? <ScreeningPic status={x.screen} size={40} /> : <img className="th" src={coverSrc(x.c)} alt="" style={{ width: 44, height: 44, borderRadius: 10 }} />}
                  title={<span className="bold">{x.c.name}{isAdvertised(x.c) && <span className="c2" style={{ fontWeight: 400 }}>{t('map.byAds')}</span>}</span>} sub={sub(x)}
                  end={!screenOn || !x.off ? <RiskScore r={x.r} /> : undefined} />
              ))}
              </div>}
            </div>

            {listOpen && noCoords.length > 0 && (
              <div className="card">
                <div className="card-head" style={{ paddingBottom: 0 }}><h3 style={{ fontSize: 17 }}>{t('map.noCoords', { n: noCoords.length })}</h3></div>
                <p className="t-xs c2" style={{ padding: '0 24px 6px' }}>{t('map.noCoordsHint')}</p>
                {noCoords.map((x) => (
                  <Cell key={x.c.id} inset to={`/complexes/${x.c.id}`}
                    icon={x.screen ? <ScreeningPic status={x.screen} size={40} /> : <span className="ico"><IcMapPin size={18} /></span>}
                    title={<span className="bold">{x.c.name}</span>} sub={sub(x)} chevron />
                ))}
              </div>
            )}

            <div className="card">
              <Cell title={t('map.labels')} end={<Toggle on={labels} onChange={setLabels} label={t('map.labelsShort')} />} />
              <Cell title={t('map.withAds')} end={<Toggle on={withAds} onChange={setWithAds} label={t('map.withAds')} />} />
              <Cell title={t('map.idle')} end={<Toggle on={idle} onChange={setIdle} label={t('map.idleShort')} />} />
              <Cell title={t('hot.toggle')} sub={t('hot.toggleSub')} end={<Toggle on={hot} onChange={setHot} label={t('hot.toggle')} />} />
              <Cell title={t('map.openData')} end={<Toggle on={open} onChange={setOpen} label={t('map.openData')} />}
                sub={!open ? t('map.openDataOff')
                  : reg.err && !reg.data ? t('map.openDataErr', { e: reg.err })
                      : reg.data ? `${t('map.openDataOn', { a: points.length, b: (reg.total ?? 0) - points.length })}${mode === 'snapshot' ? t('map.builtIn') : ''}`
                        : t('common.loading')} />
              {open && reg.data && <Cell inset title={<span className="link t-sm">{t('map.openDataList')}</span>} chevron to="/complexes?layer=open" />}
            </div>
          </div>
        ) : (
          <div className="scroll fade" key={s.c.id}>
            <div className="card">
              <Cell onClick={() => setSel(undefined)} icon={<IcChevronLeft />} title={t('map.allObjects')} />
              {coverSrc(s.c) && (
                <div className="ph" style={{ height: 160, margin: '0 12px', borderRadius: 16 }}>
                  <img src={coverSrc(s.c)} alt="" />
                  <div className="tl"><span className="tag white">{tr(stageText(s.c))}</span>{s.r.needsCheck && <span className="tag white">{t('common.needsCheckLow')}</span>}</div>
                </div>
              )}
              <div className="card-pad" style={{ paddingTop: coverSrc(s.c) ? 14 : 4 }}>
                <div className="t-xs c2">{dev?.name}</div>
                <div className="row between" style={{ alignItems: 'flex-start', marginTop: 2 }}>
                  <h2 className="t-h2">{t('common.complex', { name: s.c.name })}</h2>
                  {(!screenOn || !s.off) && <RiskScore r={s.r} />}
                </div>
                <div className="t-sm c2" style={{ marginTop: 4 }}>{placeText(s.c) || t('common.noAddress')}{!hasPoint(s.c) && t('map.noCoordsShort')}</div>
                <div className="row wrap-f" style={{ marginTop: 10, gap: 6 }}>
                  {s.c.screening && <ScreeningTag s={s.c.screening} short />}
                  <RiskZone r={s.r} />
                  {isDemo(s.c) && <Origin kind="demo" label={t('map.originDemo')} />}
                  {isRegistry(s.c) && <Origin kind="registry" label={t('map.originRegistry')} />}
                  {isAdvertised(s.c) && <Origin kind="advertised" label={t('map.originAdvertised')} />}
                </div>
                {!s.c.screening && <p className="t-sm" style={{ marginTop: 10, lineHeight: '19px' }}>{s.r.summary}</p>}
              </div>
            </div>
            {s.c.screening && (
              <div className="card" style={{ padding: 8 }}>
                <ScreeningBlock s={s.c.screening} permit={s.c.permit} state={back} compact />
              </div>
            )}
            <div className="card" style={{ padding: 8 }}>
              <div className="t-xs c2" style={{ padding: '4px 8px 6px' }}>{t('map.basisTitle')}{!s.off ? t('map.ofHundred', { n: s.r.score }) : t('map.notAssessed')}</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                {basis(t('basis.guarantee'), s.c.basis.guarantee)}{basis(t('basis.mio'), s.c.basis.mio)}
                {basis(t('basis.bvu'), s.c.basis.bvu)}{basis(t('basis.gask'), s.c.inPublishedList)}
              </div>
            </div>
            <div className="card">
              <Cell icon={<span className="ico"><IcMegaphone size={18} /></span>} title={t('nav.ads')} sub={linked.length ? t('map.adsLast', { n: linked.length, date: i18n.dayMonth(linked[0].date) }) : t('common.notFoundShort')} chevron to={`/complexes/${s.c.id}?tab=ads`} inset />
              <Cell icon={<span className="ico"><IcCamera size={18} /></span>} title={t('map.photos')} sub={s.c.idleDays ? t('map.idleDays', { n: s.c.idleDays }) : s.c.photos.length ? tn('map.shots', s.c.photos.length) : t('map.noShots')} chevron to={`/complexes/${s.c.id}?tab=photos`} inset />
              {hasPoint(s.c) && <Cell icon={<span className="ico"><IcGlobe size={18} /></span>} title={t('map.sat')} sub={t('map.satSub')} chevron to={`/complexes/${s.c.id}?tab=sat`} inset />}
              <Cell icon={<span className="ico"><IcUsers size={18} /></span>} title={t('map.developer')} sub={dev ? `${dev.name}${dev.bin ? t('map.bin', { bin: dev.bin }) : ''}` : t('map.devNone')}
                end={dev?.screening ? <span className={`scr-pic ${dev.screening.status}`} title={tr(dev.screening.label)} style={{ width: 22, height: 22, fontSize: 12 }}>{SCREENING_GLYPH[dev.screening.status]}</span> : undefined}
                chevron to={dev ? `/developers?dev=${dev.id}` : '/developers'} inset />
            </div>
            {linked.slice(0, 2).map((a) => (
              <Link key={a.id} to={`/ads/${a.id}`} state={back} className="card card-pad" style={{ display: 'block' }}>
                <div className="row between"><div className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={22} /><span className="t-sm c2">{a.account}</span></div><StatusTag s={a.status} /></div>
                <p className="t-sm clamp3" style={{ marginTop: 10, lineHeight: '19px' }}><Highlight text={a.text} /></p>
                {/* привязка к этому ЖК — гипотеза ИИ: в балл объекта публикация не входит, пока аналитик её не примет */}
                {!linkConfirmed(a) && <div style={{ marginTop: 8 }}><LinkMark a={a} label={t('common.aiLink')} /></div>}
              </Link>
            ))}
          </div>
        )}
        {/* в очередь ведёт плашка справа внизу — одна кнопка, а не две */}
        {s && (
          <div className="dock">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}><button className="btn y" onClick={() => nav(`/complexes/${s.c.id}`, { state: back })}>{t('map.card')}</button><button className="btn g" onClick={() => nav(`/complexes/${s.c.id}?tab=ads`, { state: back })}>{t('map.adsN', { n: linked.length })}</button></div>
          </div>
        )}
      </div>

      {/* кнопки над картой справа от панели: список ЖК отдельной страницей, легенда */}
      <div className="maptools" style={{ left: 412 }}>
        <Link to={listTo} className="mapbtn" title={listWider ? t('map.listWider') : undefined}>
          <IcList size={18} />{t('map.asList', { n: visible.length })}
        </Link>
        {screenOn && <button className={`mapbtn ${legend ? 'on' : ''}`} onClick={() => setLegend(!legend)} aria-pressed={legend} aria-controls="map-legend"><IcInfo size={18} />{t('map.legend')}</button>}
      </div>

      {screenOn && legend && (
        <div className="mlegend" id="map-legend" role="note" aria-label={t('map.legendAria')} style={{ left: 412 }}>
          <div className="lh">{t('scr.title')}<button className="c3" onClick={() => setLegend(false)} aria-label={t('map.hideLegend')}><IcClose size={16} /></button></div>
          {(['ok', 'check', 'suspect', 'unknown'] as const).map((k) => (
            <div key={k} className="lr" title={k === 'ok' ? t('scr.okLegend') : undefined}><span className={`lpin ${k}`}><span className="g">{SCREENING_GLYPH[k]}</span></span><span>{t(`scr.legend.${k}`)}</span></div>
          ))}
          <div className="lr"><span className="lpin check"><span className="g">?</span><span className="sc">57</span></span><span>{t('map.legendScore')}</span></div>
          <div className="lnote" title={tr(stats?.screening?.disclaimer || DISCLAIMER)}>{tr(DISCLAIMER_SHORT)}{noCoords.length > 0 && t('map.legendNoCoords', { n: noCoords.length })}</div>
        </div>
      )}

      {hot && <HotspotPanel complexes={complexes} onClose={() => setHot(false)} />}

      <Link to="/queue" className="promo dark" style={{ right: 16, bottom: 32, width: 280 }}>
        <div className="tx" style={{ padding: '18px 20px' }}>
          <div className="cond" style={{ fontSize: 26, lineHeight: '26px' }}>{queue ? <>{tn('map.promoN', queue)}<br />{tn('map.promoWait', queue)}</> : <>{t('map.promoEmpty1')}<br />{t('map.promoEmpty2')}</>}</div>
          <span className="mini">{t('map.openQueue')} <IcChevronRight size={14} /></span>
        </div>
      </Link>
    </div>
  )
}
