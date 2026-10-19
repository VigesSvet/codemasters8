import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { LinkMark } from '../components/Hypothesis'
import MapView, { type MapItem, type PlainPoint } from '../components/MapView'
import { Origin } from '../components/Provenance'
import RangeSlider from '../components/RangeSlider'
import { Disclaimer, NoScreening, ScreeningBlock, ScreeningPic, ScreeningTag } from '../components/Screening'
import { Cell, Highlight, PlatformIcon, RiskScore, RiskZone, StatusTag, Toggle, fmtShort, plural } from '../components/ui'
import type { RiskResult, ScreeningStatus, Tri } from '../data/types'
import { findComplex } from '../lib/find'
import { from } from '../lib/nav'
import { isAdvertised, isDemo, isRegistry, onMap, placeText, registryKeyOf, stageText, useRegistry } from '../lib/provenance'
import {
  RED_FROM, RISK_DEFAULT, RISK_MAX, SHOW_DEFAULT, SHOW_GROUPS, SHOW_HINT, SHOW_LABEL, YELLOW_FROM, countShowGroups, isRiskDefault, isShowDefault,
  passesMapFilters, riskCounts, riskSummary, screeningSummary, showSummary, toggleStatus, type MapFilters, type RiskFilter, type ShowFlags,
} from '../lib/mapFilters'
import { bandGradient, rangeText } from '../lib/range'
import { isAssessed, linkConfirmed, model } from '../lib/risk'
import {
  LEGEND_LABEL, OK_LEGEND, SCREENING_FILTER_LABEL, SCREENING_GLYPH, SCREENING_HINT, SCREENING_TONE, anyScreening, compareScreening, countByStatus,
  countsLine, hasPoint, labelOf, reasonShort, statusOf, topReason,
} from '../lib/screening'
import { useStore } from '../lib/store'
import { IcCheck, IcChevronDown, IcChevronLeft, IcChevronRight, IcChevronUp, IcClose, IcHelp, IcInfo, IcList, IcMapPin, IcMegaphone, IcSearch, IcCamera, IcUsers } from '../ui/icons'

