// Сверка сведений аналитиком в карточке ЖК (сервер: app.verification): основание по документам, перечень ГАСК,
// готовность, обращения и «обращений нет — сверено», снимки площадки. Чистые помощники: что уже сверено, подсказка
// «Сверено 1 из 4», варианты источника, проверка формы по-русски и тело запроса. Отправку делает lib/store.tsx.
import type { Appeal, BasisDoc, BasisSuggestion, Complex, ReadinessValue, Tri } from '../data/types'
import { docSource } from './risk'

/** Что сверяется в блоке «Основание привлечения средств»: три документа и перечень ГАСК */
export type VerifyTarget = BasisDoc | 'list'
export const TARGETS: { key: VerifyTarget; title: string; short: string }[] = [
  { key: 'guarantee', title: 'Гарантия ЕО (КЖК)', short: 'гарантия ЕО' },
  { key: 'mio', title: 'Разрешение МИО', short: 'разрешение МИО' },
  { key: 'bvu', title: 'Банк второго уровня', short: 'банк' },
  { key: 'list', title: 'Перечень ГАСК', short: 'перечень ГАСК' },
]
export const targetTitle = (t: VerifyTarget) => TARGETS.find((x) => x.key === t)?.title ?? t
/** Якорь плитки в карточке — к нему ведёт «Сверить первое несверенное» */
export const tileId = (t: VerifyTarget) => `verify-${t}`

export const OTHER = 'Другое'
/** Где проверено: варианты источника по виду сведений; «Другое» — свой текст */
export const SOURCES = {
  basis: ['Homeportal', 'Казреестр (Ashyq)', 'Ответ уполномоченного органа', 'ЕГКН', OTHER],
  list: ['Перечень ГАСК (Instagram)', 'Сайт ГАСК ВКО', 'Ответ ГАСК', OTHER],
  declared: ['Сайт застройщика', 'Реклама застройщика', 'Homeportal', OTHER],
  confirmed: ['Акт осмотра', 'Выезд на площадку', 'Фото-мониторинг (дрон)', OTHER],
  appeals: ['ДЭР ВКО', 'ГАСК ВКО', 'eOtinish', OTHER],
  photo: ['Выезд на площадку', 'Дрон', 'Камера', OTHER],
} as const
export type SourceKind = keyof typeof SOURCES

/** «Сегодня» по Усть-Каменогорску (UTC+5) — как today_local() на сервере: дата сверки по умолчанию и её предел */
export const todayLocal = (now = Date.now()) => new Date(now + 5 * 3600e3).toISOString().slice(0, 10)
/** День по Усть-Каменогорску: дата как есть; время с поясом (журнал хранит UTC) — переводится в UTC+5 */
export function localDay(iso?: string | null): string {
  if (!iso || iso.length <= 10) return iso ?? ''
  const t = Date.parse(iso)
  return Number.isNaN(t) ? iso.slice(0, 10) : new Date(t + 5 * 3600e3).toISOString().slice(0, 10)
}
/** '2026-10-19' → '19.10.2026'; время с поясом — день по Усть-Каменогорску */
export const dmy = (iso?: string | null) => {
  const d = localDay(iso)
  return d.length >= 10 ? `${d.slice(8, 10)}.${d.slice(5, 7)}.${d.slice(0, 4)}` : ''
}

// ---------- что сверено ----------

/** Документ сверен: «есть» или «нет» с источником и датой сверки (своей или общей — как в модели риска) */
export function docChecked(c: Pick<Complex, 'basis'>, doc: BasisDoc): boolean {
  const p = docSource(c.basis ?? {}, doc)
  return !!p.source && !!p.checkedAt
}

/** Перечень ГАСК сверен: «есть»/«нет» и сверка аналитика с источником; у демо-ЖК старой БД — сведения демо-перечня */
export function listChecked(c: Pick<Complex, 'inPublishedList' | 'publishedListCheck' | 'origin'>): boolean {
  if (c.inPublishedList !== 'yes' && c.inPublishedList !== 'no') return false
  if (c.publishedListCheck?.source) return true
  return (c.origin ?? 'demo') === 'demo'
}

