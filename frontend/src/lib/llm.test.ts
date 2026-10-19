import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LlmAnalysis } from '../data/types'
import { ADMIN_HEADER, NEED_ADMIN, NO_ADMIN_ROUTE, clearAdminToken, isAdmin, setAdminToken } from './admin'
import { ApiError } from './api'
import { amountText, costText, droppedText, llmState, modelText, NO_REANALYZE_ROUTE, reanalyzeAd, reanalyzeBlock, signalLabel } from './llm'

const base: LlmAnalysis = {
  status: 'done', provider: 'anthropic', model: 'claude-sonnet-5-5', promptVersion: 'saq-llm-1', contentVersion: 1,
  saleSignals: [], amounts: [], complexCandidates: [], uncertainties: [], dropped: {}, attempts: 1, fallback: false,
  inputTokens: 1000, outputTokens: 500, costUsd: 0.014,
}

describe('анализ ИИ (Claude)', () => {
  it('нет анализа — ничего не показываем', () => {
    expect(llmState(undefined)).toBeNull()
    expect(llmState(null)).toBeNull()
  })

  it('готово — гипотезы; отказ, сбой и пропуск — «не выполнен», а не «риска нет»', () => {
    expect(llmState(base)).toEqual({ tone: 'blue', label: 'гипотезы', note: null })
    const refused = llmState({ ...base, status: 'refused', refusalCategory: 'cyber' })
    expect(refused?.label).toBe('модель отказалась')
    expect(refused?.note).toBe('Анализ ИИ не выполнен: модель отказалась (категория: cyber). Это не значит, что риска нет — проверьте вручную.')
    expect(llmState({ ...base, status: 'failed', error: 'Ошибка сервера Anthropic (HTTP 503) (попыток: 3)' })?.note)
      .toBe('Анализ ИИ не выполнен: Ошибка сервера Anthropic (HTTP 503) (попыток: 3). Это не значит, что риска нет — проверьте вручную.')
    const retry = llmState({ ...base, status: 'failed', error: 'Ограничение частоты Anthropic (HTTP 429)', retryAt: '2026-10-18T10:00:00+00:00' }, () => '18 окт., 15:00')
    expect([retry?.tone, retry?.label, retry?.note]).toEqual(['gray', 'повтор', 'Анализ ИИ пока не выполнен: Ограничение частоты Anthropic (HTTP 429). Повтор — 18 окт., 15:00.'])
    expect(llmState({ ...base, status: 'skipped', error: 'дневной бюджет $5.00 исчерпан' })?.note).toBe('Анализ ИИ пропущен: дневной бюджет $5.00 исчерпан. Это не значит, что риска нет — проверьте вручную.')
    for (const status of ['refused', 'failed', 'skipped'] as const) expect(llmState({ ...base, status })?.note).toContain('не значит, что риска нет')
  })

  it('отброшенные сервером элементы, суммы, стоимость, модель', () => {
    expect(droppedText({})).toBeNull()
    expect(droppedText({ quote: 2, complex: 1 })).toBe('Сервер отбросил 3 элемента ответа: цитаты нет в тексте публикации и OCR — 2, ЖК не из справочника — 1')
    expect(droppedText({ amount: 1 })).toBe('Сервер отбросил 1 элемент ответа: сумма — не число — 1')
    expect(droppedText({ quote: 5, new_reason: 6 })).toBe('Сервер отбросил 11 элементов ответа: цитаты нет в тексте публикации и OCR — 5, new_reason — 6')
    expect(amountText({ value: 100000, currency: 'KZT' })).toBe('100 000 KZT')
    expect(amountText({ value: 12.5, currency: '' })).toBe('12,5')
    expect(costText(0.014)).toBe('≈ $0.0140')
    expect(modelText(base)).toBe('claude-sonnet-5-5')
    expect(modelText({ ...base, fallback: true, servedModel: 'claude-opus-4-8' })).toBe('claude-sonnet-5-5 → ответила claude-opus-4-8')
    expect(signalLabel('booking')).toBe('бронь')
    expect(signalLabel('constructor')).toBe('constructor')
  })
})

