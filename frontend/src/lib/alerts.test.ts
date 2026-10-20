import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ADMIN_HEADER, NEED_ADMIN, clearAdminToken, setAdminToken } from './admin'
import { ApiError } from './api'
import {
  STATUS_TONE, approveAlert, approveProblem, audienceLine, audienceProblems, canRetry, checkBot, createAlert, deliveryLine, footer,
  getAlerts, getAudience, hashtag, rejectAlert, type Audience, type ResidentAlert,
} from './alerts'

const TOKEN = 't-admin-0123456789abcdef0123'
const reply = (status: number, body: unknown) =>
  vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
const sent = (f: ReturnType<typeof reply>, i = 0) => {
  const [url, init] = f.mock.calls[i]
  return { url, method: init?.method ?? 'GET', headers: (init?.headers ?? {}) as Record<string, string>, body: init?.body ? JSON.parse(String(init.body)) : undefined }
}

const alert = (x: Partial<ResidentAlert> = {}): ResidentAlert => ({
  id: 1, code: 'A-0001', complexId: 'c1', complexName: 'Ертіс Парк', district: 'Ульбинский', template: 'basis', title: 'Проверьте основание',
  text: 'Сверьте номер гарантии', status: 'pending', statusLabel: 'На согласовании', createdBy: 'Аналитик ДЭР', createdAt: null, decidedBy: null,
  decidedAt: null, decisionComment: null, sentAt: null, lastError: null, channel: null,
  subscribers: { total: 0, sent: 0, failed: 0, pending: 0, skipped: 0 }, errors: [], ...x,
})
const aud = (x: Partial<Audience> = {}): Audience => ({
  configured: true, adminConfigured: true, workerAlive: true, problem: null, channel: '@gask_vko', channelProblem: null, district: 'Ульбинский',
  subscribers: 3, everywhere: 1, districts: [], ...x,
})

function memory() {
  const m = new Map<string, string>()
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v) }, removeItem: (k: string) => { m.delete(k) } }
}

beforeEach(() => {
  vi.stubGlobal('sessionStorage', memory())
  clearAdminToken(null)
})
afterEach(() => {
  clearAdminToken(null)
  vi.unstubAllGlobals()
})

describe('сообщение: хэштег района и подпись — как alerts.render на сервере', () => {
  it('хэштег: не буквы и цифры → «_», без района — пусто', () => {
    expect(hashtag('Ульбинский')).toBe('#Ульбинский')
    expect(hashtag('Северо-запад')).toBe('#Северо_запад')
    expect(hashtag(' мкр. 4 ')).toBe('#мкр_4')
    expect(hashtag('')).toBe('')
    expect(hashtag(null)).toBe('')
  })
  it('подпись: хэштег и «ГАСК ВКО · SAQ QURYLYS»', () => {
    expect(footer('Центр')).toBe('#Центр · ГАСК ВКО · SAQ QURYLYS')
    expect(footer('')).toBe('ГАСК ВКО · SAQ QURYLYS')
  })
  it('цвет есть у каждого статуса', () => {
    expect(Object.keys(STATUS_TONE).sort()).toEqual(['failed', 'partial', 'pending', 'rejected', 'sending', 'sent'])
  })
})

describe('кому уйдёт и что мешает', () => {
  it('канал и подписчики района; без района — «всех районов»; некому — null', () => {
    expect(audienceLine(aud())).toBe('канал @gask_vko и 3 подписчика района «Ульбинский» в боте')
    expect(audienceLine(aud({ channel: null, subscribers: 1, district: '' }))).toBe('1 подписчик всех районов в боте')
    expect(audienceLine(aud({ subscribers: 0 }))).toBe('канал @gask_vko')
    expect(audienceLine(aud({ channel: null, subscribers: 0 }))).toBeNull()
  })
  it('нет токена бота, неверный канал, некому, нет администрирования, воркер не запущен', () => {
    expect(audienceProblems(null)).toEqual([])
    expect(audienceProblems(aud())).toEqual([])
    const p = audienceProblems(aud({ configured: false, problem: 'Отправка не настроена: задайте BOT_TOKEN', channel: null,
      channelProblem: 'TG_ALERTS_CHANNEL: нужен @имя', subscribers: 0, adminConfigured: false, workerAlive: false }))
    expect(p).toEqual(['Отправка не настроена: задайте BOT_TOKEN', 'TG_ALERTS_CHANNEL: нужен @имя',
      'Некому отправить: канал ГАСК не задан, подписчиков района «Ульбинский» в боте нет',
      'Согласовать некому: режим администратора не настроен на сервере (SAQ_ADMIN_TOKEN)'])
    expect(audienceProblems(aud({ workerAlive: false }))).toEqual(['Воркер не запущен — согласованное предупреждение уйдёт, когда он запустится'])
  })
})

