// Панели статистики в шапках разделов: у каждого экрана своя форма (конвейер, светофор, кольцо, лента свежести),
// собранная из общих деталей — показатель, полоса долей, кольцо, этапы, шкала, строка-действие. Стили — styles/board.css
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useT } from '../lib/i18n'
import { Emblem, type EmblemName, type EmTone } from './emblems'

type Act = { to?: string; onClick?: () => void; active?: boolean; title?: string }

/** Ссылка, кнопка или статичный блок — по тому, что передано */
function Hit({ to, onClick, active, title, className, children }: Act & { className: string; children: ReactNode }) {
  const cls = `${className}${active ? ' on' : ''}${to || onClick ? ' hit' : ''}`
  if (to) return <Link to={to} className={cls} title={title}>{children}</Link>
  if (onClick) return <button type="button" className={cls} onClick={onClick} title={title} aria-pressed={active}>{children}</button>
  return <div className={cls} title={title}>{children}</div>
}

const fmt = (v: ReactNode) => (typeof v === 'number' ? v.toLocaleString('ru-RU') : v)
const pct = (v: number, of: number) => (of > 0 ? Math.round((v / of) * 100) : 0)

/** Панель: белая карточка; вид раскладки — модификатор (sb-queue, sb-ops…) */
export function Board({ kind, children }: { kind: string; children: ReactNode }) {
  return <section className={`sb sb-${kind}`}>{children}</section>
}

/** Подпись блока внутри панели */
export function Cap({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return <div className="sb-cap"><span>{children}</span>{aside && <span className="sb-cap-a">{aside}</span>}</div>
}

/** Показатель: эмблема, крупное значение, подпись, пояснение */
export function Kpi({ em, tone, value, label, hint, big, ...act }: Act & { em: EmblemName; tone?: EmTone; value: ReactNode; label: ReactNode; hint?: ReactNode; big?: boolean }) {
  const zero = value === 0
  return (
    <Hit className={`sb-kpi${big ? ' big' : ''}${zero ? ' zero' : ''}`} {...act}>
      <Emblem name={em} tone={zero ? 'gray' : tone} size={big ? 56 : 44} />
      <span className="sb-kpi-t">
        <b className={`sb-v ${zero ? '' : tone ?? ''}${typeof value === 'string' && value.length > 6 ? ' long' : ''}`}>{fmt(value)}</b>
        <span className="sb-l">{label}</span>
        {hint && <span className="sb-h">{hint}</span>}
      </span>
    </Hit>
  )
}

export type Part = Act & { key: string; label: ReactNode; value: number; tone: EmTone }

/** Полоса долей с легендой: доля каждого сегмента от суммы, сегменты и строки легенды нажимаются */
export function Shares({ parts, total, bar = true }: { parts: Part[]; total?: number; bar?: boolean }) {
  const sum = total ?? parts.reduce((s, p) => s + p.value, 0)
  return (
    <div className="sb-shares">
      {bar && <div className="sb-bar" role="img" aria-label={parts.map((p) => `${typeof p.label === 'string' ? p.label : p.key}: ${p.value}`).join(', ')}>
        {sum === 0 ? <i className="empty" /> : parts.filter((p) => p.value > 0).map((p) => (
          <i key={p.key} className={`${p.tone}${p.active ? ' on' : ''}`} style={{ flexGrow: p.value }} title={`${p.value} · ${pct(p.value, sum)}%`} />
        ))}
      </div>}
      <div className="sb-legend">
        {parts.map(({ key, label, value, tone, ...act }) => (
          <Hit key={key} className={`sb-li${value === 0 ? ' zero' : ''}`} {...act}>
            <i className={`dot ${tone}`} />
            <span className="sb-li-l">{label}</span>
            <span className="sb-li-n"><b>{fmt(value)}</b><span>{pct(value, sum)}%</span></span>
          </Hit>
        ))}
      </div>
    </div>
  )
}

/** Кольцо долей: в центре — итог */
export function Ring({ parts, value, label, size = 148 }: { parts: Part[]; value: ReactNode; label: ReactNode; size?: number }) {
  const sum = parts.reduce((s, p) => s + p.value, 0)
  const r = 54
  const c = 2 * Math.PI * r
  const gap = parts.filter((p) => p.value > 0).length > 1 ? 3 : 0
  let at = 0
  return (
    <div className="sb-ring" style={{ width: size, height: size }}>
      <svg viewBox="0 0 140 140" width={size} height={size}>
        <circle cx="70" cy="70" r={r} className="track" />
        {sum > 0 && parts.filter((p) => p.value > 0).map((p) => {
          const len = (p.value / sum) * c
          const seg = <circle key={p.key} cx="70" cy="70" r={r} className={`arc ${p.tone}${p.active ? ' on' : ''}`}
            strokeDasharray={`${Math.max(len - gap, 1)} ${c}`} strokeDashoffset={-at} transform="rotate(-90 70 70)" />
          at += len
          return seg
        })}
      </svg>
      <div className="sb-ring-c"><b>{fmt(value)}</b><span>{label}</span></div>
    </div>
  )
}

export type Stage = Act & { em: EmblemName; tone?: EmTone; value: number; label: ReactNode }

/** Этапы слева направо со стрелками; последний шаг — развилка из нескольких исходов */
export function Flow({ stages, outcomes }: { stages: Stage[]; outcomes?: Stage[] }) {
  const step = ({ em, tone, value, label, ...act }: Stage, k: string) => (
    <Hit key={k} className={`sb-step${value === 0 ? ' zero' : ''}`} {...act}>
      <Emblem name={em} tone={value === 0 ? 'gray' : tone} size={40} />
      <b className={`sb-v ${value === 0 ? '' : tone ?? ''}`}>{fmt(value)}</b>
      <span className="sb-l">{label}</span>
    </Hit>
  )
  return (
    <div className="sb-flow">
      {stages.map((s, i) => [i > 0 && <Arrow key={`a${i}`} />, step(s, `s${i}`)])}
      {outcomes && <><Arrow fork /><div className="sb-fork">{outcomes.map((s, i) => step(s, `o${i}`))}</div></>}
    </div>
  )
}

function Arrow({ fork }: { fork?: boolean }) {
  return (
    <svg className="sb-arrow" viewBox="0 0 28 64" width="28" height="64" aria-hidden>
      {fork
        ? <path d="M2 32h8c4 0 6-2 6-6V16c0-4 2-6 6-6h4M16 32v16c0 4 2 6 6 6h4M22 6l4 4-4 4M22 50l4 4-4 4" />
        : <path d="M2 32h22M18 26l6 6-6 6" />}
    </svg>
  )
}

/** Шкала «value из of» с подписью */
export function Meter({ value, of, tone, label, ...act }: Act & { value: number; of: number; tone: EmTone; label: ReactNode }) {
  const { t } = useT()
  return (
    <Hit className="sb-meter" {...act}>
      <span className="sb-meter-t"><span>{label}</span><b>{fmt(value)} <span className="c3">{t('common.ofN', { n: of.toLocaleString('ru-RU') })}</span></b></span>
      <span className="sb-meter-b"><i className={tone} style={{ width: `${pct(value, of)}%` }} /></span>
    </Hit>
  )
}

/** Строка-переход: эмблема, подпись, значение, стрелка */
export function Row({ em, tone, label, value, hint, ...act }: Act & { em: EmblemName; tone?: EmTone; label: ReactNode; value: ReactNode; hint?: ReactNode }) {
  const zero = value === 0
  return (
    <Hit className={`sb-row${zero ? ' zero' : ''}`} {...act}>
      <Emblem name={em} tone={zero ? 'gray' : tone} size={36} />
      <span className="sb-row-l">{label}{hint && <span className="sb-h">{hint}</span>}</span>
      <b className={`sb-v ${zero ? '' : tone ?? ''}`}>{fmt(value)}</b>
      {(act.to || act.onClick) && <svg className="sb-chev" viewBox="0 0 16 16" width="16" height="16" aria-hidden><path d="M6 3l5 5-5 5" /></svg>}
    </Hit>
  )
}

export type Fact = { text: ReactNode; tone?: 'green' | 'yellow' | 'red' }

/** Звено цепочки источников: номер шага, эмблема, значение, подпись, факты-плашки; стрелка к следующему звену */
export function Chain({ n, em, tone, value, label, facts, next, ...act }: Act & { n: string; em: EmblemName; tone?: EmTone; value: ReactNode; label: ReactNode; facts?: Fact[]; next?: boolean }) {
  return (
    <Hit className="sb-src" {...act}>
      <span className="sb-src-top"><span className="sb-src-n">{n}</span><Emblem name={em} tone={tone} size={40} /></span>
      <span className="sb-kpi-t"><b className={`sb-v ${tone ?? ''}${typeof value === 'string' && value.length > 6 ? ' long' : ''}`}>{fmt(value)}</b><span className="sb-l">{label}</span></span>
      {facts && facts.length > 0 && <span className="sb-src-facts">{facts.map((f, i) => <span key={i} className={`sb-pill ${f.tone ?? ''}`}>{f.tone && <i className={`dot ${f.tone}`} />}{f.text}</span>)}</span>}
      {next && <span className="sb-src-step" aria-hidden><svg viewBox="0 0 12 12" width="12" height="12"><path d="M4 2l4 4-4 4" /></svg></span>}
    </Hit>
  )
}
