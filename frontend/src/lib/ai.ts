// Выводы ИИ в интерфейсе (ТЗ §6): поле OCR при смене скриншота, уверенность в текущей привязке-гипотезе,
// какие гипотезы показывать после решений аналитика, какие поля публикации меняет его решение.
import type { Ad, Analysis, LinkHypothesis, OcrResult } from '../data/types'
import { fmtStamp } from './audit'

export const pct = (x: number) => `${Math.round((x || 0) * 100)}%`

// ───── Поле «Текст на изображении» ─────

/** text — содержимое поля; auto — что в него последним положил OCR (если не правили, text === auto);
 *  kept — в поле текст, введённый вручную до загрузки текущего скриншота: OCR его не заменил */
export interface OcrField { text: string; auto: string; kept: boolean }
export const OCR_EMPTY: OcrField = { text: '', auto: '', kept: false }

/** Новый скриншот заменил прежний: распознанный с прежнего текст (не правленный) к новому не относится — убираем;
 *  введённый вручную — оставляем, но с пометкой, чтобы аналитик проверил его */
export function ocrOnNewShot(f: OcrField): OcrField {
  const t = f.text.trim()
  return t && t !== f.auto.trim() ? { text: f.text, auto: '', kept: true } : OCR_EMPTY
}

/** Ответ OCR: найденный текст заменяет поле; пусто или OCR недоступен — поле не трогаем (ручной ввод остаётся) */
export function ocrOnResult(f: OcrField, r: Pick<OcrResult, 'text' | 'engine'>): OcrField {
  const t = r.text.trim()
  return r.engine !== 'none' && t ? { text: t, auto: t, kept: false } : f
}

/** Аналитик поправил поле — пометка «текст от прошлого скриншота» снята: он его видел */
export const ocrOnEdit = (f: OcrField, text: string): OcrField => ({ text, auto: f.auto, kept: false })

export const OCR_NOT_FOUND = 'Текст на изображении не найден'
export const OCR_KEPT = 'В поле ниже — текст, введённый вручную до загрузки этого скриншота: проверьте, что он относится к нему.'

/** Пояснение сервера без того, что уже сказано: «текст не найден», состояние OCR из /ocr/status */
function extraOf(detail: string | null | undefined, known: (string | null | undefined)[]) {
  let d = detail ?? ''
  for (const k of known) if (k) d = d.split(k).join('')
  d = d.replace(/^[\s.·]+|[\s.·]+$/g, '').replace(/\.(\s*\.)+/g, '.')
  return d || undefined
}

/** Что сказать после распознавания: OCR на сервере недоступен, текста нет (с причиной сервера, без повторов) или ничего */
export function ocrNotice(r: OcrResult | null, statusDetail?: string | null, kept = false): { text: string; extra?: string } | null {
  if (!r) return null
  if (r.engine === 'none') {
    return {
      text: `На сервере не работает OCR (Tesseract) — текст со скриншота не распознан. ${kept ? OCR_KEPT : 'Перепечатайте его в поле ниже: анализ ИИ учтёт его так же.'}`,
      extra: extraOf(r.detail, [statusDetail]),
    }
  }
  if (r.text.trim()) return null
  const why = extraOf(r.detail, [OCR_NOT_FOUND, statusDetail])
  return { text: `${why ? `Текст не распознан: ${why}.` : `${OCR_NOT_FOUND}.`} ${kept ? OCR_KEPT : 'Если на скриншоте есть текст — введите его вручную.'}` }
}

// ───── Текущая привязка к ЖК ─────

/** Уверенность в текущей привязке-гипотезе. now — гипотеза этого ЖК в текущем анализе (её число сохранится при принятии);
 *  saved — записанная при сборе или добавлении публикации; differs — числа на экране разные */
export interface LinkConf { now?: LinkHypothesis; saved: number; when: string; differs: boolean }

export function linkConf(a: Pick<Ad, 'complexId' | 'matchConfidence' | 'source'>, data?: Pick<Analysis, 'hypotheses'> | null): LinkConf {
  const now = a.complexId ? data?.hypotheses.find((h) => h.complexId === a.complexId) : undefined
  const saved = a.matchConfidence || 0
  return { now, saved, when: a.source === 'manual' ? 'при добавлении' : 'при сборе', differs: !!now && pct(now.confidence) !== pct(saved) }
}

/** Чья привязка и на каком основании: факт — после подтверждения аналитиком; иначе гипотеза ИИ — с уверенностью по текущему
 *  анализу (сохранённая — отдельно, если отличается) или, без анализа, с сохранённой и пометкой, когда она получена. short — метка в панели */
export function linkText(a: Pick<Ad, 'linkConfirmedBy' | 'linkConfirmedAt' | 'status'>, lc: LinkConf, short = false) {
  if (a.linkConfirmedBy) return `факт · ${short ? '' : 'подтвердил '}${a.linkConfirmedBy}${a.linkConfirmedAt && !short ? `, ${fmtStamp(a.linkConfirmedAt)}` : ''}`
  if (a.status === 'confirmed') return short ? 'факт · нарушение' : 'факт · публикация подтверждена как нарушение'
  const tail = short ? '' : ' · аналитик не подтвердил'
  const { now } = lc
  if (!now) return `гипотеза ИИ ${lc.when} ${pct(lc.saved)}${tail}`
  const low = now.low ? (short ? ' · низкая' : ', низкая уверенность') : ''
  return `гипотеза ИИ ${pct(now.confidence)}${low}${!short && lc.differs ? ` (${lc.when} — ${pct(lc.saved)})` : ''}${tail}`
}

// ───── Решения аналитика ─────

/** Гипотезы для показа: без ЖК, отклонённых аналитиком. Отклонённые — из публикации в сторе (обновляется сразу после ответа),
 *  иначе из анализа: пока анализ перечитывается, отклонённая гипотеза не висит с активными кнопками */
export function visibleHypotheses(a: Pick<Ad, 'rejectedLinks'>, data?: Pick<Analysis, 'hypotheses'> & { rejectedLinks?: string[] } | null) {
  const rejected = a.rejectedLinks ?? data?.rejectedLinks ?? []
  return { rejected, hypotheses: (data?.hypotheses ?? []).filter((h) => !rejected.includes(h.complexId)) }
}

/** Поля публикации, которые меняет решение по выводу ИИ (принять/отклонить гипотезу, условия оплаты). Статус и ответственного
 *  ведёт смена статуса: ответ решения, пришедший позже параллельного PATCH /status, их не откатывает; скриншот вкладки остаётся */
export function decisionPatch(got: Ad): Partial<Ad> {
  return {
    complexId: got.complexId ?? undefined, matchConfidence: got.matchConfidence,
    linkConfirmedBy: got.linkConfirmedBy ?? null, linkConfirmedAt: got.linkConfirmedAt ?? null, rejectedLinks: got.rejectedLinks ?? [],
    termsReview: got.termsReview ?? null, termsReviewBy: got.termsReviewBy ?? null, termsReviewAt: got.termsReviewAt ?? null,
  }
}
