import { useEffect, useState, type ImgHTMLAttributes, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { AdStatus, Platform, RiskResult, Tri, Zone } from '../data/types'
import { adKey as codeKey } from '../lib/find'
import { useT } from '../lib/i18n'
import { PLATFORMS, platformName, type PlatformInfo } from '../lib/platform'
import { MARKERS, NOT_ASSESSED, isAssessed } from '../lib/risk'
import { siFacebook, siInstagram, siTelegram, siTiktok } from 'simple-icons'
import { IcCheck, IcChevronDown, IcChevronLeft, IcChevronRight, IcChevronUp, IcClose, IcGlobe, IcHelp, IcInfo as IcInfoY } from '../ui/icons'

export const zoneName: Record<Zone, string> = { red: 'Красная зона', yellow: 'Жёлтая зона', green: 'Зелёная зона' }
export const zoneShort: Record<Zone, string> = { red: 'Красная', yellow: 'Жёлтая', green: 'Зелёная' }

// Подписи зон и статусов на экране — на языке интерфейса (lib/i18n); таблицы zoneName, statusMeta — русские, как в данных

/** Зона риска; off — риск не оценивается (карточка из открытого реестра без проверенных сведений): серая метка без зоны */
export function ZoneTag({ zone, off }: { zone: Zone; off?: boolean }) {
  const { t, tr } = useT()
  return off
    ? <span className="tag gray" title={tr(NOT_ASSESSED)}>{t('common.riskNA')}</span>
    : <span className={`tag ${zone}`}><span className={`zdot ${zone}`} style={{ width: 6, height: 6 }} />{t(`zone.name.${zone}`)}</span>
}
export function ZoneDot({ zone, off }: { zone: Zone; off?: boolean }) {
  const { tr } = useT()
  return <span className={`zdot ${off ? 'na' : zone}`} title={off ? tr(NOT_ASSESSED) : undefined} />
}
/** Балл; off — риск не оценивается: «—» без цвета зоны */
export function Score({ value, zone, off }: { value: number; zone: Zone; off?: boolean }) {
  const { tr } = useT()
  return off
    ? <span className="score na" title={tr(NOT_ASSESSED)} aria-label={tr(NOT_ASSESSED)}>—</span>
    : <span className={`score ${zone}`}>{value}</span>
}
/** Балл и зона ЖК по результату риска: не оценённый (assessed false) — без балла и зоны */
export const RiskScore = ({ r }: { r: RiskResult }) => <Score value={r.score} zone={r.zone} off={!isAssessed(r)} />
export const RiskZone = ({ r }: { r: RiskResult }) => <ZoneTag zone={r.zone} off={!isAssessed(r)} />
export function NeedsCheck() {
  const { t } = useT()
  return <span className="tag yellow"><IcHelp size={14} />{t('common.needsCheck')}</span>
}

const TRI_KEY = { yes: 'common.yes', no: 'common.no', unknown: 'common.noInfo' } as const satisfies Record<Tri, string>
export function TriTag({ v, label }: { v: Tri; label?: string }) {
  const { t } = useT()
  return <span className={`tag ${v === 'yes' ? 'green' : v === 'no' ? 'red' : 'gray'}`}>{v === 'yes' ? <IcCheck size={13} strokeWidth={3} /> : v === 'no' ? <IcClose size={13} strokeWidth={3} /> : null}{label ?? t(TRI_KEY[v])}</span>
}

/** Площадки по коду (таблица — lib/platform). Есть и 'other' — публикация собрана с площадки не из списка: у неё своя подпись
 *  и общий значок, страницы с platformMeta[a.platform] на ней не падают */
export const platformMeta: Record<Platform, PlatformInfo> = PLATFORMS

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
  const png = PNG[p]
  // площадка не из списка — общий значок вместо битой картинки
  if (!png) return (
    <span className="plat" style={{ width: size, height: size, borderRadius: r, background: 'var(--fill)', color: 'var(--text-2)' }} title={platformName(p)}>
      <IcGlobe size={Math.round(size * 0.6)} />
    </span>
  )
  return <img className="plat" src={png} alt={platformMeta[p].name} title={platformMeta[p].name} style={{ width: size, height: size, borderRadius: r }} />
}

