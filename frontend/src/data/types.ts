export type Zone = 'green' | 'yellow' | 'red'
export type Tri = 'yes' | 'no' | 'unknown'

export type Stage = 'котлован' | 'фундамент' | 'каркас' | 'кровля' | 'отделка' | 'введён'

export interface Developer {
  id: string
  name: string
  bin: string
  director: string
  registered: string
  objects: number
  unfinished: number
  site?: string
  instagram?: string
}

export interface Basis {
  /** Гарантия ЕО (Казахстанская жилищная компания) */
  guarantee: Tri
  guaranteeRef?: string
  /** Разрешение МИО на привлечение денег дольщиков */
  mio: Tri
  mioRef?: string
  /** Банк второго уровня (проектное финансирование / эскроу) */
  bvu: Tri
  bank?: string
}

export interface SitePhoto {
  date: string
  src: string
  source: 'дрон' | 'камера' | 'выезд'
  activity: 'есть' | 'нет' | 'низкая'
  note?: string
  fence?: Tri
  bins?: Tri
  wheelWash?: Tri
}

export interface Complex {
  id: string
  name: string
  developerId: string
  address: string
  district: string
  lng: number
  lat: number
  stage: Stage
  progress: number
  floors: number
  flats: number
  start: string
  plannedEnd: string
  basis: Basis
  inPublishedList: Tri
  cover: string
  photos: SitePhoto[]
  idleDays: number
}

export type Platform = 'instagram' | 'tiktok' | 'krisha' | 'olx' | 'telegram' | 'facebook' | '2gis'

export type AdStatus = 'new' | 'needs_check' | 'in_review' | 'confirmed' | 'dismissed'

export interface Ad {
  id: string
  platform: Platform
  account: string
  url: string
  date: string
  text: string
  ocr?: string
  image?: string
  complexId?: string
  matchConfidence: number
  price?: string
  source: 'manual' | 'parser' | 'demo'
  status: AdStatus
  assignee?: string
  reach?: number
}

export interface RiskFactor {
  code: string
  title: string
  points: number
  state: 'hit' | 'clear' | 'unknown'
  evidence: string
}

export interface RiskResult {
  score: number
  zone: Zone
  needsCheck: boolean
  factors: RiskFactor[]
  summary: string
}

export interface AuditEvent {
  at: string
  who: string
  what: string
}
