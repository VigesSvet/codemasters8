import type { ReactNode } from 'react'
import { Breadcrumb, BreadcrumbItem, Tag } from '@carbon/react'
import { Link } from 'react-router-dom'
import type { AdStatus, Platform, Tri, Zone } from '../data/types'
import { keywordsSafe } from '../data/demo'

export const zoneName: Record<Zone, string> = { red: 'Красная зона', yellow: 'Жёлтая зона', green: 'Зелёная зона' }

export function ZoneTag({ zone, size = 'md' }: { zone: Zone; size?: 'sm' | 'md' }) {
  if (zone === 'yellow') return <Tag size={size} className="tag-yellow">{zoneName[zone]}</Tag>
  return <Tag size={size} type={zone === 'red' ? 'red' : 'green'}>{zoneName[zone]}</Tag>
}

export const ZoneDot = ({ zone }: { zone: Zone }) => <span className={`zone-dot ${zone}`} />

export const Score = ({ value, zone }: { value: number; zone: Zone }) => <span className={`score ${zone}`}>{value}</span>

export const NeedsCheck = () => <Tag size="sm" type="outline">Нужна проверка</Tag>

const triText: Record<Tri, string> = { yes: 'Есть', no: 'Нет', unknown: 'Нет сведений' }
export function TriTag({ v, label }: { v: Tri; label?: string }) {
  return <Tag size="sm" type={v === 'yes' ? 'green' : v === 'no' ? 'red' : 'cool-gray'}>{label ?? triText[v]}</Tag>
}

export const platformMeta: Record<Platform, { name: string; color: string; short: string }> = {
  instagram: { name: 'Instagram', color: '#d62976', short: 'IG' },
  tiktok: { name: 'TikTok', color: '#161616', short: 'TT' },
  krisha: { name: 'Krisha.kz', color: '#e8542d', short: 'Kr' },
  olx: { name: 'OLX', color: '#3a77ff', short: 'OL' },
  telegram: { name: 'Telegram', color: '#2aa3e0', short: 'TG' },
  facebook: { name: 'Facebook', color: '#1877f2', short: 'FB' },
  '2gis': { name: '2ГИС', color: '#19aa1e', short: '2G' },
}
export const PlatformIcon = ({ p }: { p: Platform }) => <span className="plat-ic" style={{ background: platformMeta[p].color }} title={platformMeta[p].name}>{platformMeta[p].short}</span>

export const statusMeta: Record<AdStatus, { name: string; type: 'gray' | 'blue' | 'purple' | 'red' | 'green' | 'warm-gray' }> = {
  new: { name: 'Новая', type: 'blue' },
  needs_check: { name: 'Нужна проверка', type: 'warm-gray' },
  in_review: { name: 'На проверке', type: 'purple' },
  confirmed: { name: 'Нарушение подтверждено', type: 'red' },
  dismissed: { name: 'Отклонено', type: 'green' },
}
export const StatusTag = ({ s, size = 'sm' }: { s: AdStatus; size?: 'sm' | 'md' }) => <Tag size={size} type={statusMeta[s].type}>{statusMeta[s].name}</Tag>

/** Иконка статуса как в Linear: круг, заполненный по стадии */
export function StatusIcon({ s }: { s: AdStatus }) {
  const c = { new: '#8d8d8d', needs_check: '#f1c21b', in_review: '#8a3ffc', confirmed: '#da1e28', dismissed: '#24a148' }[s]
  const fill = { new: 0, needs_check: 0.25, in_review: 0.5, confirmed: 1, dismissed: 1 }[s]
  const r = 6, C = 2 * Math.PI * 3
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
      <circle cx="7" cy="7" r={r} fill="none" stroke={c} strokeWidth="1.5" strokeDasharray={s === 'new' ? '2 2' : undefined} />
      {fill > 0 && <circle cx="7" cy="7" r="3" fill="none" stroke={c} strokeWidth="6" strokeDasharray={`${C * fill} ${C}`} transform="rotate(-90 7 7)" />}
    </svg>
  )
}

const FUND = ['бронь', 'бронирование', 'первый взнос', 'взнос', 'рассрочка', 'ДДУ', 'долевого участия', 'долевом участии', 'от фундамента', 'цена котлована', 'напрямую застройщику', 'переуступка', 'фиксируем цену']
const SALE = ['старт продаж', 'продаём', 'продаем', 'продается', 'продаётся', 'продажа', 'продаж', 'квартиры', 'квартира', 'строительство']

/** Подсветка: красным — сбор денег, жёлтым — продажа, зелёным — признаки законности */
export function Highlight({ text }: { text: string }) {
  const all = [...keywordsSafe.map((k) => [k, 'safe'] as const), ...FUND.map((k) => [k, 'fund'] as const), ...SALE.map((k) => [k, 'sale'] as const)]
  const re = new RegExp(`(${all.map(([k]) => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi')
  return (
    <>
      {text.split(re).map((p, i) => {
        const hit = all.find(([k]) => k.toLowerCase() === p.toLowerCase())
        if (!hit) return <span key={i}>{p}</span>
        return <mark key={i} className={hit[1] === 'sale' ? '' : hit[1]}>{p}</mark>
      })}
    </>
  )
}

export function PageHeader({ crumbs, title, sub, actions, children }: { crumbs: [string, string?][]; title: ReactNode; sub?: ReactNode; actions?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-header">
      <Breadcrumb noTrailingSlash>
        {crumbs.map(([label, to], i) => (
          <BreadcrumbItem key={i} isCurrentPage={!to}>{to ? <Link to={to}>{label}</Link> : label}</BreadcrumbItem>
        ))}
      </Breadcrumb>
      <div className="row-between">
        <div>
          <h1>{title}</h1>
          {sub && <div className="sub">{sub}</div>}
        </div>
        {actions && <div style={{ display: 'flex', gap: 1 }}>{actions}</div>}
      </div>
      {children}
    </div>
  )
}

export function Gauge({ value }: { value: number }) {
  return (
    <>
      <div className="gauge"><i /><i /><i /><b style={{ left: `${value}%` }} /></div>
      <div className="label-01" style={{ display: 'flex', justifyContent: 'space-between' }}><span>0</span><span>25</span><span>60</span><span>100</span></div>
    </>
  )
}

export const fmtDate = (d: string) => new Date(d).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' })
export const fmtDT = (d: string) => new Date(d).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
export const fmtShort = (d: string) => new Date(d).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })
