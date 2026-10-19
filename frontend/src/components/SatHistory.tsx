// «Спутник» в карточке ЖК: архив снимков Esri Wayback по точке объекта. Шторка «до / после» между двумя датами,
// лента времени с периодом стройки по документам (начало → плановый ввод) и покадровый просмотр по годам.
// Тайлы мельче 17-го масштаба в Казахстане архив не отдаёт (404 без CORS) — дальше MapLibre увеличивает 17-й.
import { useEffect, useMemo, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import type { Complex } from '../data/types'
import { SAT_ZOOM, WAYBACK_ATTR, shotDate, shotsAt, tileUrl, type SatShot } from '../lib/wayback'
import { IcMinus, IcPlus } from '../ui/icons'
import SatStage from './SatStage'

const fmt = (d: string) => new Date(`${d.slice(0, 10)}T12:00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' })
const year = (d: string) => Number(d.slice(0, 4))
const t = (d: string) => new Date(`${d.slice(0, 10)}T12:00:00`).getTime()

function style(id: number): maplibregl.StyleSpecification {
  return {
    version: 8,
    sources: { sat: { type: 'raster', tiles: [tileUrl(id)], tileSize: 256, maxzoom: 17, attribution: WAYBACK_ATTR } },
    layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#2a2a2a' } }, { id: 'sat', type: 'raster', source: 'sat' }],
  }
}

/** Карта одного выпуска; камера — общая с соседней (onMove) */
function useSatMap(el: React.RefObject<HTMLDivElement | null>, center: [number, number], id: number | null, attribution: boolean) {
  const map = useRef<maplibregl.Map | null>(null)
  const shown = useRef<number | null>(null)
  useEffect(() => {
    if (!el.current || id === null) return
    if (map.current) return
    const m = new maplibregl.Map({ container: el.current, style: style(id), center, zoom: SAT_ZOOM, minZoom: 13, maxZoom: 19, attributionControl: attribution ? { compact: true } : false, dragRotate: false, pitchWithRotate: false })
    m.touchZoomRotate.disableRotation()
    // точка ЖК — кольцо, чтобы на любом снимке было видно, куда смотреть
    const ring = document.createElement('div'); ring.className = 'sat-ring'
    new maplibregl.Marker({ element: ring }).setLngLat(center).addTo(m)
    map.current = m
    shown.current = id
    const ro = new ResizeObserver(() => m.resize())
    ro.observe(el.current)
    return () => { ro.disconnect(); m.remove(); map.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id === null])
  // смена выпуска — только источник тайлов, камера остаётся
  useEffect(() => {
    const m = map.current
    if (!m || id === null || shown.current === id) return
    shown.current = id
    const apply = () => { (m.getSource('sat') as maplibregl.RasterTileSource | undefined)?.setTiles([tileUrl(id)]); m.triggerRepaint() }
    if (m.isStyleLoaded()) apply(); else m.once('load', apply)
  }, [id])
  return map
}

export default function SatHistory({ c }: { c: Complex }) {
  const center: [number, number] = [c.lng!, c.lat!]
  const [shots, setShots] = useState<SatShot[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [a, setA] = useState<number | null>(null) // «до»
  const [b, setB] = useState<number | null>(null) // «после»
  const [pickFor, setPickFor] = useState<'a' | 'b'>('b')
  const [cut, setCut] = useState(50)
  const [play, setPlay] = useState(false)
  const elA = useRef<HTMLDivElement>(null)
  const elB = useRef<HTMLDivElement>(null)
  const view = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let alive = true
    setShots(null); setErr(null)
    shotsAt(c.lat!, c.lng!).then((s) => {
      if (!alive) return
      setShots(s)
      // по умолчанию: «после» — свежий снимок, «до» — последний снимок до начала стройки; если и свежий снят до
      // начала (стройка моложе архива) или начала нет — самый старый снимок, чтобы было что сравнить
      const before = c.start ? s.find((x) => shotDate(x) < c.start) : undefined
      setB(s[0]?.id ?? null)
      setA((before && before.id !== s[0]?.id ? before : s[s.length - 1])?.id ?? null)
    }, (e: unknown) => { if (alive) setErr(e instanceof Error ? e.message : String(e)) })
    return () => { alive = false }
  }, [c.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const mapA = useSatMap(elA, center, a, false)
  const mapB = useSatMap(elB, center, b, true)

  // общая камера у двух карт
  useEffect(() => {
    const ma = mapA.current, mb = mapB.current
    if (!ma || !mb) return
    let lock = false
    const follow = (from: maplibregl.Map, to: maplibregl.Map) => () => {
      if (lock) return
      lock = true
      to.jumpTo({ center: from.getCenter(), zoom: from.getZoom() })
      lock = false
    }
    const fa = follow(ma, mb), fb = follow(mb, ma)
    ma.on('move', fa); mb.on('move', fb)
    return () => { ma.off('move', fa); mb.off('move', fb) }
  }, [mapA, mapB, shots])

  // покадровый просмотр: «после» идёт от старого снимка к свежему, шторка открыта полностью
  const order = useMemo(() => [...(shots ?? [])].reverse(), [shots])
  useEffect(() => {
    if (!play || !order.length) return
    setCut(0)
    let i = Math.max(0, order.findIndex((s) => s.id === b))
    const tm = setInterval(() => {
      i = (i + 1) % order.length
      setB(order[i].id)
    }, 1400)
    return () => clearInterval(tm)
  }, [play, order]) // eslint-disable-line react-hooks/exhaustive-deps

  const drag = (e: React.PointerEvent) => {
    const box = view.current?.getBoundingClientRect()
    if (!box) return
    const move = (x: number) => setCut(Math.min(100, Math.max(0, ((x - box.left) / box.width) * 100)))
    move(e.clientX)
    const mv = (ev: PointerEvent) => move(ev.clientX)
    const up = () => { window.removeEventListener('pointermove', mv); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', mv); window.addEventListener('pointerup', up)
    setPlay(false)
  }

  if (err) return <div className="ai-note err">Архив спутниковых снимков недоступен: {err}. Проверьте подключение к интернету и откройте вкладку ещё раз.</div>
  if (!shots) return <div className="sat-wait"><span className="spin" />Ищем снимки этого места в архиве Esri Wayback с 2014 года…</div>
  if (!shots.length) return <div className="ai-note">В архиве нет снимков этого места в нужном масштабе.</div>

  const sa = shots.find((s) => s.id === a)!, sb = shots.find((s) => s.id === b)!
  // лента: от года самого старого снимка до года окончания стройки (или текущего)
  const now = new Date().toISOString().slice(0, 10)
  const from = `${Math.min(year(shotDate(shots[shots.length - 1])), c.start ? year(c.start) : 9999)}-01-01`
  const to = `${Math.max(year(now), c.plannedEnd ? year(c.plannedEnd) : 0) + 1}-01-01`
  const pos = (d: string) => `${((t(d) - t(from)) / (t(to) - t(from))) * 100}%`
  const years = Array.from({ length: year(to) - year(from) + 1 }, (_, i) => year(from) + i)
  const pick = (id: number) => { setPlay(false); if (pickFor === 'a') setA(id); else setB(id); if (cut === 0) setCut(50) }
  const meta = (s: SatShot) => [s.acquired ? `съёмка ${fmt(s.acquired)}` : `выпуск ${fmt(s.date)}`, s.sensor, s.res ? `${s.res} м/пкс` : null].filter(Boolean).join(' · ')

  /** «Показать этот снимок на ленте» из карточки «Спутник против рекламы»: снимок — «после», шторка закрыта */
  const show = (id: number) => { if (!shots.some((x) => x.id === id)) return false; setPlay(false); setB(id); setCut(0); view.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }); return true }

  return (
    <div className="sat">
      <SatStage c={c} onShow={show} />
      <div className="sat-view" ref={view}>
        <div ref={elA} className="sat-map" />
        <div ref={elB} className="sat-map top" style={{ clipPath: `inset(0 0 0 ${cut}%)` }} />
        {cut > 0 && <span className="sat-tag l">До · {fmt(shotDate(sa))}</span>}
        <span className="sat-tag r">{cut > 0 ? 'После' : 'Снимок'} · {fmt(shotDate(sb))}</span>
        {cut > 0 && (
          <div className="sat-cut" style={{ left: `${cut}%` }} onPointerDown={drag} role="slider" aria-label="Шторка до/после" aria-valuenow={Math.round(cut)}>
            <span className="knob"><svg viewBox="0 0 24 24" width="18" height="18"><path d="M9 6l-5 6 5 6M15 6l5 6-5 6" /></svg></span>
          </div>
        )}
        <div className="sat-zoom">
          <button aria-label="Приблизить" onClick={() => mapB.current?.zoomIn()}><IcPlus size={18} /></button>
          <button aria-label="Отдалить" onClick={() => mapB.current?.zoomOut()}><IcMinus size={18} /></button>
        </div>
      </div>

      <div className="sat-bar">
        <div className="seg sat-seg">
          <button className={pickFor === 'a' ? 'on' : ''} onClick={() => setPickFor('a')}><i className="sat-k a" />Выбрать «до»</button>
          <button className={pickFor === 'b' ? 'on' : ''} onClick={() => setPickFor('b')}><i className="sat-k b" />Выбрать «после»</button>
        </div>
        <button className="btn g s" onClick={() => setPlay((p) => !p)}>{play ? '❚❚ Пауза' : '▶ По годам'}</button>
        <button className="btn g s" onClick={() => { setPlay(false); setCut(cut ? 0 : 50) }}>{cut ? 'Только «после»' : 'Шторка до/после'}</button>
        <span className="t-sm c2" style={{ marginLeft: 'auto' }}>{shots.length} {shots.length === 1 ? 'снимок' : shots.length < 5 ? 'снимка' : 'снимков'} с {year(shotDate(shots[shots.length - 1]))} года</span>
      </div>

      <div className="sat-tl">
        <div className="sat-axis">
          {c.start && (
            <span className="sat-build" style={{ left: pos(c.start), width: `calc(${pos(c.plannedEnd || now)} - ${pos(c.start)})` }}
              title={`Период стройки по документам: ${fmt(c.start)} — ${c.plannedEnd ? fmt(c.plannedEnd) : 'срок ввода не указан'}`} />
          )}
          <span className="sat-now" style={{ left: pos(now) }} title="Сегодня" />
          {shots.map((s) => (
            <button key={s.id} className={`sat-dot${s.id === a ? ' a' : ''}${s.id === b ? ' b' : ''}`} style={{ left: pos(shotDate(s)) }} onClick={() => pick(s.id)} title={meta(s)}>
              <span>{fmt(shotDate(s)).replace(' г.', '')}</span>
            </button>
          ))}
        </div>
        <div className="sat-years">{years.map((y) => <span key={y} style={{ left: pos(`${y}-01-01`) }}>{y}</span>)}</div>
      </div>

      <div className="sat-meta">
        <div><i className="sat-k a" /><b>До:</b> {meta(sa)}</div>
        <div><i className="sat-k b" /><b>После:</b> {meta(sb)}</div>
        <div className="c2"><i className="sat-k build" />Стройка по документам: {c.start ? fmt(c.start) : 'начало не указано'} — {c.plannedEnd ? fmt(c.plannedEnd) : 'срок ввода не указан'}</div>
        <div className="c3 t-xs">{WAYBACK_ATTR}. Дата съёмки — по метаданным Esri; снимок помогает увидеть ход работ, но не заменяет выезд и фото-мониторинг.</div>
      </div>
    </div>
  )
}