export const isChecked = (c: Complex, t: VerifyTarget) => (t === 'list' ? listChecked(c) : docChecked(c, t))

export interface Progress { checked: number; total: number; first: VerifyTarget | null }
/** Сколько из четырёх оснований сверено и какое первое — нет */
export function progress(c: Complex): Progress {
  const open = TARGETS.filter((t) => !isChecked(c, t.key)).map((t) => t.key)
  return { checked: TARGETS.length - open.length, total: TARGETS.length, first: open[0] ?? null }
}

/** «Сверено 1 из 4 оснований — сверьте остальные, чтобы рассчитать риск»; всё сверено — null (подсказки нет) */
export function progressText(p: Progress, assessed: boolean): string | null {
  if (!p.first) return null
  return `Сверено ${p.checked} из ${p.total} оснований — сверьте остальные, чтобы ${assessed ? 'уточнить' : 'рассчитать'} риск`
}

/** Подсказка реестра у несверенного документа: «Подтвердить — совпадает с источником» */
export function suggestionFor(c: Complex, t: VerifyTarget): BasisSuggestion | null {
  if (t === 'list' || isChecked(c, t)) return null
  return c.basisSuggest?.[t] ?? null
}

/** Кто, когда и где сверил: «Сверено: Омарова Ж., 19.10.2026 · Homeportal»; не сверено аналитиком — null */
export function checkedLine(rec?: { checkedBy?: string | null; checkedAt?: string | null; source?: string | null } | null): string | null {
  if (!rec?.checkedBy || !rec.source) return null
  return `Сверено: ${rec.checkedBy}${rec.checkedAt ? `, ${dmy(rec.checkedAt)}` : ''} · ${rec.source}`
}

/** Сверка документа или перечня: запись с автором (только у сверки аналитиком на сервере) */
export function checkOf(c: Complex, t: VerifyTarget): { source: string; url?: string | null; checkedAt?: string | null; checkedBy?: string | null; comment?: string | null } | null {
  if (t === 'list') return c.publishedListCheck ?? null
  return c.basis.sources?.[t] ?? null
}

/** Кто внёс значение готовности: «Внёс: Омарова Ж., 19.10.2026»; без автора (прежний демо-набор) — null */
export const enteredLine = (v?: ReadinessValue | null) => (v?.by ? `Внёс: ${v.by}${v.at ? `, ${dmy(v.at)}` : ''}` : null)

/** Кто внёс и кто решил по обращению */
export function appealLines(a: Appeal): string[] {
  const out: string[] = []
  if (a.createdBy) out.push(`Внёс: ${a.createdBy}${a.createdAt ? `, ${dmy(a.createdAt)}` : ''}`)
  if (a.decidedBy && a.status !== 'pending') {
    out.push(`${a.status === 'verified' ? 'Подтвердил' : 'Отклонил'}: ${a.decidedBy}${a.verifiedAt ? `, ${dmy(a.verifiedAt)}` : ''}${a.decisionComment ? ` — ${a.decisionComment}` : ''}`)
  }
  return out
}

// ---------- форма сверки ----------

export interface CheckForm {
  /** '' — ещё не выбрано: «есть», «нет» или «нет сведений» аналитик отмечает сам */
  value: Tri | ''
  ref: string
  choice: string
  other: string
  url: string
  date: string
  comment: string
}
export type FormErrors = Partial<Record<'value' | 'ref' | 'source' | 'url' | 'date' | 'comment' | 'file' | 'subject' | 'applicants' | 'activity', string>>

