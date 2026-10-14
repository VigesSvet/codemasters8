import { useEffect, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { AdStatus, Platform, Tri, Zone } from '../data/types'
import { keywordsSafe } from '../data/demo'
import { IcCheck, IcChevronRight, IcClose, IcHelp } from '../ui/icons'

export const zoneName: Record<Zone, string> = { red: 'Красная зона', yellow: 'Жёлтая зона', green: 'Зелёная зона' }
export const zoneShort: Record<Zone, string> = { red: 'Красная', yellow: 'Жёлтая', green: 'Зелёная' }

export const ZoneTag = ({ zone }: { zone: Zone }) => <span className={`tag ${zone}`}><span className={`zdot ${zone}`} style={{ width: 6, height: 6 }} />{zoneName[zone]}</span>
export const ZoneDot = ({ zone }: { zone: Zone }) => <span className={`zdot ${zone}`} />
export const Score = ({ value, zone }: { value: number; zone: Zone }) => <span className={`score ${zone}`}>{value}</span>
export const NeedsCheck = () => <span className="tag yellow"><IcHelp size={14} />Нужна проверка</span>

const triText: Record<Tri, string> = { yes: 'Есть', no: 'Нет', unknown: 'Нет сведений' }
export function TriTag({ v, label }: { v: Tri; label?: string }) {
  return <span className={`tag ${v === 'yes' ? 'green' : v === 'no' ? 'red' : 'gray'}`}>{v === 'yes' ? <IcCheck size={13} strokeWidth={3} /> : v === 'no' ? <IcClose size={13} strokeWidth={3} /> : null}{label ?? triText[v]}</span>
}

export const platformMeta: Record<Platform, { name: string; color: string; short: string }> = {
  instagram: { name: 'Instagram', color: '#e1306c', short: 'IG' },
  tiktok: { name: 'TikTok', color: '#21201f', short: 'TT' },
  krisha: { name: 'Krisha.kz', color: '#f2622e', short: 'Kr' },
  olx: { name: 'OLX', color: '#3a77ff', short: 'OL' },
  telegram: { name: 'Telegram', color: '#2aa3e0', short: 'TG' },
  facebook: { name: 'Facebook', color: '#1877f2', short: 'FB' },
  '2gis': { name: '2ГИС', color: '#19aa1e', short: '2G' },
}
export const PlatformIcon = ({ p, size = 28 }: { p: Platform; size?: number }) => (
  <span className="plat" style={{ background: platformMeta[p].color, width: size, height: size, borderRadius: size / 3 }} title={platformMeta[p].name}>{platformMeta[p].short}</span>
)

export const statusMeta: Record<AdStatus, { name: string; tag: string; color: string }> = {
  new: { name: 'Новая', tag: 'blue', color: '#1a6dff' },
  needs_check: { name: 'Нужна проверка', tag: 'yellow', color: '#ff9d00' },
  in_review: { name: 'На проверке', tag: 'violet', color: '#7a4ae0' },
  confirmed: { name: 'Нарушение', tag: 'red', color: '#f0422f' },
  dismissed: { name: 'Отклонено', tag: 'green', color: '#22a94f' },
}
export const StatusTag = ({ s }: { s: AdStatus }) => <span className={`tag ${statusMeta[s].tag}`}>{statusMeta[s].name}</span>
export const StatusDot = ({ s }: { s: AdStatus }) => <span className="zdot" style={{ background: statusMeta[s].color }} />

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

export function PageTitle({ crumbs, title, sub, actions }: { crumbs?: [string, string?][]; title: ReactNode; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-title">
      <div>
        {crumbs && (
          <div className="crumbs">
            {crumbs.map(([l, to], i) => (
              <span key={i} className="row" style={{ gap: 6 }}>
                {i > 0 && <IcChevronRight size={14} />}
                {to ? <Link to={to}>{l}</Link> : <span>{l}</span>}
              </span>
            ))}
          </div>
        )}
        <h1 className="t-h1">{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  )
}

export const Toggle = ({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label?: string }) => (
  <button className={`toggle ${on ? 'on' : ''}`} role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)} />
)

/** Ячейка списка: иконка / заголовок / подпись / хвост */
export function Cell({ icon, title, sub, end, chevron, onClick, to, inset }: { icon?: ReactNode; title: ReactNode; sub?: ReactNode; end?: ReactNode; chevron?: boolean; onClick?: () => void; to?: string; inset?: boolean }) {
  const inner = (
    <>
      {icon}
      <div className="main"><div className="ttl">{title}</div>{sub && <div className="sub">{sub}</div>}</div>
      {(end || chevron) && <div className="end">{end}{chevron && <IcChevronRight size={20} />}</div>}
    </>
  )
  const cls = `cell ${inset ? 'inset' : ''}`
  if (to) return <Link className={cls} to={to}>{inner}</Link>
  if (onClick) return <button className={cls} onClick={onClick}>{inner}</button>
  return <div className={cls}>{inner}</div>
}

export function Seg<T extends string>({ value, onChange, items, full }: { value: T; onChange: (v: T) => void; items: [T, ReactNode][]; full?: boolean }) {
  return (
    <div className={`seg ${full ? 'full' : ''}`}>
      {items.map(([k, l]) => <button key={k} className={value === k ? 'on' : ''} onClick={() => onChange(k)}>{l}</button>)}
    </div>
  )
}

export function Modal({ title, onClose, children, footer }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])
  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <div className="modal-h"><span />{title}<button className="x-btn" onClick={onClose} aria-label="Закрыть"><IcClose size={16} strokeWidth={3} /></button></div>
        <div className="modal-b">{children}</div>
        {footer && <div className="modal-f">{footer}</div>}
      </div>
    </div>
  )
}

export function Gauge({ value }: { value: number }) {
  return (
    <div>
      <div className="gauge"><i /><i /><i /><b style={{ left: `${Math.max(2, Math.min(98, value))}%` }} /></div>
      <div className="gauge-l"><span>0</span><span>25</span><span>60</span><span>100</span></div>
    </div>
  )
}

export const fmtDate = (d: string) => new Date(d).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).replace(' г.', '')
export const fmtDT = (d: string) => new Date(d).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
export const fmtShort = (d: string) => new Date(d).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })
export const adKey = (id: string) => `AD-${id.replace(/\D/g, '').padStart(3, '0')}`