/** Чипы «Проверки по рекламе» после «Все» — в порядке задачи: без замечаний, проверить, подозрение, нет данных; можно несколько */
const SCREEN_CHIPS: ScreeningStatus[] = ['ok', 'check', 'suspect', 'unknown']
/** Полоса балла формального риска — цвета зон по порогам модели: зелёная, жёлтая (янтарная, как балл), красная; мягкие переходы */
const RISK_TRACK = bandGradient([
  { from: 0, to: YELLOW_FROM, color: 'var(--green)' },
  { from: YELLOW_FROM, to: RED_FROM, color: 'var(--amber)' },
  { from: RED_FROM, to: RISK_MAX, color: 'var(--red)' },
], 0, RISK_MAX, 8)
const RISK_TICKS = [0, YELLOW_FROM, RED_FROM, RISK_MAX]
/** Подсказка к полосе: зоны по баллу из модели риска (красной зоне нужны ещё и подтверждённые R1 и R2) */
const ZONES_HINT = `Цвет полосы — зоны по баллу: ${model.zones.items.map((z) => `${z.name.toLowerCase()} ${z.range}`).join(', ')}. ${model.zones.items.find((z) => z.zone === 'red')?.rule ?? ''}`
const scoreText = (n: number) => `${n} ${plural(n, ['балл', 'балла', 'баллов'])}`

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
  return (
    <section className="mf" aria-labelledby={`${id}-h`}>
      <div className="mf-head">
        <h4 id={`${id}-h`}>{title}</h4>
        {dirty && <button className="mf-reset" onClick={onReset} title={`Сбросить: ${title}`}>Сбросить</button>}
        <button className="list-toggle" onClick={() => onOpen(!open)} aria-expanded={open} aria-controls={id}
          title={open ? `Свернуть блок «${title}» — фильтр останется` : `Развернуть блок «${title}»`}>
          {open ? <>Свернуть<IcChevronUp size={16} /></> : <>Развернуть<IcChevronDown size={16} /></>}
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
  const back = from('Карта', s ? `/?sel=${s.c.id}` : '/')
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
      <div><div className="t-xs c2">{name}</div><div className="bold t-sm">{v === 'yes' ? 'Есть' : v === 'no' ? 'Нет' : 'Нет сведений'}</div></div>
    </div>
  )
  /** Строка ЖК в списке: статус, главная причина с цитатой, счётчики рекламы */
  const sub = (x: (typeof scored)[number]) => {
    const scr = x.c.screening
    if (!scr) return `${placeText(x.c) || 'адрес не указан'} · ${stageText(x.c)}`
    const why = topReason(scr)
    return (
      <span className="scr-sub">
        <span className={`st ${SCREENING_TONE[scr.status]}`}>{labelOf(scr)}</span>
        {why && <span className={`clamp2 ${why.quote && why.signal ? 'qt' : ''}`}>{reasonShort(why)}</span>}
        <span>{countsLine(scr.counts)}</span>
      </span>
    )
  }

  return (
    <div className="mapscreen">
      <MapView items={onMapItems} points={points} onPoint={onPoint} selected={sel} onSelect={setSel} controls labels={labels} padLeft={400} />

      <div className="left">
        {!s ? (
          <div className="scroll">
            <div className="card" style={{ padding: 16 }}>
              <div className="fsearch" style={{ height: 44 }}>
                <IcSearch size={18} />
                <input placeholder="ЖК, адрес или БИН застройщика" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Поиск" />
                {q && <button onClick={() => setQ('')} className="c3" aria-label="Очистить"><IcClose size={16} /></button>}
              </div>
              {screenOn ? (
                <FilterBlock id="mf-screening" title="Проверка по рекламе" summary={screeningSummary(scrSel)} dirty={scrSel.length > 0}
                  open={fScr} onOpen={setFScr} onReset={() => setScrSel([])}>
                  <div className="scr-chips" role="group" aria-label="Проверка по рекламе: можно выбрать несколько статусов">
                    <button className={`chip all ${scrSel.length ? '' : 'on'}`} aria-pressed={!scrSel.length} onClick={() => setScrSel([])} title="Все ЖК на контроле">
                      Все<span className="n num">{scored.length}</span>
                    </button>
                    {SCREEN_CHIPS.map((k) => (
                      <button key={k} className={`chip ${scrSel.includes(k) ? 'on' : ''}`} aria-pressed={scrSel.includes(k)} onClick={() => setScrSel(toggleStatus(scrSel, k))}
                        title={`${SCREENING_HINT[k]} — можно выбрать несколько`}>
                        <ScreeningPic status={k} />{SCREENING_FILTER_LABEL[k]}<span className="n num">{sc[k]}</span>
                      </button>
                    ))}
                  </div>
                  <div style={{ marginTop: 8 }}><Disclaimer text={stats?.screening?.disclaimer} /></div>
                </FilterBlock>
              ) : <div style={{ marginTop: 12 }}><NoScreening> — метки показывают формальный риск.</NoScreening></div>}

              <FilterBlock id="mf-risk" title="Формальный риск" summary={riskSummary(risk)} dirty={!isRiskDefault(risk)}
                open={fRisk} onOpen={setFRisk} onReset={() => setRisk(RISK_DEFAULT)}>
                <RangeSlider value={[risk.min, risk.max]} onChange={([min, max]) => setRisk((r) => ({ ...r, min, max }))} min={0} max={RISK_MAX}
                  track={RISK_TRACK} ticks={RISK_TICKS} labels={['Минимальный балл', 'Максимальный балл']} valueText={scoreText} />
                <div className="mf-note" title={ZONES_HINT}>Балл {rangeText([risk.min, risk.max])}: {rc.inRange} из {rc.assessed} ЖК с оценкой риска</div>
                <div style={{ marginTop: 6 }}>
                  <SwitchRow title="Показывать ЖК без оценки риска" count={rc.na} on={risk.withNA} onChange={(v) => setRisk((r) => ({ ...r, withNA: v }))}
                    hint={risk.withNA ? 'Балла нет — видны при любом диапазоне' : 'Скрыты — видны только ЖК с баллом в диапазоне'} />
                </div>
                <div className="mini-chips" role="group" aria-label="Нужна проверка">
                  <button className={risk.needsCheck ? 'on' : ''} aria-pressed={risk.needsCheck} onClick={() => setRisk((r) => ({ ...r, needsCheck: !r.needsCheck }))}
                    title="Только ЖК, где для оценки не хватает проверенных сведений">
                    только «нужна проверка»<span className="n num">{rc.needsCheck}</span>
                  </button>
                </div>
              </FilterBlock>

              <FilterBlock id="mf-show" title="Показывать" summary={showSummary(show)} dirty={!isShowDefault(show)}
                open={fShow} onOpen={setFShow} onReset={() => setShow(SHOW_DEFAULT)}>
                {SHOW_GROUPS.map((g) => (
                  <SwitchRow key={g} title={SHOW_LABEL[g]} count={gc[g]} hint={SHOW_HINT[g]} on={show[g]} onChange={(v) => setShow((x) => ({ ...x, [g]: v }))} />
                ))}
                <div className="mf-note">Требуют проверки, подозрение и нет данных видны всегда · {gc.other}</div>
              </FilterBlock>
            </div>

            {screenOn && (
              <div className="card" style={{ padding: '6px 0' }}>
                <Cell inset to={`/developers?screening=suspect`} icon={<span className="ico"><IcUsers size={18} /></span>} title="Застройщики"
                  sub={<>{devSc.suspect ? <b style={{ color: 'var(--red-ink)' }}>подозрение — {devSc.suspect}</b> : 'подозрений нет'} · требуют проверки — {devSc.check} · без замечаний — {devSc.ok}</>} chevron />
                <Cell inset to="/discovered" icon={<span className="ico"><IcMegaphone size={18} /></span>} title="Объекты из рекламы"
                  sub={discNew === undefined ? 'ЖК, которых нет в реестре и справочнике' : discNew ? <b style={{ color: 'var(--amber-ink)' }}>{discNew} {plural(discNew, ['новый', 'новых', 'новых'])} — ждут решения</b> : 'новых нет'}
                  end={discNew ? <span className="badge">{discNew}</span> : undefined} chevron />
              </div>
            )}

            <div className="card">
              {/* список объектов сворачивается целиком (вместе с «Без координат»): фильтры и переключатели панели остаются */}
              <div className="card-head" style={{ paddingBottom: listOpen ? 4 : 16 }}>
                <h3 style={{ fontSize: 17 }}>{visible.length} {plural(visible.length, ['объект', 'объекта', 'объектов'])}</h3>
                <button className="list-toggle" onClick={() => setListOpen(!listOpen)} aria-expanded={listOpen} aria-controls="map-objects"
                  title={listOpen ? 'Свернуть список объектов — фильтры останутся' : 'Показать список объектов'}>
                  {listOpen ? <>Свернуть<IcChevronUp size={16} /></> : <>Развернуть<IcChevronDown size={16} /></>}
                </button>
              </div>
              {listOpen && <div id="map-objects">
              <div className="row t-xs c2" style={{ gap: 6, padding: '0 24px 4px' }}>{anyDemo && <Origin kind="demo" label="демо-сценарии" title="Вымышленные ЖК: на них показана модель риска. Реальные объекты — слой «Открытые данные», без балла" />}{complexes.some(isRegistry) && <Origin kind="registry" label="реестр" />}{screenOn ? 'подозрение первыми' : 'по риску'}</div>
              {visible.length === 0 && <div className="empty">Ничего не найдено</div>}
              {visible.filter((x) => hasPoint(x.c)).map((x) => (
                <Cell key={x.c.id} onClick={() => setSel(x.c.id)} inset
                  icon={x.screen ? <ScreeningPic status={x.screen} size={40} /> : <img className="th" src={x.c.cover || undefined} alt="" style={{ width: 44, height: 44, borderRadius: 10 }} />}
                  title={<span className="bold">{x.c.name}{isAdvertised(x.c) && <span className="c2" style={{ fontWeight: 400 }}> · по рекламе</span>}</span>} sub={sub(x)}
                  end={!screenOn || !x.off ? <RiskScore r={x.r} /> : undefined} />
              ))}
              </div>}
            </div>

            {listOpen && noCoords.length > 0 && (
              <div className="card">
                <div className="card-head" style={{ paddingBottom: 0 }}><h3 style={{ fontSize: 17 }}>Без координат · {noCoords.length}</h3></div>
                <p className="t-xs c2" style={{ padding: '0 24px 6px' }}>Точки на карте нет — в карточке ЖК сверьте адрес в 2ГИС</p>
                {noCoords.map((x) => (
                  <Cell key={x.c.id} inset to={`/complexes/${x.c.id}`}
                    icon={x.screen ? <ScreeningPic status={x.screen} size={40} /> : <span className="ico"><IcMapPin size={18} /></span>}
                    title={<span className="bold">{x.c.name}</span>} sub={sub(x)} chevron />
                ))}
              </div>
            )}

            <div className="card">
              <Cell title="Названия на карте" end={<Toggle on={labels} onChange={setLabels} label="Названия" />} />
              <Cell title="Только с рекламой" end={<Toggle on={withAds} onChange={setWithAds} label="Только с рекламой" />} />
              <Cell title="Стройка стоит 30+ дней" end={<Toggle on={idle} onChange={setIdle} label="Простой" />} />
              <Cell title="Открытые данные" end={<Toggle on={open} onChange={setOpen} label="Открытые данные" />}
                sub={!open ? 'Реальные объекты — серые метки без балла'
                  : reg.err && !reg.data ? `Не загрузились: ${reg.err}`
                      : reg.data ? `${points.length} на карте, ${(reg.total ?? 0) - points.length} без координат · без балла${mode === 'snapshot' ? ' · встроенная копия' : ''}`
                        : 'Загружаем…'} />
              {open && reg.data && <Cell inset title={<span className="link t-sm">Список открытых данных</span>} chevron to="/complexes?layer=open" />}
            </div>
          </div>
        ) : (
          <div className="scroll fade" key={s.c.id}>
            <div className="card">
              <Cell onClick={() => setSel(undefined)} icon={<IcChevronLeft />} title="Все объекты" />
              {s.c.cover && (
                <div className="ph" style={{ height: 160, margin: '0 12px', borderRadius: 16 }}>
                  <img src={s.c.cover} alt="" />
                  <div className="tl"><span className="tag white">{stageText(s.c)}</span>{s.r.needsCheck && <span className="tag white">нужна проверка</span>}</div>
                </div>
              )}
              <div className="card-pad" style={{ paddingTop: s.c.cover ? 14 : 4 }}>
                <div className="t-xs c2">{dev?.name}</div>
                <div className="row between" style={{ alignItems: 'flex-start', marginTop: 2 }}>
                  <h2 className="t-h2">ЖК «{s.c.name}»</h2>
                  {(!screenOn || !s.off) && <RiskScore r={s.r} />}
                </div>
                <div className="t-sm c2" style={{ marginTop: 4 }}>{placeText(s.c) || 'адрес не указан'}{!hasPoint(s.c) && ' · координат нет'}</div>
                <div className="row wrap-f" style={{ marginTop: 10, gap: 6 }}>
                  {s.c.screening && <ScreeningTag s={s.c.screening} short />}
                  <RiskZone r={s.r} />
                  {isDemo(s.c) && <Origin kind="demo" label="Демо-сценарий (вымышленный ЖК)" />}
                  {isRegistry(s.c) && <Origin kind="registry" label="Открытый реестр · не проверено" />}
                  {isAdvertised(s.c) && <Origin kind="advertised" label="По рекламе · не проверено" />}
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
              <div className="t-xs c2" style={{ padding: '4px 8px 6px' }}>Формальный риск: основания{!s.off ? ` · ${s.r.score} из 100` : ' · не оценивается'}</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                {basis('Гарантия ЕО', s.c.basis.guarantee)}{basis('Разрешение МИО', s.c.basis.mio)}
                {basis('Банк (БВУ)', s.c.basis.bvu)}{basis('Перечень ГАСК', s.c.inPublishedList)}
              </div>
            </div>
            <div className="card">
              <Cell icon={<span className="ico"><IcMegaphone size={18} /></span>} title="Реклама" sub={linked.length ? `${linked.length} публ. · последняя ${fmtShort(linked[0].date)}` : 'Не найдено'} chevron to={`/complexes/${s.c.id}?tab=ads`} inset />
              <Cell icon={<span className="ico"><IcCamera size={18} /></span>} title="Фото-мониторинг" sub={s.c.idleDays ? `Нет активности ${s.c.idleDays} дн.` : s.c.photos.length ? `${s.c.photos.length} ${plural(s.c.photos.length, ['снимок', 'снимка', 'снимков'])} · стройка идёт` : 'Снимков пока нет'} chevron to={`/complexes/${s.c.id}?tab=photos`} inset />
              <Cell icon={<span className="ico"><IcUsers size={18} /></span>} title="Застройщик" sub={dev ? `${dev.name}${dev.bin ? ` · БИН ${dev.bin}` : ''}` : 'Не указан'}
                end={dev?.screening ? <span className={`scr-pic ${dev.screening.status}`} title={dev.screening.label} style={{ width: 22, height: 22, fontSize: 12 }}>{SCREENING_GLYPH[dev.screening.status]}</span> : undefined}
                chevron to={dev ? `/developers?dev=${dev.id}` : '/developers'} inset />
            </div>
            {linked.slice(0, 2).map((a) => (
              <Link key={a.id} to={`/ads/${a.id}`} state={back} className="card card-pad" style={{ display: 'block' }}>
                <div className="row between"><div className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={22} /><span className="t-sm c2">{a.account}</span></div><StatusTag s={a.status} /></div>
                <p className="t-sm clamp3" style={{ marginTop: 10, lineHeight: '19px' }}><Highlight text={a.text} /></p>
                {/* привязка к этому ЖК — гипотеза ИИ: в балл объекта публикация не входит, пока аналитик её не примет */}
                {!linkConfirmed(a) && <div style={{ marginTop: 8 }}><LinkMark a={a} label="привязка — гипотеза ИИ" /></div>}
              </Link>
            ))}
          </div>
        )}
        {/* в очередь ведёт плашка справа внизу — одна кнопка, а не две */}
        {s && (
          <div className="dock">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}><button className="btn y" onClick={() => nav(`/complexes/${s.c.id}`, { state: back })}>Карточка ЖК</button><button className="btn g" onClick={() => nav(`/complexes/${s.c.id}?tab=ads`, { state: back })}>Реклама · {linked.length}</button></div>
          </div>
        )}
      </div>

      {/* кнопки над картой справа от панели: список ЖК отдельной страницей, легенда */}
      <div className="maptools" style={{ left: 412 }}>
        <Link to={listTo} className="mapbtn" title={listWider ? 'В списке — поиск, проверка по рекламе и один признак; диапазон балла и «Показывать» список не применяет — ЖК может быть больше' : undefined}>
          <IcList size={18} />Показать {visible.length} ЖК списком
        </Link>
        {screenOn && <button className={`mapbtn ${legend ? 'on' : ''}`} onClick={() => setLegend(!legend)} aria-pressed={legend} aria-controls="map-legend"><IcInfo size={18} />Легенда</button>}
      </div>

      {screenOn && legend && (
        <div className="mlegend" id="map-legend" role="note" aria-label="Легенда карты" style={{ left: 412 }}>
          <div className="lh">Проверка по рекламе<button className="c3" onClick={() => setLegend(false)} aria-label="Скрыть легенду"><IcClose size={16} /></button></div>
          {(['ok', 'check', 'suspect', 'unknown'] as const).map((k) => (
            <div key={k} className="lr" title={k === 'ok' ? OK_LEGEND : undefined}><span className={`lpin ${k}`}><span className="g">{SCREENING_GLYPH[k]}</span></span><span>{LEGEND_LABEL[k]}</span></div>
          ))}
          <div className="lr"><span className="lpin check"><span className="g">?</span><span className="sc">57</span></span><span>Балл формального риска, кольцо — зона (если риск оценён)</span></div>
          <div className="lnote" title={stats?.screening?.disclaimer || 'Предварительная автоматическая оценка по рекламе и открытым данным — не вывод о нарушении'}>Предварительная оценка — не вывод о нарушении{noCoords.length > 0 && <> · без координат: {noCoords.length}, в списке слева</>}</div>
        </div>
      )}

      <Link to="/queue" className="promo dark" style={{ right: 16, bottom: 32, width: 280 }}>
        <div className="tx" style={{ padding: '18px 20px' }}>
          <div className="cond" style={{ fontSize: 26, lineHeight: '26px' }}>{queue ? <>{queue} {plural(queue, ['публикация', 'публикации', 'публикаций'])}<br />{plural(queue, ['ждёт', 'ждут', 'ждут'])} решения</> : <>Очередь<br />проверки пуста</>}</div>
          <span className="mini">Открыть очередь <IcChevronRight size={14} /></span>
        </div>
      </Link>
    </div>
  )
}
