import { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import type { Complex, Zone } from '../data/types'
import { IcLocate, IcMinus, IcPlus } from '../ui/icons'

// Подложка Яндекс Карт (растровые тайлы). Яндекс режет тайлы в эллиптической проекции EPSG:3395,
// а MapLibre рисует сетку Web Mercator (EPSG:3857) — поэтому широты объектов пересчитываем через yLat().
const STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    yandex: {
      type: 'raster', tileSize: 256, maxzoom: 19,
      tiles: ['https://core-renderer-tiles.maps.yandex.net/tiles?l=map&x={x}&y={y}&z={z}&scale=2&lang=ru_RU'],
      attribution: '© Яндекс Карты',
    },
  },
  layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#f6f4ef' } }, { id: 'yandex', type: 'raster', source: 'yandex' }],
}
const E = 0.0818191908426
/** Широта WGS84 → широта в сетке тайлов Яндекса, отображённой как Web Mercator */
export const yLat = (lat: number) => {
  const phi = (lat * Math.PI) / 180, es = E * Math.sin(phi)
  const y = Math.log(Math.tan(Math.PI / 4 + phi / 2) * Math.pow((1 - es) / (1 + es), E / 2))
  return ((2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180) / Math.PI
}
const ll = (lng: number, lat: number): [number, number] => [lng, yLat(lat)]
export const UKA_CENTER: [number, number] = [82.62, 49.937]

interface Props {
  items: { c: Complex; zone: Zone; score: number }[]
  selected?: string
  onSelect?: (id: string) => void
  zoom?: number
  center?: [number, number]
  interactive?: boolean
  controls?: boolean
  labels?: boolean
  padLeft?: number
}

export default function MapView({ items, selected, onSelect, zoom = 11.6, center = UKA_CENTER, interactive = true, controls = false, labels = false, padLeft = 0 }: Props) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const markers = useRef<maplibregl.Marker[]>([])
  const [ready, setReady] = useState(false)

  useEffect(() => {
    if (!el.current) return
    const m = new maplibregl.Map({ container: el.current, style: STYLE, center: ll(center[0], center[1]), zoom, attributionControl: { compact: true }, interactive })
    if (padLeft) m.setPadding({ left: padLeft, top: 80, right: 0, bottom: 0 })
    m.on('load', () => setReady(true))
    map.current = m
    const ro = new ResizeObserver(() => m.resize())
    ro.observe(el.current)
    return () => { ro.disconnect(); m.remove(); map.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const m = map.current
    if (!m) return
    markers.current.forEach((mk) => mk.remove())
    markers.current = items.map(({ c, zone, score }) => {
      const node = document.createElement('div')
      node.className = `gpin ${zone} ${selected === c.id ? 'sel' : ''} ${labels ? 'lbl' : ''}`
      const b = document.createElement('span'); b.className = 'b'
      const n = document.createElement('span'); n.className = 'n'; n.textContent = c.name
      const i = document.createElement('i'); i.textContent = String(score)
      b.append(n, i)
      node.append(b)
      node.addEventListener('click', (e) => { e.stopPropagation(); onSelect?.(c.id) })
      return new maplibregl.Marker({ element: node, anchor: 'bottom', offset: [0, -6] }).setLngLat(ll(c.lng, c.lat)).addTo(m)
    })
  }, [items, selected, onSelect, labels, ready])

  useEffect(() => {
    const c = items.find((x) => x.c.id === selected)?.c
    if (c && map.current) map.current.easeTo({ center: ll(c.lng, c.lat), zoom: Math.max(map.current.getZoom(), 14), duration: 600 })
  }, [selected, items])

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
            <button aria-label="К центру города" title="Показать весь город" onClick={() => map.current?.easeTo({ center: ll(...UKA_CENTER), zoom: 11.8 })}><IcLocate size={18} /></button>
          </div>
        </div>
      )}
    </>
  )
}
