// «Проверка по рекламе»: значки, блок с основанием и причинами, счётчики. Оценка предварительная и автоматическая —
// рядом всегда дисклеймер (коротко, полный текст — в подсказке). Подписи и порядок — lib/screening.ts.
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { Advert, DeveloperScreening, Permit, Screening, ScreeningReason, ScreeningStatus, ScreeningVariant } from '../data/types'
import type { From } from '../lib/nav'
import {
  BASIS_HINT, BASIS_LABEL, DISCLAIMER, DISCLAIMER_SHORT, NO_SCREENING, PERMIT_LABEL, PERMIT_TONE, SCREENING_GLYPH, SCREENING_ORDER,
  SCREENING_SHORT, SCREENING_TONE, adReasons, countsLine, decisionReasons, labelOf, permitLinks, permitReason, permitSource, reasonParts, shortOf,
  signalName, variantOf,
} from '../lib/screening'
import { IcInfo } from '../ui/icons'
import { SrcLink } from './Provenance'
import { adKey } from './ui'

type State = { from: From } | undefined

/** Круг со знаком: ✓ ? ! — */
export const ScreeningPic = ({ status, size }: { status: ScreeningStatus | 'all'; size?: number }) => (
  <span className={`scr-pic ${status}`} aria-hidden style={size ? { width: size, height: size, fontSize: Math.round(size * 0.55) } : undefined}>
    {status === 'all' ? '' : SCREENING_GLYPH[status]}
  </span>
)

/** Метка статуса: знак и подпись (коротко у решения аналитика — «сдан» / «проверено»); в подсказке — подпись и дисклеймер */
export function ScreeningTag({ s, dev, short, big }: {
  s: Pick<Screening, 'status' | 'label'> & { disclaimer?: string; variant?: ScreeningVariant | null }; dev?: boolean; short?: boolean; big?: boolean
}) {
  const label = labelOf(s, dev)
  return (
    <span className={`tag scr-tag ${SCREENING_TONE[s.status]} ${big ? 'big' : ''}`} title={`Проверка по рекламе: ${label}. ${s.disclaimer || DISCLAIMER}`}>
      <span className="scr-g">{SCREENING_GLYPH[s.status]}</span>{short ? (dev ? SCREENING_SHORT[s.status] : shortOf(s)) : label}
    </span>
  )
}

/** Дисклеймер: коротко, полный текст — в подсказке; full — полностью */
export const Disclaimer = ({ text, full }: { text?: string | null; full?: boolean }) => (
  <span className="scr-disc" title={text || DISCLAIMER}><IcInfo size={13} />{full ? text || DISCLAIMER : DISCLAIMER_SHORT}</span>
)

/** Оценки нет: сервер старой версии */
export const NoScreening = ({ children }: { children?: ReactNode }) => <div className="ai-note">{NO_SCREENING}{children}</div>

/** Основание по открытым данным: статус, текст сервера, источник (карточка открытых данных и внешняя ссылка) */
export function PermitLine({ p, state }: { p: Permit; state?: State }) {
  const links = permitLinks(p)
  const src = permitSource(p.source)
  return (
    <div className="scr-permit">
      <span className={`tag ${PERMIT_TONE[p.status]}`}>{PERMIT_LABEL[p.status]}{p.verified ? ' · сверено аналитиком' : ''}</span>
      <span>{p.text}</span>
      {(links.registry || links.url || src) && (
        <span className="scr-links">
          {src && <span className="c2">Источник: {src}</span>}
          {links.registry && <Link className="link" to={links.registry} state={state}>Записи открытых данных</Link>}
          {links.url && <SrcLink url={links.url}>страница источника</SrcLink>}
        </span>
      )}
    </div>
  )
}

/** Текст причины: цитата, вписанная сервером в текст, выделена на месте; иначе — отдельной строкой */
export function ReasonText({ r }: { r: Pick<ScreeningReason, 'text' | 'quote'> }) {
  const p = reasonParts(r)
  if (p.inline) return <span>{p.before}<b className="scr-q">«{p.quote}»</b>{p.after}</span>
  return <span>{p.before}{p.quote && <span className="scr-q" style={{ display: 'block', marginTop: 2 }}>«{p.quote}»</span>}</span>
}

/** Причины по рекламе: признак, текст с цитатой, ссылка на публикацию */
export function ReasonList({ reasons, state }: { reasons: ScreeningReason[]; state?: State }) {
  if (!reasons.length) return null
  return (
    <div className="ev-list" style={{ marginTop: 0 }}>
      {reasons.map((r, i) => {
        const body = (
          <div>
            {r.signal && <span className="tag outline" style={{ height: 20, fontSize: 12, marginRight: 6, verticalAlign: 1 }}>{signalName(r.signal)}</span>}
            <ReasonText r={r} />
            {r.adId && <div className="meta" style={{ marginTop: 3 }}><span className="link">Публикация {adKey(r.adId)}</span></div>}
          </div>
        )
        return r.adId
          ? <Link key={`${r.adId}-${i}`} to={`/ads/${encodeURIComponent(r.adId)}`} state={state} className="ev scr-reason" style={{ gridTemplateColumns: 'minmax(0, 1fr)' }}>{body}</Link>
          : <div key={`r-${i}`} className="ev scr-reason" style={{ gridTemplateColumns: 'minmax(0, 1fr)' }}>{body}</div>
      })}
    </div>
  )
}

