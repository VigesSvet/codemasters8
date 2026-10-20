import { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import type { FeatureCollection } from 'geojson'
import type { Complex, ScreeningStatus, Zone } from '../data/types'
import { NOT_ASSESSED } from '../lib/risk'
import { SCREENING_GLYPH, SCREENING_LABEL, VARIANT_SHORT, hasPoint, labelOf, variantOf } from '../lib/screening'
import { IcLocate, IcMinus, IcPlus } from '../ui/icons'

// Схема - векторный стиль Liberty от OpenFreeMap(данные OpenStreetMap, схема OpenMapTiles, без ключа). Подпись атрибуции
// приходит из TileJSON источника. Все подложки - в Web Mercator, координаты меток - WGS84 как есть.
const SCHEME_STYLE = 'https://tiles.openfreemap.org/styles/liberty'
/** Подписи схемы - по-русски, где в OSM есть name:ru, иначе местное название: в исходном стиле подпись двуязычная(латиница и
 *  местное написание). Номера дорог(ref) остаются как есть */
const LABEL: maplibregl.ExpressionSpecification = ['coalesce', ['get', 'name:ru'], ['get', 'name']]
const ruLabels: maplibregl.TransformStyleFunction = (_prev, next) => ({
  ...next,
  layers: next.layers.map((l) => (l.type === 'symbol' && l.layout?.['text-field'] && JSON.stringify(l.layout['text-field']).includes('"name')
    ? { ...l, layout: { ...l.layout, 'text-field': LABEL } } : l)),
})
// Спутник - Esri World Imagery с подписями поверх; 3D - тот же снимок и объёмные
// дома OpenStreetMap(OpenFreeMap, без ключа), а наши ЖК - столбики цвета статуса высотой по этажности
const ESRI_ATTR = 'Снимки © Esri, Maxar, Earthstar Geographics'
const SAT_SOURCES: maplibregl.StyleSpecification['sources'] = {
  sat: { type: 'raster', tileSize: 256, maxzoom: 17, attribution: ESRI_ATTR, tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'] },
  places: { type: 'raster', tileSize: 256, maxzoom: 17, tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'] },
}
const SAT_STYLE: maplibregl.StyleSpecification = {
  version: 8, sources: SAT_SOURCES,
  layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#2a2a2a' } }, { id: 'sat', type: 'raster', source: 'sat' }, { id: 'places', type: 'raster', source: 'places', paint: { 'raster-opacity': 0.9 } }],
}
const D3_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: { ...SAT_SOURCES, osm: { type: 'vector', url: 'https://tiles.openfreemap.org/planet', attribution: '© OpenStreetMap · OpenFreeMap' } },
  layers: [
    { id: 'bg', type: 'background', paint: { 'background-color': '#2a2a2a' } },
    { id: 'sat', type: 'raster', source: 'sat', paint: { 'raster-saturation': -0.35, 'raster-brightness-max': 0.85 } },
    { id: 'houses', type: 'fill-extrusion', source: 'osm', 'source-layer': 'building', minzoom: 13,
      paint: { 'fill-extrusion-color': '#e9e6e1', 'fill-extrusion-opacity': 0.82,
        'fill-extrusion-height': ['coalesce', ['get', 'render_height'], 9], 'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0] } },
  ],
}
export type MapMode = 'scheme' | 'sat' | '3d'
const MODE_KEY = 'saq.mapMode'
const MODES: [MapMode, string][] = [['scheme', 'Схема'], ['sat', 'Спутник'], ['3d', '3D']]
const styleOf = (m: MapMode) => (m === 'scheme' ? SCHEME_STYLE : m === 'sat' ? SAT_STYLE : D3_STYLE)
const COLOR: Record<string, string> = { suspect: '#e4402d', check: '#f59d00', ok: '#00b341', unknown: '#8a8c91', red: '#e4402d', yellow: '#f59d00', green: '#00b341', na: '#8a8c91' }

/** Столбик ЖК для 3D: круг ~45 м, высота — этажность × 3,2 м (нет этажности — 36 м) */
function columns(items: MapItem[], selected?: string): FeatureCollection {
  const R = 45
  return {
    type: 'FeatureCollection',
    features: items.flatMap(({ c, zone, off, screen }) => {
      if (!hasPoint(c)) return []
      const dLat = R / 111320, dLng = R / (111320 * Math.cos((c.lat * Math.PI) / 180))
      const ring = Array.from({ length: 33 }, (_, i) => { const a = (i / 32) * 2 * Math.PI; return [c.lng + dLng * Math.cos(a), c.lat + dLat * Math.sin(a)] })
      const tone = screen ?? (off ? 'na' : zone)
      return [{ type: 'Feature' as const, properties: { id: c.id, color: COLOR[tone] ?? COLOR.na, h: (c.floors > 0 ? c.floors * 3.2 : 36) + (c.id === selected ? 12 : 0) },
        geometry: { type: 'Polygon' as const, coordinates: [ring] } }]
    }),
  }
}

export const UKA_CENTER: [number, number] = [82.62, 49.937]

/** Метка открытых данных: серая, без балла — риск для реальных объектов не считается */
export interface PlainPoint { key: string; name: string; lng: number; lat: number }

/** ЖК на карте. screen — «Проверка по рекламе»: цвет и знак метки (✓ ? ! —), балл формального риска — белым кружком с кольцом
 *  цвета зоны (только у оценённых). Без screen (сервер старой версии) — прежняя метка: цвет зоны и балл;
 *  off — риск не оценивается: серая метка с «—». ЖК без координат на карту не попадают */
export interface MapItem { c: Complex; zone: Zone; score: number; off?: boolean; screen?: ScreeningStatus | null }

/** Горячая точка (переключатель «Горячие точки рекламы» на главной карте): круг под меткой ЖК, w — публикаций с предложением
 *  оплаты, red — основания по открытым данным нет, amber — есть */
export interface HeatPoint { id: string; lng: number; lat: number; w: number; tone: 'red' | 'amber' }

interface Props {
  items: MapItem[]
  /** Горячие точки рекламы: размытые круги, радиус растёт с √w; нет или пусто — слоя нет */
  heat?: HeatPoint[]
  /** Слой «Открытые данные»: серые метки без балла */
  points?: PlainPoint[]
  onPoint?: (key: string) => void
  selected?: string
  onSelect?: (id: string) => void
  zoom?: number
  center?: [number, number]
  interactive?: boolean
  controls?: boolean
  labels?: boolean
  padLeft?: number
  /** вписать все объекты в видимую область */
  fit?: boolean
  /** переключатель «Схема · Спутник · 3D» (выбор запоминается) */
  modes?: boolean
}

export default function MapView({ items, heat, points, onPoint, selected, onSelect, zoom = 11.6, center = UKA_CENTER, interactive = true, controls = false, labels = false, padLeft = 0, fit = false, modes = false }: Props) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const markers = useRef<maplibregl.Marker[]>([])
  const plain = useRef<maplibregl.Marker[]>([])
  const [ready, setReady] = useState(false)
  const [mode, setMode] = useState<MapMode>(() => {
    if (!modes) return 'scheme'
    try { const v = localStorage.getItem(MODE_KEY); return v === 'sat' || v === '3d' ? v : 'scheme' } catch { return 'scheme' }
  })
  const [styled, setStyled] = useState(0) // растёт после каждой смены подложки — слой столбиков ставится заново

  useEffect(() => {
    if (!el.current) return
    const m = new maplibregl.Map({ container: el.current, center, zoom, attributionControl: { compact: true }, interactive,
      ...(mode === '3d' ? { pitch: 55, bearing: -17 } : {}), maxPitch: 70 })
    m.on('style.load', () => setStyled((n) => n + 1))
    // стиль ставится отдельно: только setStyle принимает transformStyle(русские подписи схемы)
    m.setStyle(styleOf(mode), { transformStyle: ruLabels })
    if (padLeft) m.setPadding({ left: padLeft, top: 80, right: 0, bottom: 0 })
    m.on('load', () => setReady(true))
    map.current = m
    const ro = new ResizeObserver(() => m.resize())
    ro.observe(el.current)
    return () => { ro.disconnect(); m.remove(); map.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // левая панель свернулась или раскрылась — центр карты плавно сдвигается вслед (первый отступ задан при создании)
  const firstPad = useRef(padLeft)
  useEffect(() => {
    const m = map.current
    if (!m || padLeft === firstPad.current) return
    firstPad.current = padLeft
    m.easeTo({ padding: { left: padLeft, top: 80, right: 0, bottom: 0 }, duration: 300 })
  }, [padLeft])

  useEffect(() => {
    const m = map.current
    if (!m) return
    markers.current.forEach((mk) => mk.remove())
    markers.current = items.flatMap(({ c, zone, score, off, screen }) => {
      if (!hasPoint(c)) return []
      const node = document.createElement('div')
      const b = document.createElement('span'); b.className = 'b'
      const n = document.createElement('span'); n.className = 'n'; n.textContent = c.name
      if (screen) {
        node.className = `gpin scr ${screen} ${selected === c.id ? 'sel' : ''} ${labels ? 'lbl' : ''}`
        // ok по решению аналитика: подпись решения («Дом сдан — замечаний нет»), у названия — «сдан» / «проверено»
        const v = variantOf(c.screening)
        if (v) n.textContent = `${c.name} · ${VARIANT_SHORT[v]}`
        node.title = `${c.name} — ${(c.screening?.status === screen ? labelOf(c.screening) : SCREENING_LABEL[screen]).toLowerCase()}${off ? '' : ` · формальный риск ${score} из 100`}`
        const g = document.createElement('i'); g.className = 'g'; g.textContent = SCREENING_GLYPH[screen]
        b.append(g, n)
        if (!off) { const sc = document.createElement('span'); sc.className = `sc ${zone}`; sc.textContent = String(score); b.append(sc) }
      } else {
        node.className = `gpin ${off ? 'na' : zone} ${selected === c.id ? 'sel' : ''} ${labels ? 'lbl' : ''}`
        if (off) node.title = `${c.name} — ${NOT_ASSESSED.toLowerCase()}`
        const i = document.createElement('i'); i.textContent = off ? '—' : String(score)
        b.append(n, i)
      }
      node.append(b)
      node.setAttribute('role', 'button')
      node.setAttribute('aria-label', node.title || c.name)
      node.addEventListener('click', (e) => { e.stopPropagation(); onSelect?.(c.id) })
      return [new maplibregl.Marker({ element: node, anchor: 'bottom', offset: [0, -6] }).setLngLat([c.lng, c.lat]).addTo(m)]
    })
  }, [items, selected, onSelect, labels, ready])

  useEffect(() => {
    const m = map.current
    if (!m) return
    plain.current.forEach((mk) => mk.remove())
    plain.current = (points ?? []).map((p) => {
      const node = document.createElement('div')
      node.className = `gpin src ${labels ? 'lbl' : ''}`
      node.title = `${p.name} — открытые данные, риск не оценивается`
      const b = document.createElement('span'); b.className = 'b'
      const n = document.createElement('span'); n.className = 'n'; n.textContent = p.name
      b.append(n)
      node.append(b)
      node.addEventListener('click', (e) => { e.stopPropagation(); onPoint?.(p.key) })
      return new maplibregl.Marker({ element: node, anchor: 'bottom', offset: [0, -6] }).setLngLat([p.lng, p.lat]).addTo(m)
    })
  }, [points, onPoint, labels, ready])

  // смена подложки: камера та же, в 3D - наклон и поворот
  // прежний режим — в ref: повторный запуск эффекта (StrictMode) с тем же режимом ничего не делает
  const prev = useRef(mode)
  useEffect(() => {
    const m = map.current
    if (!m || prev.current === mode) return
    prev.current = mode
    try { localStorage.setItem(MODE_KEY, mode) } catch { /* выбор не запомнится — не страшно */ }
    m.setStyle(styleOf(mode), { diff: false, transformStyle: ruLabels })
    m.easeTo(mode === '3d' ? { pitch: 55, bearing: -17, zoom: Math.max(m.getZoom(), 14.2), duration: 900 } : { pitch: 0, bearing: 0, duration: 600 })
  }, [mode])

  // 3D: столбики наших ЖК поверх домов города
  useEffect(() => {
    const m = map.current
    if (!m || mode !== '3d' || !m.getStyle()?.sources?.osm) return
    const data = columns(items, selected)
    const src = m.getSource('saq-cx') as maplibregl.GeoJSONSource | undefined
    if (src) { src.setData(data); return }
    m.addSource('saq-cx', { type: 'geojson', data })
    m.addLayer({ id: 'saq-cx', type: 'fill-extrusion', source: 'saq-cx',
      paint: { 'fill-extrusion-color': ['get', 'color'], 'fill-extrusion-height': ['get', 'h'], 'fill-extrusion-opacity': 0.2 } })
  }, [mode, styled, items, selected])

  // горячие точки: слой кругов под HTML-метками; после смены подложки(styled) - заново
  useEffect(() => {
    const m = map.current
    if (!m || !ready) return
    try {
      const src = m.getSource('saq-heat') as maplibregl.GeoJSONSource | undefined
      if (!heat?.length) {
        if (m.getLayer('saq-heat')) m.removeLayer('saq-heat')
        if (src) m.removeSource('saq-heat')
        return
      }
      const data: FeatureCollection = { type: 'FeatureCollection', features: heat.map((p) => ({ type: 'Feature' as const,
        properties: { id: p.id, w: Math.max(1, p.w), color: p.tone === 'red' ? COLOR.red : COLOR.yellow }, geometry: { type: 'Point' as const, coordinates: [p.lng, p.lat] } })) }
      if (src) { src.setData(data); return }
      m.addSource('saq-heat', { type: 'geojson', data })
      m.addLayer({ id: 'saq-heat', type: 'circle', source: 'saq-heat', paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, ['+', 12, ['*', 8, ['sqrt', ['get', 'w']]]], 15, ['+', 46, ['*', 30, ['sqrt', ['get', 'w']]]]],
        'circle-color': ['get', 'color'], 'circle-opacity': 0.38, 'circle-blur': 0.6 } })
    } catch { /* подложка ещё грузится — слой поставится после style.load (styled) */ }
  }, [heat, ready, styled])

  useEffect(() => {
    const m = map.current
    const all = [...items.flatMap(({ c }) => (hasPoint(c) ? [{ lng: c.lng, lat: c.lat }] : [])), ...(points ?? [])]
    if (!fit || !ready || !m || !all.length) return
    const lngs = all.map((x) => x.lng), lats = all.map((x) => x.lat)
    m.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding: { top: 60, bottom: 40, left: 40, right: 70 }, maxZoom: 14, duration: 400 })
  }, [fit, ready, items, points])

  useEffect(() => {
    const c = items.find((x) => x.c.id === selected)?.c
    if (c && hasPoint(c) && map.current) map.current.easeTo({ center: [c.lng, c.lat], zoom: Math.max(map.current.getZoom(), mode === '3d' ? 15 : 14), duration: 600 })
  }, [selected, items]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div ref={el} className="map" />
      {controls && (
        <div className="mctrl">
          <div className="zoom">
            <button aria-label="Приблизить" onClick={() => map.current?.zoomIn()}><IcPlus size={18} /></button>
            <button aria-label="Отдалить" onClick={() => map.current?.zoomOut()}><IcMinus size={18} /></button>
          </div>
          <div className="loc">
            <button aria-label="К центру города" title="Показать весь город" onClick={() => map.current?.easeTo({ center: UKA_CENTER, zoom: mode === '3d' ? 13 : 11.8 })}><IcLocate size={18} /></button>
          </div>
          {modes && (
            <div className="modes" role="group" aria-label="Подложка карты">
              {MODES.map(([k, l]) => <button key={k} className={mode === k ? 'on' : ''} aria-pressed={mode === k} onClick={() => setMode(k)}
                title={k === 'scheme' ? 'Схема OpenStreetMap(OpenFreeMap)' : k === 'sat' ? 'Спутниковый снимок Esri' : 'Объёмные дома OpenStreetMap на снимке, ЖК - столбики цвета статуса'}>{l}</button>)}
            </div>
          )}
        </div>
      )}
    </>
  )
}
