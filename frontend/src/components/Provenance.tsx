// Происхождение сведений (ТЗ §4, §7 «Достоверность»): видно, что факт из источника, что демонстрационный пример,
// что гипотеза ИИ, а что решение аналитика. Бейдж — у сущности; вариант «у поля» — мелкой строкой рядом со значением.
import type { ReactNode } from 'react'
import type { BasisSource } from '../data/types'
import { OPEN_DATA_NOTE, when } from '../lib/provenance'
import { IcCheck, IcDatabase, IcDoc, IcExternal, IcHelp, IcInfo } from '../ui/icons'
import { AiBadge, Confidence } from './Hypothesis'

export type OriginKind = 'source' | 'demo' | 'ai' | 'analyst'

const LABEL: Record<OriginKind, string> = {
  source: 'Факт из источника', demo: 'Демонстрационные данные', ai: 'Гипотеза ИИ', analyst: 'Решение аналитика',
}
const HINT: Record<OriginKind, string> = {
  source: 'Сведения открытого источника — как их опубликовал источник, со ссылкой и временем сбора. Не проверены аналитиком и не являются выводом системы',
  demo: 'Демонстрационные данные: вымышленный сценарий для показа модели риска. Не сведения о реальных объектах и организациях',
  ai: 'Вывод ИИ: не факт, пока аналитик его не примет. Принятие и отклонение пишутся в журнал',
  analyst: 'Внесено или решено сотрудником: кто и когда — в журнале',
}

/** Ссылка на источник: внешняя, в новой вкладке */
export const SrcLink = ({ url, children }: { url: string; children?: ReactNode }) => (
  <a className="link prov-a" href={url} target="_blank" rel="noreferrer">{children ?? 'источник'}<IcExternal size={12} /></a>
)

interface OriginProps {
  kind: OriginKind
  /** Своя подпись вместо «Факт из источника» и т. п. */
  label?: ReactNode
  /** source — название источника */
  source?: string | null
  /** Время фиксации (source) или решения (analyst) */
  at?: string | null
  url?: string | null
  /** analyst — кто решил */
  who?: string | null
  /** ai — уверенность 0…1 */
  confidence?: number | null
  low?: boolean
  /** Вариант «у поля»: иконка и текст мелко, с подробностями */
  field?: boolean
  /** Бейдж на фото (белая подложка) */
  onPhoto?: boolean
  title?: string
}

/** Метка происхождения: бейдж (по умолчанию) или строка у поля */
export function Origin({ kind, label, source, at, url, who, confidence, low, field, onPhoto, title }: OriginProps) {
  const text = label ?? LABEL[kind]
  const details = kind === 'source' ? [source, at && `зафиксировано ${when(at)}`] : kind === 'analyst' ? [who, when(at)] : []
  const hint = title ?? [HINT[kind], ...details.filter(Boolean)].join('. ')
  if (field) {
    const Icon = kind === 'source' ? IcDatabase : kind === 'analyst' ? IcCheck : kind === 'demo' ? IcInfo : null
    if (kind === 'ai') return (
      <span className="prov-f ai" title={hint}>
        <AiBadge label={typeof text === 'string' ? text.toLowerCase() : undefined} />
        {confidence != null && <Confidence value={confidence} low={low} />}
      </span>
    )
    return (
      <span className={`prov-f ${kind}`} title={hint}>
        {Icon && <Icon size={14} />}
        <span><b>{text}</b>{details.filter(Boolean).map((d, i) => <span key={i}> · {d}</span>)}{url && <> · <SrcLink url={url} /></>}</span>
      </span>
    )
  }
  if (kind === 'ai') return <AiBadge label={typeof text === 'string' ? text.toLowerCase() : 'гипотеза ИИ'} title={title} />
  const cls = kind === 'source' ? 'src' : kind === 'demo' ? 'demo' : 'green'
  const Icon = kind === 'source' ? IcDatabase : kind === 'analyst' ? IcCheck : IcInfo
  return <span className={`tag prov ${cls} ${onPhoto ? 'on-ph' : ''}`} title={hint}><Icon size={12} />{text}</span>
}

/** Где и когда сверен документ основания: источник, дата, ссылка; нет записи — «не проверялся» */
export function DocSourceLine({ src, general, none }: { src?: BasisSource | null; general?: boolean; none?: ReactNode }) {
  if (!src?.source) return (
    <span className="prov-f none" title="Источника и даты сверки по этому документу нет: его статус не установлен">
      <IcHelp size={14} /><span>{none ?? 'Не проверялся: источника и даты сверки нет'}</span>
    </span>
  )
  return (
    <span className="prov-f src" title={general ? 'Своей записи о сверке у документа нет — показана общая сверка оснований' : 'Где и когда сверен документ'}>
      <IcDoc size={14} />
      <span>{general && 'общая сверка: '}{src.source}{src.checkedAt ? ` · сверено ${when(src.checkedAt)}` : ' · дата сверки не указана'}{src.url && <> · <SrcLink url={src.url} /></>}</span>
    </span>
  )
}

// ---------- Открытые данные ----------

/** Плашка у реальных объектов (решение 1 SPEC-E): риск, зоны и сигналы для них не считаются */
export function OpenDataNote({ children }: { children?: ReactNode }) {
  return (
    <div className="box od-note t-sm">
      <IcInfo size={18} />
      <div><b>Открытые данные.</b> {OPEN_DATA_NOTE}{children && <div style={{ marginTop: 6 }}>{children}</div>}</div>
    </div>
  )
}
