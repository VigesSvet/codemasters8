// Проект запроса сведений по ЖК в ЖАО (МИО) / Единому оператору / ГАСК (T-78). Текст письма собирает сервер
// (backend/app/letters.py) — здесь адресаты, предпросмотр и скачивание PDF/DOCX. Черновик: автоматической отправки нет.
import { API, ApiError, apiPost, messageOf } from './api'


export type Addressee = 'mio' | 'operator' | 'gask'
export const ADDRESSEES: [Addressee, string, string][] = [
  ['mio', 'ЖАО (МИО) ВКО', 'разрешение на привлечение денег дольщиков (ст. 18)'],
  ['operator', 'Единый оператор жилищного строительства', 'АО «Казахстанская жилищная компания» — договор гарантии (ст. 8)'],
  ['gask', 'ГАСК ВКО', 'перечень строящихся объектов, разрешительная документация'],
]
export type LetterBlock =
  | { type: 'note' | 'meta' | 'subject' | 'h' | 'p' | 'bullet' | 'small'; text: string }
  | { type: 'li'; n: number; text: string }
  | { type: 'addr' | 'sign'; lines: string[] }
export interface Letter {
  complexId: string
  complexName: string
  addressee: Addressee
  addresseeName: string
  subject: string
  date: string
  number: string
  topics: string[]
  lawRefs: string[]
  attachments: string[]
  signature: string
  signalCode: string | null
  ads: number
  note: string
  blocks: LetterBlock[]
  text: string
  fileBase: string
}
export interface LetterIn { addressee: Addressee; author: string; topics: string[]; comment?: string }

export const previewLetter = (cid: string, body: LetterIn) =>
  apiPost<Letter>(`/complexes/${encodeURIComponent(cid)}/request-letter/preview`, { ...body, comment: body.comment?.trim() || null })

/** Файл проекта запроса (PDF/DOCX): сервер пишет «Сформирован проект запроса в …» в журнал объекта */
export async function letterFile(cid: string, body: LetterIn, format: 'pdf' | 'docx'): Promise<{ blob: Blob; name: string }> {
  let r: Response
  try {
    r = await fetch(`${API}/complexes/${encodeURIComponent(cid)}/request-letter?format=${format}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body, comment: body.comment?.trim() || null }),
    })
  } catch {
    throw new ApiError(0, 'Сервер недоступен')
  }
  if (!r.ok) {
    let data: unknown = null
    try { data = await r.json() } catch { /* не JSON */ }
    throw new ApiError(r.status, messageOf(data, `Ошибка ${r.status}`), data)
  }
  const m = /filename="([^"]+)"/.exec(r.headers.get('Content-Disposition') ?? '')
  return { blob: await r.blob(), name: m?.[1] ?? `request-${cid}.${format}` }
}

/** Сохранить файл в браузере (ссылка с download по blob-URL) */
export function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
