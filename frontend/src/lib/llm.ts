// Анализ ИИ (Claude, этап «llm» анализа публикации на сервере): подписи и пояснения по-русски.
// Всё, что вернула модель, — гипотезы: привязку, статус и вывод о нарушении принимает аналитик.
// Не выполненный анализ (отказ модели, сбой, пропуск) — «анализ ИИ не выполнен», а не «риска нет».
// «Проанализировать ещё раз» — маршрут администратора: повторный запрос к Claude расходует дневной бюджет.
import type { Ad, LlmAmount, LlmAnalysis, LlmCandidate, LlmSignalType, Processing } from '../data/types'
import { adminCall } from './admin'
import { ApiError, apiPost, isRouteMissing } from './api'

export const SIGNAL_LABEL: Record<LlmSignalType, string> = {
  sale: 'продажа',
  presale: 'старт продаж',
  booking: 'бронь',
  installment: 'рассрочка',
  down_payment: 'первоначальный взнос',
  ddu: 'ДДУ',
  price: 'цена',
  discount: 'скидка',
  keys_handover: 'сдача ключей',
  other: 'другое',
}

export const CONF_LABEL: Record<LlmCandidate['confidence'], string> = { low: 'низкая', medium: 'средняя', high: 'высокая' }
export const CONF_TONE: Record<LlmCandidate['confidence'], 'gray' | 'yellow' | 'blue'> = { low: 'gray', medium: 'yellow', high: 'blue' }

/** Почему сервер отбросил элемент ответа модели */
export const DROP_LABEL: Record<string, string> = {
  quote: 'цитаты нет в тексте публикации и OCR',
  complex: 'ЖК не из справочника',
  amount: 'сумма — не число',
  limit: 'сверх предела',
}

export const signalLabel = (t: string) => (Object.hasOwn(SIGNAL_LABEL, t) ? SIGNAL_LABEL[t as LlmSignalType] : t)

const NOT_NO_RISK = 'Это не значит, что риска нет — проверьте вручную.'

export type LlmTone = 'gray' | 'blue' | 'yellow' | 'red'

/** Метка и пояснение к состоянию анализа ИИ. null — анализа не было (поле llm отсутствует) */
export function llmState(x: LlmAnalysis | null | undefined, fmt: (iso: string) => string = (s) => s):
  { tone: LlmTone; label: string; note: string | null } | null {
  if (!x) return null
  const err = x.error?.trim()
  if (x.status === 'done') return { tone: 'blue', label: 'гипотезы', note: null }
  if (x.status === 'refused') {
    return { tone: 'yellow', label: 'модель отказалась',
      note: `Анализ ИИ не выполнен: модель отказалась${x.refusalCategory ? ` (категория: ${x.refusalCategory})` : ''}. ${NOT_NO_RISK}` }
  }
  if (x.status === 'failed' && x.retryAt) {
    return { tone: 'gray', label: 'повтор', note: `Анализ ИИ пока не выполнен${err ? `: ${err}` : ''}. Повтор — ${fmt(x.retryAt)}.` }
  }
  if (x.status === 'failed') return { tone: 'yellow', label: 'не выполнен', note: `Анализ ИИ не выполнен${err ? `: ${err}` : ''}. ${NOT_NO_RISK}` }
  return { tone: 'gray', label: 'пропущен', note: `Анализ ИИ пропущен${err ? `: ${err}` : ''}. ${NOT_NO_RISK}` }
}

/** «Сервер отбросил 3 элемента ответа: цитаты нет в тексте публикации и OCR — 2, ЖК не из справочника — 1»; null — ничего не отброшено */
export function droppedText(d: Record<string, number> | null | undefined): string | null {
  const parts = Object.entries(d ?? {}).filter(([, n]) => typeof n === 'number' && n > 0)
  if (!parts.length) return null
  const total = parts.reduce((s, [, n]) => s + n, 0)
  const word = total % 10 === 1 && total % 100 !== 11 ? 'элемент' : [2, 3, 4].includes(total % 10) && ![12, 13, 14].includes(total % 100) ? 'элемента' : 'элементов'
  return `Сервер отбросил ${total} ${word} ответа: ${parts.map(([k, n]) => `${Object.hasOwn(DROP_LABEL, k) ? DROP_LABEL[k] : k} — ${n}`).join(', ')}`
}

/** «100 000 KZT» — сумма с валютой, как её назвала модель */
export function amountText(a: Pick<LlmAmount, 'value' | 'currency'>): string {
  const v = Number.isInteger(a.value) ? a.value.toLocaleString('ru-RU') : a.value.toLocaleString('ru-RU', { maximumFractionDigits: 2 })
  return `${v.replace(/ /g, ' ')} ${a.currency ?? ''}`.trim()
}

/** Оценка стоимости запроса по токенам ответа сервера: «≈ $0.0140» (4 знака — запросы дешевле цента) */
export const costText = (usd: number) => `≈ $${(Number.isFinite(usd) ? usd : 0).toFixed(4)}`

/** «claude-opus-5-5» или «claude-opus-5-5 → ответила claude-opus-4-8» (резервная модель после отказа) */
export function modelText(x: Pick<LlmAnalysis, 'model' | 'servedModel' | 'fallback'>): string {
  return x.fallback && x.servedModel && x.servedModel !== x.model ? `${x.model} → ответила ${x.servedModel}` : x.model
}

// ---------- «Проанализировать ещё раз» ----------

export const REANALYZE_HINT = 'Повторный запрос к Claude по тому же тексту и OCR: расходует дневной бюджет анализа ИИ, прежний результат сохраняется в истории'
export const NO_REANALYZE_ROUTE = 'На сервере нет повторного анализа ИИ — обновите бэкенд'

/** Почему кнопка «Проанализировать ещё раз» недоступна (подсказка у кнопки); null — доступна */
export function reanalyzeBlock(o: { live: boolean; admin: boolean; collected: boolean; processing?: Processing | null; author?: string | null }): string | null {
  if (!o.live) return 'Повторный анализ выполняется на сервере — в демо-режиме недоступен'
  if (!o.collected) return 'Повторный анализ доступен для собранных публикаций'
  if (o.processing?.status === 'queued' || o.processing?.status === 'running') return 'Анализ публикации уже в очереди или идёт — дождитесь результата'
  if (!o.admin) return 'Нужен режим администратора (страница «Источники»): повторный анализ расходует дневной бюджет анализа ИИ'
  if (!o.author?.trim()) return 'Войдите как сотрудник — имя попадёт в журнал'
  return null
}

/** POST /ads/{id}/reanalyze (администратор) → публикация с processing: 202 — задание поставлено, 200 — анализ уже идёт или
 *  повторный анализ уже в очереди. 409 (не собрана, бюджет исчерпан, ключ отклонён), 503 (не настроено) — текст сервера */
export function reanalyzeAd(adId: string, author: string): Promise<Ad> {
  return adminCall(async (h) => {
    try {
      return await apiPost<Ad>(`/ads/${encodeURIComponent(adId)}/reanalyze`, { author }, h)
    } catch (e) {
      // сервер старой версии: маршрута нет — не «режим администратора не поддерживается», а «обновите бэкенд»
      if (isRouteMissing(e)) throw new ApiError((e as ApiError).status, NO_REANALYZE_ROUTE, { route: 'missing' })
      throw e
    }
  })
}