/** Картинка, которая может не загрузиться (файл удалён; офлайн-копия открыта без сервера её картинок — 404): вместо значка
 *  «битой» картинки — fallback (по умолчанию ничего: виден фон рамки .ph). Нет src — сразу fallback */
export function SafeImg({ src, fallback = null, ...rest }: ImgHTMLAttributes<HTMLImageElement> & { fallback?: ReactNode }) {
  const [broken, setBroken] = useState<string | null>(null)
  if (!src || broken === src) return <>{fallback}</>
  return <img src={src} {...rest} onError={() => setBroken(src)} />
}

export const statusMeta: Record<AdStatus, { name: string; tag: string; color: string }> = {
  new: { name: 'Новая', tag: 'gray', color: 'rgba(0,0,0,.32)' },
  needs_check: { name: 'Нужна проверка', tag: 'yellow', color: '#F59D00' },
  in_review: { name: 'На проверке', tag: 'blue', color: '#0065D9' },
  confirmed: { name: 'Нарушение', tag: 'red', color: '#E4402D' },
  dismissed: { name: 'Отклонено', tag: 'green', color: '#00B341' },
}
export function StatusTag({ s }: { s: AdStatus }) {
  const { t } = useT()
  return <span className={`tag ${statusMeta[s].tag}`}>{t(`status.${s}`)}</span>
}
export const StatusDot = ({ s }: { s: AdStatus }) => <span className="zdot" style={{ background: statusMeta[s].color }} />

// Словари подсветки — из модели риска: money → R2, sale → R3, safe — только подсветка. Длинные маркеры раньше коротких
const HL = ([[MARKERS.money, 'fund'], [MARKERS.sale, ''], [MARKERS.safe, 'safe']] as const)
  .flatMap(([ws, cls]) => ws.map((w) => [w, cls] as const)).sort((a, b) => b[0].length - a[0].length)
// Маркер — основа слова: «рассрочк» подсвечивает «рассрочка» целиком
const HL_RE = new RegExp(`(${HL.map(([w]) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + (/\p{L}$/u.test(w) ? '\\p{L}*' : '')).join('|')})`, 'giu')

