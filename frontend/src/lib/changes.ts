// «Удалено в источнике» и история изменений собранных публикаций: пословный diff версий (LCS, без зависимостей),
// статус в источнике (GET /api/ads/{id}/source-status) и лента изменений (GET /api/changes) — app/changes.py.
import { apiGet, orNull } from './api'

export type DiffOp = 'eq' | 'del' | 'add'
export type DiffPart = [DiffOp, string]

/** Слова и пробелы по порядку: склеенные обратно дают исходный текст */
export const tokens = (s: string | null | undefined) => (s ?? '').match(/\S+|\s+/g) ?? []

/** Предел таблицы LCS: длиннее — изменившаяся середина целиком «удалено → добавлено» */
export const DIFF_CELLS = 1_000_000

/** Пословный diff двух версий: [(op, текст)], соседние куски одного вида склеены, разница только в пробелах — eq.
 *  Общие начало и конец отрезаются до LCS (как diff_words в app/changes.py) */
export function diffWords(a: string | null | undefined, b: string | null | undefined, cells = DIFF_CELLS): DiffPart[] {
  const ta = tokens(a), tb = tokens(b)
  const key = (t: string) => (/^\s+$/.test(t) ? ' ' : t)
  const ka = ta.map(key), kb = tb.map(key)
  let i = 0
  while (i < ka.length && i < kb.length && ka[i] === kb[i]) i++
  let j = 0
  while (j < ka.length - i && j < kb.length - i && ka[ka.length - 1 - j] === kb[kb.length - 1 - j]) j++
  const ma = ka.slice(i, ka.length - j), mb = kb.slice(i, kb.length - j)
  const ops: DiffPart[] = tb.slice(0, i).map((t) => ['eq', t])
  const n = ma.length, m = mb.length
  if (n * m > cells) {
    ops.push(...ta.slice(i, ta.length - j).map((t): DiffPart => ['del', t]), ...tb.slice(i, tb.length - j).map((t): DiffPart => ['add', t]))
  } else {
    // L[x][y] — длина LCS хвостов ma[x:] и mb[y:]
    const L = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1))
    for (let x = n - 1; x >= 0; x--) for (let y = m - 1; y >= 0; y--) L[x][y] = ma[x] === mb[y] ? L[x + 1][y + 1] + 1 : Math.max(L[x + 1][y], L[x][y + 1])
    let x = 0, y = 0
    while (x < n && y < m) {
      if (ma[x] === mb[y]) { ops.push(['eq', tb[i + y]]); x++; y++ }
      else if (L[x + 1][y] >= L[x][y + 1]) { ops.push(['del', ta[i + x]]); x++ }
      else { ops.push(['add', tb[i + y]]); y++ }
    }
    ops.push(...ta.slice(i + x, ta.length - j).map((t): DiffPart => ['del', t]), ...tb.slice(i + y, tb.length - j).map((t): DiffPart => ['add', t]))
  }
  ops.push(...tb.slice(tb.length - j).map((t): DiffPart => ['eq', t]))
  // склеить соседние куски; пробел между двумя удалёнными (добавленными) словами — к ним же
  const out: DiffPart[] = []
  ops.forEach(([op, t], k) => {
    const last = out[out.length - 1]
    const glue = op === 'eq' && /^\s+$/.test(t) && last && last[0] !== 'eq' && ops[k + 1]?.[0] === last[0]
    const o = glue ? last[0] : op
    if (last && last[0] === o) last[1] += t
    else out.push([o, t])
  })
  return out
}

/** Есть ли в diff изменения (не только совпадающие куски) */
export const changed = (d: DiffPart[]) => d.some(([op]) => op !== 'eq')

export type SourceState = 'present' | 'deleted' | 'unknown'
export interface SourceStatus {
  status: SourceState
  how: string
  checkedAt?: string | null
  lastSeenAt?: string | null
  /** Когда получена последняя сохранённая версия — копия в БД */
  savedAt?: string | null
  missedRuns: number
  collector?: string | null
}

export interface ChangeItem {
  adId: string; platform: string; account: string; url: string; complexId?: string | null; text: string
  fromVersion: number; toVersion: number; at?: string | null
  priceFrom?: string | null; priceTo?: string | null
  removed: string[]; added: string[]; termsRemoved: string[]
  /** Правки с контекстом: до 4 совпадающих слов с каждой стороны */
  hunks?: { before: string; removed: string; added: string; after: string }[]
  kinds: ('price' | 'text_removed' | 'text_added' | 'terms_removed' | 'media')[]
}
export type DeletedItem = Omit<ChangeItem, 'fromVersion' | 'toVersion' | 'at' | 'priceFrom' | 'priceTo' | 'removed' | 'added' | 'termsRemoved' | 'kinds' | 'hunks'> & SourceStatus
export interface ChangesOut {
  generatedAt: string; days: number; changes: ChangeItem[]; deleted: DeletedItem[]
  sourceStatus: Record<SourceState, number>; items: number; limits: string
}

export const KIND_LABEL: Record<ChangeItem['kinds'][number], string> = {
  price: 'цена', text_removed: 'текст убран', text_added: 'текст добавлен', terms_removed: 'условия оплаты убраны', media: 'файлы',
}

const statusMemo = new Map<string, Promise<SourceStatus | null>>()
/** Статус публикации в источнике; null — недоступно (офлайн-копия, сервер старой версии). Один запрос на публикацию */
export function getSourceStatus(adId: string): Promise<SourceStatus | null> {
  let p = statusMemo.get(adId)
  if (!p) {
    p = orNull(apiGet<SourceStatus>(`/ads/${encodeURIComponent(adId)}/source-status`), (x) => !!x && typeof x === 'object' && 'status' in x)
    statusMemo.set(adId, p)
    p.catch(() => statusMemo.delete(adId))
  }
  return p
}

export const getChanges = (days = 30) => orNull(apiGet<ChangesOut>(`/changes?days=${days}`), (x) => !!x && typeof x === 'object' && Array.isArray((x as ChangesOut).changes))

/** «ДД.ММ» — дата сохранения копии в бейдже */
export const ddmm = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) : '')