describe('«Проанализировать ещё раз»', () => {
  const ok = { live: true, admin: true, collected: true, processing: { status: 'done', version: 1 } as const, author: 'Омарова Ж.' }

  it('доступна администратору у собранной публикации, когда анализ не идёт', () => {
    expect(reanalyzeBlock(ok)).toBeNull()
    expect(reanalyzeBlock({ ...ok, processing: null })).toBeNull()
    expect(reanalyzeBlock({ ...ok, processing: { status: 'failed', version: 2 } })).toBeNull()
  })

  it('иначе — причина у кнопки', () => {
    expect(reanalyzeBlock({ ...ok, live: false })).toBe('Повторный анализ выполняется на сервере — в офлайн-копии недоступен')
    expect(reanalyzeBlock({ ...ok, collected: false })).toBe('Повторный анализ доступен для собранных публикаций')
    for (const status of ['queued', 'running'] as const) {
      expect(reanalyzeBlock({ ...ok, processing: { status, version: 1 } })).toBe('Анализ публикации уже в очереди или идёт — дождитесь результата')
    }
    expect(reanalyzeBlock({ ...ok, admin: false })).toBe('Нужен режим администратора (страница «Источники»): повторный анализ расходует дневной бюджет анализа ИИ')
    expect(reanalyzeBlock({ ...ok, author: '  ' })).toBe('Войдите как сотрудник — имя попадёт в журнал')
  })

  describe('запрос', () => {
    const TOKEN = 't-admin-0123456789abcdef0123'
    const reply = (status: number, body: unknown) =>
      vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
    beforeEach(() => clearAdminToken(null))
    afterEach(() => {
      clearAdminToken(null)
      vi.unstubAllGlobals()
    })

    it('без режима администратора — на сервер не уходит', async () => {
      const f = reply(202, {})
      vi.stubGlobal('fetch', f)
      await expect(reanalyzeAd('ad1', 'Омарова Ж.')).rejects.toMatchObject({ status: 401, message: NEED_ADMIN })
      expect(f).not.toHaveBeenCalled()
    })

    it('токен и автор уходят в POST /ads/{id}/reanalyze; ошибка сервера — его текстом, токен не забываем', async () => {
      setAdminToken(TOKEN)
      const f = reply(202, { id: 'ad 1', processing: { status: 'queued', version: 1 } })
      vi.stubGlobal('fetch', f)
      await expect(reanalyzeAd('ad 1', 'Омарова Ж.')).resolves.toMatchObject({ processing: { status: 'queued' } })
      const [url, init] = f.mock.calls[0]
      const headers = (init?.headers ?? {}) as Record<string, string>
      expect([url, init?.method, headers[ADMIN_HEADER], JSON.parse(String(init?.body))])
        .toEqual(['/api/ads/ad%201/reanalyze', 'POST', TOKEN, { author: 'Омарова Ж.' }])
      const over = 'Дневной бюджет анализа ИИ исчерпан: потрачено $4.90 из $5.00, запрос может стоить до $0.17 — повторный анализ возможен после полуночи по Усть-Каменогорску'
      vi.stubGlobal('fetch', reply(409, { detail: over }))
      await expect(reanalyzeAd('ad1', 'Омарова Ж.')).rejects.toMatchObject({ status: 409, message: over })
      expect(isAdmin()).toBe(true)
      vi.stubGlobal('fetch', reply(403, { detail: 'Неверный токен администратора' }))
      await expect(reanalyzeAd('ad1', 'Омарова Ж.')).rejects.toMatchObject({ status: 403, message: 'Неверный токен администратора' })
      expect(isAdmin()).toBe(false) // неверный токен забыт — кнопка снова просит режим администратора
    })

    it('маршрута нет на старом сервере — «обновите бэкенд», а не «нет режима администратора»', async () => {
      setAdminToken(TOKEN)
      vi.stubGlobal('fetch', reply(404, { detail: 'Not Found' }))
      const e: unknown = await reanalyzeAd('ad1', 'Омарова Ж.').then(() => null, (x: unknown) => x)
      expect(e).toBeInstanceOf(ApiError)
      const err = e as ApiError
      expect([err.status, err.message]).toEqual([404, NO_REANALYZE_ROUTE])
      expect(err.message).not.toBe(NO_ADMIN_ROUTE)
    })
  })
})