/** Другие названия и аккаунты рекламы ЖК */
export function AdvertLine({ a }: { a: Advert }) {
  const parts: ReactNode[] = []
  if (a.aliases.length) parts.push(<span key="al"><b>Названия в рекламе:</b> {a.aliases.join(', ')}</span>)
  if (a.accounts.length) parts.push(<span key="ac"><b>Аккаунты:</b> {a.accounts.join(', ')}</span>)
  if (!parts.length) return null
  return <div className="scr-line row wrap-f" style={{ gap: '2px 14px' }}>{parts}</div>
}

/** Блок «Проверка по рекламе»: статус, подпись, основание, причины с цитатами, счётчики, на чём основано, дисклеймер.
 *  compact — для панели карты: меньше причин, без названий рекламы */
export function ScreeningBlock({ s, permit, advert, state, compact, title = 'Проверка по рекламе', extra }: {
  s: Screening; permit?: Permit | null; advert?: Advert | null; state?: State; compact?: boolean; title?: string; extra?: ReactNode
}) {
  const tone = SCREENING_TONE[s.status]
  // решение аналитика (сведения решения или «новая реклама после решения») — над основанием, остальные причины — под ним
  const dec = decisionReasons(s).slice(0, compact ? 1 : 2)
  const reasons = adReasons(s).filter((r) => r.kind !== 'decision').slice(0, (compact ? 2 : 5) - dec.length)
  const pr = permitReason(s)
  return (
    <section className={`scr-block ${tone} ${compact ? 'compact' : ''}`} aria-label={title}>
      <div className="scr-head">
        <ScreeningPic status={s.status} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="scr-kicker">{title}{variantOf(s) && ' · решение аналитика'}</div>
          <div className="scr-title">{labelOf(s)}</div>
        </div>
        {!compact && <span className="tag outline" title={BASIS_HINT[s.basis]} style={{ background: 'rgba(255,255,255,.6)' }}>{BASIS_LABEL[s.basis]}</span>}
      </div>
      <Disclaimer text={s.disclaimer} full={!compact} />
      <ReasonList reasons={dec} state={state} />
      {permit ? <PermitLine p={permit} state={state} /> : pr && <div className="t-sm">{pr.text}</div>}
      <ReasonList reasons={reasons} state={state} />
      <div className="scr-line"><b>Реклама:</b> {countsLine(s.counts)}{!s.inRegistry && <> · <b>в открытом реестре нет</b></>}{compact && <span title={BASIS_HINT[s.basis]}> · оценка {BASIS_LABEL[s.basis]}</span>}</div>
      {!compact && advert && <AdvertLine a={advert} />}
      {extra}
    </section>
  )
}

/** Счётчики ЖК застройщика по статусам: ● 1 ● 2 … (нули не показываем) */
export function StatusCounts({ c }: { c: Partial<Record<ScreeningStatus, number>> }) {
  const xs = SCREENING_ORDER.filter((k) => (c[k] ?? 0) > 0)
  if (!xs.length) return <span className="scr-counts">ЖК на контроле нет</span>
  return (
    <span className="scr-counts">
      {xs.map((k) => <span key={k} title={SCREENING_SHORT[k]}><ScreeningPic status={k} /><b>{c[k]}</b>{SCREENING_SHORT[k]}</span>)}
    </span>
  )
}

/** Худшие ЖК застройщика — ссылками на карточки */
export function WorstList({ worst, state, max = 3 }: { worst: DeveloperScreening['worst']; state?: State; max?: number }) {
  const xs = worst.filter((w) => w.status !== 'ok').slice(0, max)
  if (!xs.length) return null
  return (
    <span className="row wrap-f" style={{ gap: 6 }}>
      {xs.map((w) => (
        <Link key={w.complexId} to={`/complexes/${encodeURIComponent(w.complexId)}`} state={state} className={`tag scr-tag ${SCREENING_TONE[w.status]}`}
          title={`${w.label} — открыть карточку ЖК`} onClick={(e) => e.stopPropagation()}>
          <span className="scr-g">{SCREENING_GLYPH[w.status]}</span>ЖК «{w.name}»
        </Link>
      ))}
      {worst.filter((w) => w.status !== 'ok').length > max && <span className="t-xs c2">и ещё {worst.filter((w) => w.status !== 'ok').length - max}</span>}
    </span>
  )
}

/** Блок застройщика: статус, подпись, причины, счётчики по ЖК, худшие ЖК, дисклеймер */
export function DevScreeningBlock({ s, state }: { s: DeveloperScreening; state?: State }) {
  const tone = SCREENING_TONE[s.status]
  return (
    <section className={`scr-block ${tone} compact`} aria-label="Проверка по рекламе: застройщик">
      <div className="scr-head">
        <ScreeningPic status={s.status} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="scr-kicker">Проверка по рекламе · застройщик</div>
          <div className="scr-title">{labelOf(s, true)}</div>
        </div>
      </div>
      <Disclaimer text={s.disclaimer} />
      {s.reasons.map((r, i) => <div key={i} className="t-sm">{r.text}</div>)}
      <div className="row between wrap-f" style={{ gap: 8 }}>
        <StatusCounts c={s.counts} />
        <span className="t-xs c2" title={BASIS_HINT[s.basis]}>ЖК на контроле: {s.counts.complexes} · оценка {BASIS_LABEL[s.basis]}</span>
      </div>
      <WorstList worst={s.worst} state={state} />
    </section>
  )
}
