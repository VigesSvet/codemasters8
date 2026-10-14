import { useEffect, useRef } from 'react'
import * as maplibregl from 'maplibre-gl'
import type { Complex, Zone } from '../data/types'
import { useStore } from '../lib/store'

const STYLES = {
  fortis: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
  siera: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
  banda: 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json',
}

export const UKA_CENTER: [number, number] = [82.603, 49.953]

interface Props {
  items: { c: Complex; zone: Zone; score: number }[]
  selected?: string
  onSelect?: (id: string) => void
  zoom?: number
  center?: [number, number]
  interactive?: boolean
}

export default function MapView({ items, selected, onSelect, zoom = 11.5, center = UKA_CENTER, interactive = true }: Props) {
  const { theme } = useStore()
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const markers = useRef<maplibregl.Marker[]>([])

  useEffect(() => {
    if (!el.current) return
    const m = new maplibregl.Map({
      container: el.current, style: STYLES[theme], center, zoom, attributionControl: { compact: true }, interactive,
    })
    if (interactive) m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    map.current = m
    return () => { m.remove(); map.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme])

  useEffect(() => {
    const m = map.current
    if (!m) return
    markers.current.forEach((mk) => mk.remove())
    markers.current = items.map(({ c, zone, score }) => {
      const node = document.createElement('div')
      node.className = `marker z-${zone} ${zone === 'red' ? 'pulse' : ''} ${selected === c.id ? 'sel' : ''}`
      node.title = `${c.name} · ${score}`
      node.addEventListener('click', (e) => { e.stopPropagation(); onSelect?.(c.id) })
      return new maplibregl.Marker({ element: node }).setLngLat([c.lng, c.lat]).addTo(m)
    })
  }, [items, selected, onSelect, theme])

  useEffect(() => {
    const c = items.find((x) => x.c.id === selected)?.c
    if (c && map.current) map.current.easeTo({ center: [c.lng, c.lat], zoom: Math.max(map.current.getZoom(), 13), duration: 700 })
  }, [selected, items])

  return <div ref={el} className="map" />
}