/** Подсветка: красным — предложение денег, жёлтым — продажа, зелёным — признаки законности */
export function Highlight({ text }: { text: string }) {
  return (
    <>
      {text.split(HL_RE).map((p, i) => {
        if (i % 2 === 0) return <span key={i}>{p}</span>
        const low = p.toLowerCase()
        const cls = HL.find(([w]) => low.startsWith(w.toLowerCase()))?.[1]
        return <mark key={i} className={cls || undefined}>{p}</mark>
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

/** Переключатель; disabled — недоступен (приглушён, не нажимается), title — почему */
export const Toggle = ({ on, onChange, label, disabled, title }: { on: boolean; onChange: (v: boolean) => void; label?: string; disabled?: boolean; title?: string }) => (
  <button className={`toggle ${on ? 'on' : ''}`} role="switch" aria-checked={on} aria-label={label} title={title} disabled={disabled}
    style={disabled ? { opacity: 0.4, cursor: 'not-allowed' } : undefined} onClick={() => { if (!disabled) onChange(!on) }} />
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
  const { t } = useT()
  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal-wrap" onClick={(e) => e.stopPropagation()} style={wide ? { width: 'min(1040px, calc(100vw - 120px))' } : undefined}>
        <button className="x-btn" onClick={onClose} aria-label={t('common.close')}><IcClose /></button>
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
/** Склонение по числу: plural(2, ['объект', 'объекта', 'объектов']) → «объекта» */
export const plural = (n: number, [one, few, many]: readonly [string, string, string]) => {
  const a = Math.abs(n) % 100, b = a % 10
  return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many
}
/** Номер публикации (AD-007, AD-M1A2B3C4) — рядом с разбором номера в поиске (lib/find). У собранных публикаций id —
 *  32 символа (UUID hex): номер — AD- и первые 8 символов (AD-3F2A9C1B); поиск по началу номера их находит */
export const adKey = (id: string) => (id.length >= 20 ? `AD-${id.slice(0, 8).toUpperCase()}` : codeKey(id))

/** Кнопка фильтра с поповером — как «Комнат ⌄» на realty.yandex.ru: круглые варианты и жёлтая кнопка «Показать N» */
export function FilterPop<T extends string>({ label, title, value, options, onChange, count, multi }: {
  label: string; title: string; value: T[]; options: [T, ReactNode][]; onChange: (v: T[]) => void; count: (v: T[]) => number; multi?: boolean
}) {
  const { t } = useT()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<T[]>(value)
  useEffect(() => { if (open) setDraft(value) }, [open, value])
  const toggle = (v: T) => setDraft((d) => (d.includes(v) ? d.filter((x) => x !== v) : multi ? [...d, v] : [v]))
  const shown = value.length ? (value.length === 1 ? options.find((o) => o[0] === value[0])?.[1] : t('common.labelN', { label, n: value.length })) : label
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
              {draft.length > 0 && <button className="btn g" onClick={() => setDraft([])}>{t('common.reset')}</button>}
              <button className="btn y" style={{ flex: 1 }} onClick={() => { onChange(draft); setOpen(false) }}>{t('common.showN', { n: count(draft) })}</button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

/** Серая шапка раздела — как на realty.yandex.ru/ipoteka: крошки, заголовок, строка с «i», панель статистики и главный блок */
export function Hero({ crumb, title, info, actions, board, children }: { crumb: string; title: ReactNode; info?: ReactNode; actions?: ReactNode; board?: ReactNode; children?: ReactNode }) {
  return (
    <div className="band">
      <div className="page-title" style={{ paddingBottom: 16 }}>
        <div>
          <div className="crumbs"><Link to="/">SAQ Qurylys</Link><span>›</span><span>{crumb}</span></div>
          <h1 className="t-h1">{title}</h1>
          {info && <div className="row t-body" style={{ gap: 8, marginTop: 12 }}><span style={{ color: 'var(--link)', display: 'flex' }}><IcInfoY /></span>{info}</div>}
        </div>
        {actions && <div className="row">{actions}</div>}
      </div>
      {board}
      {children}
    </div>
  )
}

/** Аккордеон «Вопросы, которые задавал себе каждый» */
export function Faq({ title, sub, items }: { title: string; sub?: string; items: [string, ReactNode][] }) {
  const [open, setOpen] = useState<number | null>(null)
  return (
    <section className="faq">
      <h2 className="t-h2">{title}</h2>
      {sub && <p className="c2" style={{ margin: '8px 0 8px', maxWidth: 680 }}>{sub}</p>}
      {items.map(([q, a], i) => (
        <div key={i} className={`faq-i ${open === i ? 'on' : ''}`}>
          <button onClick={() => setOpen(open === i ? null : i)}><span>{q}</span>{open === i ? <IcChevronUp size={16} /> : <IcChevronDown size={16} />}</button>
          {open === i && <div className="faq-a fade">{a}</div>}
        </div>
      ))}
    </section>
  )
}

/** Колонки синих ссылок с серым количеством — как подвал выдачи Недвижимости */
export function LinkCols({ cols }: { cols: [string, string, number][][] }) {
  return (
    <div className="linkcols">
      {cols.map((col, i) => <div key={i}>{col.map(([l, to, n]) => <Link key={l} to={to}><span className="link">{l}</span><span className="c3">{n}</span></Link>)}</div>)}
    </div>
  )
}

/** Заголовок секции со стрелкой и кнопками карусели — «Новостройки от застройщиков ›  ‹ ›» */
export function SectionHead({ title, to, onPrev, onNext, right }: { title: string; to?: string; onPrev?: () => void; onNext?: () => void; right?: ReactNode }) {
  return (
    <div className="shead">
      {to ? <Link to={to} className="t-h2 row" style={{ gap: 4 }}>{title}<IcChevronRight size={20} /></Link> : <h2 className="t-h2">{title}</h2>}
      <div className="row" style={{ gap: 8 }}>{right}{onPrev && <><button className="arr" onClick={onPrev} aria-label="Назад"><IcChevronLeft size={16} /></button><button className="arr" onClick={onNext} aria-label="Вперёд"><IcChevronRight size={16} /></button></>}</div>
    </div>
  )
}
