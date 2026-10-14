import type { ReactNode } from 'react'
import { AlertTriangle, Check, HelpCircle, X } from 'lucide-react'
import type { AdStatus, Platform, Tri, Zone } from '../data/types'
import { zoneLabel } from '../lib/risk'
import { keywordsSafe } from '../data/demo'

export function ZoneBadge({ zone, pill }: { zone: Zone; pill?: boolean }) {
  if (pill) return <span className={`zone-pill z-${zone}`}>{zoneLabel[zone]} зона</span>
  return <span className={`zone z-${zone}`}><i />{zoneLabel[zone]}</span>
}

export function Score({ value, zone, solid }: { value: number; zone: Zone; solid?: boolean }) {
  return <span className={`score z-${zone} ${solid ? 'solid' : ''}`}>{value}</span>
}

export function NeedsCheck() {
  return <span className="check"><HelpCircle size={11} />Нужна проверка</span>
}

const triText: Record<Tri, string> = { yes: 'Есть', no: 'Нет', unknown: 'Нет сведений' }
export function TriMark({ v, label }: { v: Tri; label?: string }) {
  const Icon = v === 'yes' ? Check : v === 'no' ? X : HelpCircle
  return <span className={`tri ${v}`}><Icon size={12} />{label ?? triText[v]}</span>
}

export const platformMeta: Record<Platform, { name: string; color: string }> = {
  instagram: { name: 'Instagram', color: '#c13584' },
  tiktok: { name: 'TikTok', color: '#25c2c2' },
  krisha: { name: 'Krisha.kz', color: '#e8542d' },
  olx: { name: 'OLX', color: '#3a77ff' },
  telegram: { name: 'Telegram', color: '#2aa3e0' },
  facebook: { name: 'Facebook', color: '#4267b2' },
  '2gis': { name: '2ГИС', color: '#2fb24c' },
}
export function PlatformTag({ p }: { p: Platform }) {
  return <span className="plat"><i style={{ background: platformMeta[p].color }} />{platformMeta[p].name}</span>
}

export const statusMeta: Record<AdStatus, { name: string; zone?: Zone }> = {
  new: { name: 'Новая' },
  needs_check: { name: 'Нужна проверка', zone: 'yellow' },
  in_review: { name: 'На проверке' },
  confirmed: { name: 'Нарушение подтверждено', zone: 'red' },
  dismissed: { name: 'Отклонено', zone: 'green' },
}
export function StatusTag({ s }: { s: AdStatus }) {
  const m = statusMeta[s]
  return <span className={`chip ${m.zone ? `z-${m.zone}` : ''}`} style={m.zone ? { borderColor: 'var(--z)', color: 'var(--z)' } : undefined}>{m.name}</span>
}

const FUND = ['бронь', 'бронирование', 'первый взнос', 'взнос', 'рассрочка', 'ДДУ', 'долевого участия', 'долевом участии', 'от фундамента', 'цена котлована', 'напрямую застройщику', 'переуступка', 'фиксируем цену']
const SALE = ['старт продаж', 'продаём', 'продаем', 'продается', 'продаётся', 'продажа', 'продаж', 'квартиры', 'квартира', 'строительство']

/** Подсветка формулировок: красным — признаки сбора денег, жёлтым — продажа, зелёным — признаки законности */
export function Highlight({ text }: { text: string }) {
  const all = [...keywordsSafe.map((k) => [k, 'safe'] as const), ...FUND.map((k) => [k, 'fund'] as const), ...SALE.map((k) => [k, 'sale'] as const)]
  const re = new RegExp(`(${all.map(([k]) => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi')
  const parts = text.split(re)
  return (
    <>
      {parts.map((p, i) => {
        const hit = all.find(([k]) => k.toLowerCase() === p.toLowerCase())
        if (!hit) return <span key={i}>{p}</span>
        return <mark key={i} className={hit[1] === 'sale' ? '' : hit[1]}>{p}</mark>
      })}
    </>
  )
}

export function PageHead({ idx, label, title, aside, children }: { idx: string; label: string; title: ReactNode; aside?: ReactNode; children?: ReactNode }) {
  return (
    <header className="page-head fade-in">
      <div>
        <div className="idx"><span className="label">{idx}</span><span className="label" style={{ color: 'var(--ink)' }}>{label}</span></div>
        <h1 className="display d-xl">{title}</h1>
        {children}
      </div>
      {aside && <div className="row">{aside}</div>}
    </header>
  )
}

export function Gauge({ value }: { value: number }) {
  return (
    <div>
      <div className="gauge"><b style={{ left: `${value}%` }} /></div>
      <div className="gauge-scale label"><span>0</span><span style={{ marginLeft: '20%' }}>25</span><span style={{ marginLeft: '28%' }}>60</span><span>100</span></div>
    </div>
  )
}

export function Disclaimer() {
  return (
    <div className="row label" style={{ gap: 8, padding: '10px 0' }}>
      <AlertTriangle size={13} /> ИИ даёт рекомендацию. Решение о нарушении принимает аналитик. Данные — демонстрационные.
    </div>
  )
}

export const fmtDate = (d: string) => new Date(d).toLocaleDateString('ru-RU', { day: '2-digit', month: 'short', year: 'numeric' })
export const fmtDT = (d: string) => new Date(d).toLocaleString('ru-RU', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
