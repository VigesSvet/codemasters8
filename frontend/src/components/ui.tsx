import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { AdStatus, Platform, Tri, Zone } from '../data/types'
import { keywordsSafe } from '../data/demo'
import { siFacebook, siInstagram, siTelegram, siTiktok } from 'simple-icons'
import { IcCheck, IcChevronDown, IcChevronRight, IcChevronUp, IcClose, IcHelp } from '../ui/icons'

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

export const platformMeta: Record<Platform, { name: string; color: string }> = {
  instagram: { name: 'Instagram', color: '#E4405F' },
  tiktok: { name: 'TikTok', color: '#000000' },
  krisha: { name: 'Krisha.kz', color: '#FFD84D' },
  olx: { name: 'OLX', color: '#23E5DB' },
  telegram: { name: 'Telegram', color: '#26A5E4' },
  facebook: { name: 'Facebook', color: '#0866FF' },
  '2gis': { name: '2ГИС', color: '#19AA1E' },
}

// Логотипы: Simple Icons (CC0) для соцсетей, фавиконки сайтов для Krisha, OLX и 2ГИС
const SI: Partial<Record<Platform, { path: string; bg: string }>> = {
  instagram: { path: siInstagram.path, bg: 'radial-gradient(circle at 30% 107%, #fdf497 0%, #fdf497 5%, #fd5949 45%, #d6249f 60%, #285AEB 90%)' },
  tiktok: { path: siTiktok.path, bg: '#000' },
  telegram: { path: siTelegram.path, bg: '#26A5E4' },
  facebook: { path: siFacebook.path, bg: '#0866FF' },
}
const PNG: Partial<Record<Platform, string>> = { krisha: '/brands/krisha.png', olx: '/brands/olx.png', '2gis': '/brands/2gis.png' }

export function PlatformIcon({ p, size = 28 }: { p: Platform; size?: number }) {
  const r = Math.round(size * 0.28)
  const si = SI[p]
  if (si) return (
    <span className="plat" style={{ width: size, height: size, borderRadius: p === 'telegram' ? '50%' : r, background: si.bg }} title={platformMeta[p].name}>
      <svg viewBox="0 0 24 24" width={size * (p === 'telegram' ? 0.56 : 0.6)} height={size * (p === 'telegram' ? 0.56 : 0.6)} fill="#fff" style={p === 'telegram' ? { marginLeft: -size * 0.05 } : undefined}><path d={si.path} /></svg>
    </span>
  )
  return <img className="plat" src={PNG[p]} alt={platformMeta[p].name} title={platformMeta[p].name} style={{ width: size, height: size, borderRadius: r }} />
}

export const statusMeta: Record<AdStatus, { name: string; tag: string; color: string }> = {
  new: { name: 'Новая', tag: 'gray', color: 'rgba(0,0,0,.32)' },
  needs_check: { name: 'Нужна проверка', tag: 'yellow', color: '#F59D00' },
  in_review: { name: 'На проверке', tag: 'blue', color: '#0065D9' },
  confirmed: { name: 'Нарушение', tag: 'red', color: '#E4402D' },
  dismissed: { name: 'Отклонено', tag: 'green', color: '#00B341' },
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

export function Modal({ title, onClose, children, footer, wide }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])
  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal-wrap" onClick={(e) => e.stopPropagation()} style={wide ? { width: 'min(1040px, calc(100vw - 120px))' } : undefined}>
        <button className="x-btn" onClick={onClose} aria-label="Закрыть"><IcClose /></button>
        <div className="modal" role="dialog" aria-label={title}>
          <div className="modal-h">{title}</div>
          <div className="modal-b">{children}</div>
          {footer && <div className="modal-f">{footer}</div>}
        </div>
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

/** Кнопка фильтра с поповером — как «Комнат ⌄» на realty.yandex.ru: круглые варианты и жёлтая кнопка «Показать N» */
export function FilterPop<T extends string>({ label, title, value, options, onChange, count, multi }: {
  label: string; title: string; value: T[]; options: [T, ReactNode][]; onChange: (v: T[]) => void; count: (v: T[]) => number; multi?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<T[]>(value)
  useEffect(() => { if (open) setDraft(value) }, [open, value])
  const toggle = (v: T) => setDraft((d) => (d.includes(v) ? d.filter((x) => x !== v) : multi ? [...d, v] : [v]))
  const shown = value.length ? (value.length === 1 ? options.find((o) => o[0] === value[0])?.[1] : `${label}: ${value.length}`) : label
  return (
    <div className="nav-item">
      <button className={`fbtn ${open ? 'open' : ''} ${value.length && !open ? 'on' : ''}`} onClick={() => setOpen(!open)}>{shown}{open ? <IcChevronUp /> : <IcChevronDown />}</button>
      {open && (
        <>
          <div className="menu-scrim" onClick={() => setOpen(false)} />
          <div className="pop fade">
            <h5>{title}</h5>
            <div className="opts">{options.map(([v, l]) => <button key={v} className={`opt ${draft.includes(v) ? 'on' : ''}`} onClick={() => toggle(v)}>{l}</button>)}</div>
            <div className="row" style={{ gap: 8 }}>
              {draft.length > 0 && <button className="btn g" onClick={() => setDraft([])}>Сбросить</button>}
              <button className="btn y" style={{ flex: 1 }} onClick={() => { onChange(draft); setOpen(false) }}>Показать {count(draft)}</button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
