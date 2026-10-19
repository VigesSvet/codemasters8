// «Кто продаёт»: связанные продавцы, аномальная цена, связи застройщика (backend/app/sellers.py, prices.py,
// affiliations.py). Всё — гипотезы для проверки вне балла риска: общий номер бывает у агентства, которое продаёт
// несколько ЖК. Офлайн-копии этих данных нет — без сервера блоки не показываются.
import { apiGet, orNull } from './api'

export type SellerLinkKind = 'phone' | 'bin' | 'handle' | 'mention' | 'image'
export interface Evidence { adId: string; account?: string; source: 'text' | 'ocr' | 'speech' | 'image'; quote: string; raw?: string; whatsapp?: boolean; otherAdId?: string; distance?: number; mediaId?: number | null; otherMediaId?: number | null }
export interface SellerAccount { key: string; platform: string; account: string; ads: number; firstSeen: string | null; lastSeen: string | null }
export interface SellerPhone { phone: string; digits: string; whatsapp: boolean; accounts: string[]; ads: number; evidence: Evidence[] }
export interface SellerBin { bin: string; developerId: string | null; developerName: string | null; accounts: string[]; evidence: Evidence[] }
export interface SellerComplex { id: string; name: string; ads: number; confirmed: number; accounts: string[] }
export interface SellerLink { kind: SellerLinkKind; label: string; value: string; shown: string; accounts: string[]; evidence: Evidence[] }
export interface SellerGroup {
  key: string; title: string; accounts: SellerAccount[]; phones: SellerPhone[]; bins: SellerBin[]; complexes: SellerComplex[]
  links: SellerLink[]; adsCount: number; adIds: string[]; firstSeen: string | null; lastSeen: string | null; linked: boolean
}
export interface GraphNode { id: string; kind: 'account' | 'phone' | 'bin' | 'complex'; label: string; platform?: string; weight: number }
export interface GraphEdge { a: string; b: string; kind: SellerLinkKind | 'complex' }
export interface SellerAd { id: string; platform: string; account: string; date: string; url: string; complexId: string | null; complexName: string | null; snippet: string }
export interface SellerDetail extends SellerGroup { graph: { nodes: GraphNode[]; edges: GraphEdge[] }; ads: SellerAd[]; note: string }
export interface SellerStats { groups: number; linked: number; accounts: number; ads: number; phones: number; sharedPhones: number; imageLinks: number }
export interface SellersList { groups: SellerGroup[]; stats: SellerStats; note: string; imageMaxDistance: number }

export interface PriceRow {
  adId: string; platform: string; account: string; date: string | null; url: string | null; complexId: string | null; complexName: string | null
  perM2: number | null; method?: 'explicit' | 'implied' | 'total_area'; total?: number; area?: number; rooms: number | null
  source?: string; quote?: string; from?: boolean; fullPayment: { quote: string; source: string } | null
  median: number | null; medianBasis: 'rooms' | 'city' | null; belowPct: number | null; anomaly: boolean
}
export interface PriceReport {
  items: PriceRow[]; median: number | null; n: number; byRooms: Record<string, { median: number; n: number; used: boolean }>
  threshold: number; minGroup: number; anomalies: number; fullPayment: { adId: string; account: string; fullPayment: { quote: string }; perM2: number | null; anomaly: boolean }[]; note: string
}
export interface AdSellerHint {
  seller: { groupKey: string; title: string; accounts: number; ads: number; linked: boolean
    phones: { phone: string; digits: string; whatsapp: boolean; otherAds: number; accounts: number }[]
    links: { kind: SellerLinkKind; label: string; shown: string; accounts: number }[] } | null
  price: (PriceRow & { threshold: number; cityMedian: number | null; n: number }) | null
  note: string
}
export interface Affiliations {
  developer: { id: string; name: string; bin: string | null; binValid: boolean; director: string | null; registered: string | null; origin: string }
  complexes: { id: string; name: string; ads: number }[]; adsCount: number; confirmedAds: number
  accounts: { account: string; platform: string; ads: number }[]; phones: { phone: string; digits: string; ads: number }[]
  firstSeen: string | null; firstAd: string | null; registryRecords: number
  agents: { name: string; bin: string | null; binValid: boolean; records: number; sources: string[] }[]
  related: { id: string; name: string; bin: string | null; reasons: { kind: 'director' | 'phone' | 'agent' | 'seller'; label: string; value: string; adIds?: string[]; groupKey?: string }[] }[]
  sellerGroups: { key: string; title: string; accounts: number; ads: number; otherDevelopers: string[] }[]
  lookups: { label: string; url: string; note: string }[]; note: string
}

export const HYPOTHESIS = 'связь по номеру телефона — гипотеза для проверки'

const isObj = (x: unknown) => !!x && typeof x === 'object' && !Array.isArray(x)
export const listSellers = () => orNull(apiGet<SellersList>('/sellers'), (x) => isObj(x) && Array.isArray((x as SellersList).groups))
export const getSeller = (key: string) => apiGet<SellerDetail>(`/sellers/${encodeURIComponent(key)}`)
export const sellerOfAd = (id: string) => orNull(apiGet<AdSellerHint>(`/sellers/by-ad/${encodeURIComponent(id)}`), isObj)
export const getPrices = (all = false) => orNull(apiGet<PriceReport>(`/price-anomalies${all ? '?all=true' : ''}`), (x) => isObj(x) && Array.isArray((x as PriceReport).items))
export const getAffiliations = (id: string) => orNull(apiGet<Affiliations>(`/developers/${encodeURIComponent(id)}/affiliations`), isObj)

