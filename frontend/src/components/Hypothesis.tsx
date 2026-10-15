// Выводы ИИ (ТЗ §6): метка «гипотеза ИИ», уверенность, причины сопоставления, условия оплаты с цитатами.
// Гипотеза — не факт: в балл она входит, только когда аналитик её примет.
import type { ReactNode } from 'react'
import type { LinkHypothesis, LinkReason, ReasonFeature, Term } from '../data/types'
import { IcCamera, IcCheck, IcSparkSearch } from '../ui/icons'
import { Highlight } from './ui'

/** Метка вывода ИИ: по умолчанию «гипотеза ИИ» — не факт, пока аналитик не примет */
export function AiBadge({ label = 'гипотеза ИИ', title }: { label?: string; title?: string }) {
  return (
    <span className="tag blue" title={title ?? 'Вывод ИИ: не факт, пока аналитик его не примет. Принятие и отклонение пишутся в журнал'}>
      <IcSparkSearch size={12} />{label}
    </span>
  )
}

/** Уверенность ИИ: число и полоска; низкая — жёлтая пометка */
export function Confidence({ value, low }: { value: number; low?: boolean }) {
  const p = Math.round(Math.max(0, Math.min(1, value || 0)) * 100)
  return (
    <span className={`conf ${low ? 'low' : ''}`} title={`Уверенность ИИ ${p}%${low ? ' — низкая, вывод нужно проверить' : ''}`}>
      <span className="conf-bar" aria-hidden><i style={{ width: `${p}%` }} /></span>
      <b className="num">{p}%</b>
      {low && <span className="tag yellow">низкая уверенность</span>}
    </span>
  )
}

const FEATURE: Record<ReasonFeature, string> = {
  name: 'Название', translit: 'Транслитерация', address: 'Адрес', account: 'Аккаунт', phone: 'Телефон', history: 'История аккаунта', district: 'Район',
}

/** Пояснение без повтора названия признака: «Телефон +7…» под меткой «Телефон» → «+7…» */
const detailOf = (label: string, detail: string) =>
  detail.toLowerCase().startsWith(label.toLowerCase() + ' ') ? detail.slice(label.length + 1) : detail

/** Почему ИИ связал публикацию с ЖК: признак и что именно совпало */
export function ReasonList({ reasons }: { reasons: LinkReason[] }) {
  if (!reasons.length) return null
  return (
    <ul className="ai-reasons">
      {reasons.map((r, i) => {
        const label = FEATURE[r.feature] ?? r.feature
        return <li key={i}><span className="tag gray">{label}</span><span>{detailOf(label, r.detail)}</span></li>
      })}
    </ul>
  )
}

const CURRENCY: Record<string, string> = { KZT: '₸', USD: '$', RUB: '₽' }
const money = (n: number, cur?: string | null) => {
  const v = n.toLocaleString('ru-RU')
  return cur === 'USD' ? `$${v}` : `${v} ${CURRENCY[cur ?? 'KZT'] ?? cur}`
}

/** Значения условия одной строкой: «200 000 ₸», «290 000 ₸/м²», «30%», «18 мес.» */
function termValue(t: Term): string {
  const xs: string[] = []
  if (t.amount != null) xs.push(money(t.amount, t.currency) + (t.unit === 'm2' ? '/м²' : ''))
  if (t.percent != null) xs.push(`${t.percent.toLocaleString('ru-RU')}%`)
  if (t.months != null) xs.push(`${t.months} мес.`)
  return xs.join(' · ')
}

/** Пометка источника цитаты: распознанный скриншот шумнее текста */
export const OcrTag = () => (
  <span className="tag outline" title="Фрагмент взят из текста, распознанного на скриншоте: возможны ошибки распознавания">
    <IcCamera size={12} />из скриншота (OCR)
  </span>
)

/** Условия оплаты: что найдено, значения, дословная цитата, уверенность, источник */
export function TermList({ terms, empty }: { terms: Term[]; empty?: ReactNode }) {
  if (!terms.length) return <div className="t-sm c2">{empty ?? 'Условий оплаты не найдено'}</div>
  return (
    <div className="ai-list">
      {terms.map((t, i) => {
        const v = termValue(t)
        return (
          <div key={i} className="ai-item">
            <div className="row between" style={{ gap: 8, alignItems: 'flex-start' }}>
              <span className="bold">{t.label}{v && <span className="num"> · {v}</span>}</span>
              <Confidence value={t.confidence} low={t.low} />
            </div>
            <div className="ai-q">«<Highlight text={t.quote} />»</div>
            {(t.from === 'ocr' || !t.money) && (
              <div className="row wrap-f" style={{ gap: 6, marginTop: 6 }}>
                {t.from === 'ocr' && <OcrTag />}
                {!t.money && <span className="tag gray" title="Цена или условие без требования внести деньги — для R2 не учитывается">не предложение денег</span>}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

/** Гипотеза «публикация про ЖК X»: уверенность, причины, фрагмент; pick — выбор в форме, children — кнопки решения.
 *  При выборе кнопка — только заголовок (название и уверенность): в кнопке допустимы лишь строчные элементы, а её имя для
 *  скринридера — «ЖК «X» N%», а не весь текст карточки; щелчок по остальной карточке выбирает так же */
export function HypothesisItem({ h, selected, onPick, note, children }: {
  h: LinkHypothesis; selected?: boolean; onPick?: () => void; note?: ReactNode; children?: ReactNode
}) {
  const head = (
    <>
      <span className="row bold" style={{ gap: 8 }}>
        {onPick && <span className={`ai-radio ${selected ? 'on' : ''}`} aria-hidden>{selected && <IcCheck size={12} strokeWidth={3} />}</span>}
        ЖК «{h.complexName}»
      </span>
      <Confidence value={h.confidence} low={h.low} />
    </>
  )
  const rest = (
    <>
      <ReasonList reasons={h.reasons} />
      {h.quote && <div className="ai-q c2">«<Highlight text={h.quote} />»</div>}
      {note && <div className="t-xs c2" style={{ marginTop: 6 }}>{note}</div>}
    </>
  )
  if (onPick) return (
    <div className={`ai-item ai-pick ${selected ? 'on' : ''}`} onClick={onPick}>
      <button type="button" className="ai-pick-head" aria-pressed={!!selected} onClick={(e) => { e.stopPropagation(); onPick() }}>{head}</button>
      {rest}
    </div>
  )
  return (
    <div className="ai-item">
      <div className="row between" style={{ gap: 8, alignItems: 'flex-start' }}>{head}</div>
      {rest}
      {children && <div className="row wrap-f" style={{ gap: 8, marginTop: 10 }}>{children}</div>}
    </div>
  )
}
