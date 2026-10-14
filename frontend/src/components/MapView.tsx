import { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import { Add, Layers, Location, Subtract } from '@carbon/icons-react'
import type { Complex, Zone } from '../data/types'
import { useStore } from '../lib/store'

const STYLES = {
  voyager: 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json',
  positron: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
  dark: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
}

export const UKA_CENTER: [number, number] = [82.603, 49.953]

interface Props {
  items: { c: Complex; zone: Zone; score: number }[]
  selected?: string
  onSelect?: (id: string) => void
  zoom?: number
  center?: [number, number]
  interactive?: boolean
  controls?: boolean
  labels?: boolean
  /** смещение центра, когда слева открыта панель */
  padLeft?: number
}

export default function MapView({ items, selected, onSelect, zoom = 11.6, center = UKA_CENTER, interactive = true, controls = false, labels = false, padLeft = 0 }: Props) {
  const { theme } = useStore()
  const [base, setBase] = useState<'voyager' | 'positron'>('voyager')
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const markers = useRef<maplibregl.Marker[]>([])
  const style = theme === 'g100' ? STYLES.dark : STYLES[base]

  useEffect(() => {
    if (!el.current) return
    const m = new maplibregl.Map({ container: el.current, style, center, zoom, attributionControl: { compact: true }, interactive })
    if (padLeft) m.setPadding({ left: padLeft, top: 0, right: 0, bottom: 0 })
    map.current = m
    return () => { m.remove(); map.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [style])

  useEffect(() => {
    const m = map.current
    if (!m) return
    markers.current.forEach((mk) => mk.remove())
    markers.current = items.map(({ c, zone, score }) => {
      const node = document.createElement('div')
      node.className = `pin ${zone} ${selected === c.id ? 'sel' : ''} ${labels ? 'label' : ''}`
      const dot = document.createElement('span'); dot.className = 'd'
      const name = document.createElement('span'); name.className = 'n'; name.textContent = `${c.name} · ${score}`
      node.append(dot, name)
      node.addEventListener('click', (e) => { e.stopPropagation(); onSelect?.(c.id) })
      return new maplibregl.Marker({ element: node, anchor: 'left' }).setLngLat([c.lng, c.lat]).addTo(m)
    })
  }, [items, selected, onSelect, style, labels])

  useEffect(() => {
    const c = items.find((x) => x.c.id === selected)?.c
    if (c && map.current) map.current.easeTo({ center: [c.lng, c.lat], zoom: Math.max(map.current.getZoom(), 13.5), duration: 600 })
  }, [selected, items])

  return (
    <>
      <div ref={el} className="map" />
      {controls && (
        <div className="map-ctrls">
          <div className="grp">
            <button aria-label="Приблизить" onClick={() => map.current?.zoomIn()}><Add size={20} /></button>
            <button aria-label="Отдалить" onClick={() => map.current?.zoomOut()}><Subtract size={20} /></button>
          </div>
          <div className="grp">
            <button aria-label="К городу" onClick={() => map.current?.easeTo({ center: UKA_CENTER, zoom: 11.6 })}><Location size={20} /></button>
            <button aria-label="Слой" onClick={() => setBase((b) => (b === 'voyager' ? 'positron' : 'voyager'))}><Layers size={20} /></button>
          </div>
        </div>
      )}
    </>
  )
}