/** 470000 → «470 000 ₸/м²» */
export const fmtPerM2 = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v).toLocaleString('ru-RU').replace(/ | /g, ' ')} ₸/м²`)

/** Отклонение цены от медианы словами: «ниже рынка на 38%», «выше рынка на 12%», «на уровне рынка» (±3%) */
export function belowText(pct: number | null | undefined): string {
  if (pct == null) return 'нет медианы'
  if (Math.abs(pct) <= 3) return 'на уровне рынка'
  return pct > 0 ? `ниже рынка на ${pct}%` : `выше рынка на ${-pct}%`
}

export const ROOMS_LABEL = (r: string | number | null) => (r == null ? 'не указано' : String(r) === '0' ? 'студии' : String(r) === '4' ? '4+ комн.' : `${r}-комн.`)
export const METHOD_LABEL: Record<NonNullable<PriceRow['method']>, string> = {
  explicit: 'цена за м² названа в тексте',
  implied: 'сумма меньше цены любой квартиры — это цена за м²',
  total_area: 'цена квартиры ÷ площадь',
}
export const SOURCE_LABEL: Record<string, string> = { text: 'текст', ocr: 'OCR картинки', speech: 'речь в ролике', image: 'картинка', price: 'поле цены' }

/** Строка подсказки разбора публикации: «Этот номер встречается ещё в 8 публикациях / 2 аккаунтах» */
export function phoneHint(p: { otherAds: number; accounts: number }): string {
  if (!p.otherAds && p.accounts <= 1) return 'В других публикациях этот номер не встречается'
  const pubs = (n: number) => `${n} ${n % 10 === 1 && n % 100 !== 11 ? 'публикации' : 'публикациях'}`
  const accs = (n: number) => `${n} ${n % 10 === 1 && n % 100 !== 11 ? 'аккаунте' : 'аккаунтах'}`
  return `Этот номер встречается ещё в ${pubs(p.otherAds)} / ${accs(p.accounts)}`
}

/** Подпись узла схемы: без «krisha.kz / » и не длиннее max символов */
export function nodeLabel(label: string, max = 24): string {
  const s = label.replace(/^krisha\.kz \/ /, '')
  return s.length > max ? `${s.slice(0, max - 1)}…` : s
}

export interface Placed extends GraphNode { x: number; y: number }

/** Радиальная схема без библиотек: телефоны и БИН — в центре, аккаунты — на среднем кольце (соседние по общему контакту
 *  рядом), ЖК — снаружи рядом со своими аккаунтами. Детерминированно: одинаковые данные — одинаковая картинка */
export function radialLayout(nodes: GraphNode[], edges: GraphEdge[], w: number, h: number): Placed[] {
  const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 36
  const hubs = nodes.filter((n) => n.kind === 'phone' || n.kind === 'bin')
  const accs = nodes.filter((n) => n.kind === 'account')
  const cxs = nodes.filter((n) => n.kind === 'complex')
  const nb = (id: string) => edges.flatMap((e) => (e.a === id ? [e.b] : e.b === id ? [e.a] : []))
  // аккаунты по порядку первого общего контакта — связанные стоят рядом
  const order: GraphNode[] = []
  for (const hub of hubs) for (const id of nb(hub.id)) { const a = accs.find((x) => x.id === id); if (a && !order.includes(a)) order.push(a) }
  for (const a of accs) if (!order.includes(a)) order.push(a)
  const out: Placed[] = []
  const ring = (list: GraphNode[], r: number, start = -Math.PI / 2) => list.map((n, i) => {
    const t = start + (2 * Math.PI * i) / Math.max(list.length, 1)
    return { ...n, x: cx + r * Math.cos(t), y: cy + r * Math.sin(t) }
  })
  const rAcc = cxs.length ? R * 0.62 : R * 0.82
  out.push(...(hubs.length === 1 ? [{ ...hubs[0], x: cx, y: cy }] : ring(hubs, R * 0.24)))
  const placedAcc = order.length === 1 && !hubs.length ? [{ ...order[0], x: cx, y: cy }] : ring(order, rAcc)
  out.push(...placedAcc)
  // ЖК — по среднему углу своих аккаунтов, разводятся, если углы совпали
  const used: number[] = []
  for (const c of cxs) {
    const own = placedAcc.filter((a) => nb(c.id).includes(a.id))
    let t = own.length ? Math.atan2(own.reduce((s, a) => s + (a.y - cy), 0), own.reduce((s, a) => s + (a.x - cx), 0)) : 0
    while (used.some((u) => Math.abs(u - t) < 0.35)) t += 0.4
    used.push(t)
    out.push({ ...c, x: cx + R * Math.cos(t), y: cy + R * Math.sin(t) })
  }
  return out
}