/** Источник из записи → вариант списка или «Другое» со своим текстом */
export function sourceChoice(source: string | null | undefined, kind: SourceKind): { choice: string; other: string } {
  const s = (source ?? '').trim()
  if (!s) return { choice: '', other: '' }
  const hit = (SOURCES[kind] as readonly string[]).find((o) => o !== OTHER && o.toLowerCase() === s.toLowerCase())
  return hit ? { choice: hit, other: '' } : { choice: OTHER, other: s }
}
/** Итоговый источник формы: вариант или свой текст при «Другое» */
export const sourceOf = (f: Pick<CheckForm, 'choice' | 'other'>) => (f.choice === OTHER ? f.other.trim() : f.choice.trim())

/** Поле номера документа у основания */
const REF_FIELD = { guarantee: 'guaranteeRef', mio: 'mioRef', bvu: 'bank' } as const

/** Форма «Сверить»: уже сверенное — как сверено; иначе номер, источник и ссылка — из подсказки реестра (если есть),
 *  «есть / нет / нет сведений» аналитик отмечает сам; дата сверки — сегодня */
export function initialForm(c: Complex, t: VerifyTarget, today = todayLocal()): CheckForm {
  const sg = t === 'list' ? null : c.basisSuggest?.[t] ?? null
  const checked = isChecked(c, t)
  const rec = checked ? checkOf(c, t) : null
  const value: Tri = t === 'list' ? c.inPublishedList : c.basis[t]
  const ref = t === 'list' ? '' : checked ? (c.basis.sources?.[t]?.ref ?? c.basis[REF_FIELD[t]] ?? '') : (sg?.ref ?? '')
  return {
    value: checked ? value : '',
    ref,
    ...sourceChoice(rec?.source ?? (checked && t !== 'list' ? c.basis.source : null) ?? sg?.source, t === 'list' ? 'list' : 'basis'),
    url: rec?.url ?? sg?.url ?? '',
    date: today,
    comment: '',
  }
}

const HTTP = /^https?:\/\/\S+$/i
const DAY = /^\d{4}-\d{2}-\d{2}$/
const validDay = (d: string) => DAY.test(d) && !Number.isNaN(new Date(`${d}T00:00:00Z`).getTime()) && new Date(`${d}T00:00:00Z`).toISOString().slice(0, 10) === d

/** Дата: формат, не позже сегодня, не раньше 2000 года — как на сервере */
export function dateProblem(d: string, today = todayLocal(), required = true): string | undefined {
  const s = d.trim()
  if (!s) return required ? 'Укажите дату' : undefined
  if (!validDay(s)) return 'Дата в формате ГГГГ-ММ-ДД'
  if (s > today) return 'Дата не может быть позже сегодняшней'
  if (s < '2000-01-01') return 'Дата раньше 2000 года — проверьте год'
  return undefined
}

/** Проверка формы сверки по-русски; пустой объект — можно записывать */
export function validateCheck(f: CheckForm, today = todayLocal()): FormErrors {
  const e: FormErrors = {}
  if (!f.value) e.value = 'Отметьте: есть, нет или нет сведений'
  if (f.value === 'yes' || f.value === 'no') {
    if (!f.choice) e.source = 'Выберите, где проверено: без источника «есть» или «нет» не считается сверкой'
    else if (f.choice === OTHER && !f.other.trim()) e.source = 'Напишите, где проверено'
  }
  if (sourceOf(f).length > 255) e.source = 'Не длиннее 255 символов'
  if (f.url.trim() && !HTTP.test(f.url.trim())) e.url = 'Ссылка должна начинаться с http:// или https://'
  if (f.url.trim().length > 512) e.url = 'Ссылка не длиннее 512 символов'
  const d = dateProblem(f.date, today, false)
  if (d) e.date = d
  if (f.ref.trim().length > 500) e.ref = 'Не длиннее 500 символов'
  if (f.comment.trim().length > 2000) e.comment = 'Комментарий не длиннее 2000 символов'
  return e
}

const opt = (s: string) => (s.trim() ? s.trim() : undefined)