describe('итог и действия', () => {
  it('сводка доставки — как в журнале сервера', () => {
    const a = alert({ channel: { target: '@gask_vko', status: 'sent', error: null, sentAt: null, link: null },
      subscribers: { total: 14, sent: 11, failed: 1, pending: 1, skipped: 1 } })
    expect(deliveryLine(a)).toBe('канал @gask_vko — опубликовано · подписчики: доставлено 11 из 13, не доставлено 1, ожидают 1, отписались 1')
    expect(deliveryLine(alert())).toBe('')
  })
  it('согласует не автор и только в режиме администратора', () => {
    expect(approveProblem(alert(), 'Ведущий аналитик ДЭР', true)).toBeNull()
    expect(approveProblem(alert(), ' аналитик дэр ', true)).toMatch(/другой сотрудник/)
    expect(approveProblem(alert(), 'Ведущий аналитик ДЭР', false)).toMatch(/режиме администратора/)
    expect(approveProblem(alert(), null, true)).toMatch(/Войдите/)
    expect(approveProblem(alert({ status: 'sent' }), 'Ведущий аналитик ДЭР', true)).toMatch(/уже не на согласовании/)
  })
  it('«Повторить отправку» — только после ошибки и пока есть доставки «ожидает»', () => {
    const pending = { total: 2, sent: 1, failed: 0, pending: 1, skipped: 0 }
    expect(canRetry(alert({ status: 'partial', subscribers: pending }))).toBe(true)
    expect(canRetry(alert({ status: 'failed', channel: { target: '@x', status: 'pending', error: null, sentAt: null, link: null } }))).toBe(true)
    expect(canRetry(alert({ status: 'sending', subscribers: pending }))).toBe(false)
    expect(canRetry(alert({ status: 'partial', subscribers: { ...pending, pending: 0, failed: 1 } }))).toBe(false)
  })
})

describe('запросы', () => {
  it('список и получатели: чтение без токена; офлайн или старый сервер — null', async () => {
    const f = reply(200, [alert()])
    vi.stubGlobal('fetch', f)
    expect(await getAlerts('c 1')).toHaveLength(1)
    expect(sent(f).url).toBe('/api/alerts?complexId=c%201')
    expect(sent(f).headers[ADMIN_HEADER]).toBeUndefined()
    vi.stubGlobal('fetch', reply(404, { detail: 'Not Found' }))
    expect(await getAlerts()).toBeNull()
    const g = reply(200, aud())
    vi.stubGlobal('fetch', g)
    expect((await getAudience('Северо-запад'))?.channel).toBe('@gask_vko')
    expect(sent(g).url).toBe('/api/alerts/audience?district=%D0%A1%D0%B5%D0%B2%D0%B5%D1%80%D0%BE-%D0%B7%D0%B0%D0%BF%D0%B0%D0%B4')
  })
  it('на согласование — автор в теле, без токена администратора', async () => {
    const f = reply(201, alert())
    vi.stubGlobal('fetch', f)
    await createAlert({ complexId: 'c1', template: 'basis', title: 'T', text: 'x', author: 'Аналитик ДЭР' })
    expect(sent(f)).toMatchObject({ url: '/api/alerts', method: 'POST', body: { complexId: 'c1', author: 'Аналитик ДЭР' } })
    expect(sent(f).headers[ADMIN_HEADER]).toBeUndefined()
  })
  it('согласовать, отклонить, проверить бота — только с токеном администратора', async () => {
    const f = reply(200, alert({ status: 'sending' }))
    vi.stubGlobal('fetch', f)
    await expect(approveAlert(1, 'Ведущий аналитик ДЭР')).rejects.toThrow(NEED_ADMIN)
    expect(f).not.toHaveBeenCalled()
    setAdminToken(TOKEN)
    await approveAlert(1, 'Ведущий аналитик ДЭР', '  ')
    expect(sent(f)).toMatchObject({ url: '/api/alerts/1/approve', method: 'POST', body: { author: 'Ведущий аналитик ДЭР' } })
    expect(sent(f).headers[ADMIN_HEADER]).toBe(TOKEN)
    await rejectAlert(1, 'Ведущий аналитик ДЭР', 'Не тот ЖК')
    expect(sent(f, 1)).toMatchObject({ url: '/api/alerts/1/reject', body: { comment: 'Не тот ЖК' } })
    await checkBot()
    expect(sent(f, 2).url).toBe('/api/alerts/check')
  })
  it('отказ сервера — его текст (автор не согласует сам)', async () => {
    setAdminToken(TOKEN)
    vi.stubGlobal('fetch', reply(409, { detail: 'Согласует другой сотрудник: автор предупреждения не может согласовать его сам' }))
    const e = await approveAlert(1, 'Аналитик ДЭР').catch((x: unknown) => x)
    expect(e).toBeInstanceOf(ApiError)
    expect((e as ApiError).message).toMatch(/другой сотрудник/)
  })
})