/** Тело POST /api/complexes/{id}/basis или /published-list */
export function checkBody(t: VerifyTarget, f: CheckForm, author: string) {
  const known = f.value === 'yes' || f.value === 'no'
  return {
    author,
    ...(t === 'list' ? {} : { doc: t, ref: opt(f.ref) }),
    value: f.value || 'unknown',
    source: known ? sourceOf(f) : opt(sourceOf(f)),
    sourceUrl: opt(f.url),
    checkedAt: opt(f.date),
    comment: opt(f.comment),
  }
}

/** «Подтвердить — совпадает с источником»: «есть» с номером, источником и ссылкой реестра, сверено сегодня */
export function confirmBody(doc: BasisDoc, sg: BasisSuggestion, author: string, today = todayLocal()) {
  return { author, doc, value: 'yes' as const, ref: sg.ref, source: sg.source, sourceUrl: sg.url ?? undefined, checkedAt: today }
}

/** Что будет записано при подтверждении в один клик — строки для окна подтверждения */
export function confirmLines(doc: BasisDoc, sg: BasisSuggestion, author: string, today = todayLocal()): [string, string][] {
  return [
    ['Документ', `${targetTitle(doc)} — есть`],
    ['Номер и дата', sg.ref],
    ['Источник', sg.source],
    ...(sg.url ? [['Ссылка', sg.url] as [string, string]] : []),
    ['Дата сверки', dmy(today)],
    ['Сверил', author],
  ]
}

// ---------- готовность, обращения, снимки ----------

export interface ReadinessForm { value: string; choice: string; other: string; date: string; comment: string }

export function validateReadiness(f: ReadinessForm, today = todayLocal()): FormErrors {
  const e: FormErrors = {}
  const v = f.value.trim().replace(',', '.')
  if (!v) e.value = 'Укажите готовность, %'
  else if (!/^\d+(\.\d+)?$/.test(v) || Number(v) > 100) e.value = 'Число от 0 до 100'
  if (!f.choice) e.source = 'Выберите источник'
  else if (f.choice === OTHER && !f.other.trim()) e.source = 'Напишите источник'
  const d = dateProblem(f.date, today)
  if (d) e.date = d
  if (f.comment.trim().length > 2000) e.comment = 'Комментарий не длиннее 2000 символов'
  return e
}

export const readinessBody = (kind: 'declared' | 'confirmed', f: ReadinessForm, author: string) =>
  ({ author, kind, value: Number(f.value.trim().replace(',', '.')), source: sourceOf(f), date: f.date.trim(), comment: opt(f.comment) })

export interface AppealForm { date: string; subject: string; applicants: string; collective: boolean; choice: string; other: string; comment: string }

export function validateAppeal(f: AppealForm, today = todayLocal()): FormErrors {
  const e: FormErrors = {}
  const d = dateProblem(f.date, today)
  if (d) e.date = d
  if (!f.subject.trim()) e.subject = 'Опишите суть обращения'
  else if (f.subject.trim().length > 2000) e.subject = 'Не длиннее 2000 символов'
  const n = Number(f.applicants)
  if (!/^\d+$/.test(f.applicants.trim()) || n < 1) e.applicants = 'Целое число от 1'
  else if (n > 100000) e.applicants = 'Не больше 100 000'
  else if (f.collective && n < 2) e.applicants = 'Коллективное обращение — от двух заявителей'
  if (!f.choice) e.source = 'Выберите, откуда обращение'
  else if (f.choice === OTHER && !f.other.trim()) e.source = 'Напишите источник'
  if (f.comment.trim().length > 2000) e.comment = 'Комментарий не длиннее 2000 символов'
  return e
}

export const appealBody = (f: AppealForm, author: string) => ({
  author, date: f.date.trim(), subject: f.subject.trim(), applicants: Number(f.applicants), collective: f.collective,
  source: sourceOf(f), comment: opt(f.comment),
})

export interface AppealsCheckForm { choice: string; other: string; date: string; comment: string }

export function validateAppealsCheck(f: AppealsCheckForm, today = todayLocal()): FormErrors {
  const e: FormErrors = {}
  if (!f.choice) e.source = 'Выберите, где проверено'
  else if (f.choice === OTHER && !f.other.trim()) e.source = 'Напишите, где проверено'
  const d = dateProblem(f.date, today, false)
  if (d) e.date = d
  return e
}

export const appealsCheckBody = (f: AppealsCheckForm, author: string) =>
  ({ author, source: sourceOf(f), checkedAt: opt(f.date), comment: opt(f.comment) })

export type Activity = 'active' | 'idle' | 'unknown'
export const ACTIVITIES: [Activity, string][] = [['active', 'Идут работы'], ['idle', 'Работ нет'], ['unknown', 'Не видно / нет сведений']]

export interface PhotoForm { file: File | null; date: string; choice: string; other: string; activity: Activity | ''; note: string }

/** Предел снимка — как у скриншота публикации (POST /api/media) */
export const PHOTO_MAX = 10 * 1024 * 1024

export function validatePhoto(f: PhotoForm, today = todayLocal()): FormErrors {
  const e: FormErrors = {}
  if (!f.file) e.file = 'Выберите снимок'
  else if (f.file.type && !f.file.type.startsWith('image/')) e.file = 'Нужно изображение: JPEG, PNG, WEBP или GIF'
  else if (f.file.size > PHOTO_MAX) e.file = 'Файл больше 10 МБ'
  const d = dateProblem(f.date, today)
  if (d) e.date = d
  if (!f.choice) e.source = 'Выберите, откуда снимок'
  else if (f.choice === OTHER && !f.other.trim()) e.source = 'Напишите источник'
  if (!f.activity) e.activity = 'Отметьте, идут ли работы'
  if (f.note.trim().length > 2000) e.comment = 'Примечание не длиннее 2000 символов'
  return e
}

/** Поля multipart-формы снимка (файл добавляет store) */
export const photoFields = (f: PhotoForm, author: string): Record<string, string> => ({
  author, date: f.date.trim(), source: sourceOf(f), activity: f.activity || 'unknown', ...(f.note.trim() ? { note: f.note.trim() } : {}),
})

// ---------- окна сверки в карточке ЖК (components/Verification.tsx) ----------

type Busy = { busy?: boolean; err?: string; errors?: FormErrors }
/** Открытое окно сверки и его состояние: запись идёт (busy), ошибка сервера (err), ошибки полей (errors) */
export type Dlg = Busy & (
  | { kind: 'check'; target: VerifyTarget; form: CheckForm }
  | { kind: 'confirm'; doc: BasisDoc; sg: BasisSuggestion }
  | { kind: 'readiness'; which: 'declared' | 'confirmed'; form: ReadinessForm }
  | { kind: 'appeal'; form: AppealForm }
  | { kind: 'decide'; appeal: Appeal; action: 'verify' | 'reject'; comment: string }
  | { kind: 'appealsCheck'; form: AppealsCheckForm }
  | { kind: 'photo'; form: PhotoForm }
)

/** Новые формы — с сегодняшней датой; готовность — с текущим значением и источником */
export const newReadiness = (c: Complex, which: 'declared' | 'confirmed', today = todayLocal()): Dlg => {
  const cur = c.readiness?.[which]
  return { kind: 'readiness', which, form: { value: cur ? String(cur.value) : '', ...sourceChoice(cur?.source, which), date: today, comment: '' } }
}
export const newAppeal = (today = todayLocal()): Dlg =>
  ({ kind: 'appeal', form: { date: today, subject: '', applicants: '1', collective: false, choice: '', other: '', comment: '' } })
export const newAppealsCheck = (c: Complex, today = todayLocal()): Dlg =>
  ({ kind: 'appealsCheck', form: { ...sourceChoice(c.appealsCheck?.source, 'appeals'), date: today, comment: '' } })
export const newPhoto = (today = todayLocal()): Dlg => ({ kind: 'photo', form: { file: null, date: today, choice: '', other: '', activity: '', note: '' } })
