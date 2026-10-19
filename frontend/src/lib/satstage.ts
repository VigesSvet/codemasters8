// «Спутник против рекламы»: стадия стройки на снимке Esri Wayback (гипотеза ИИ, shared/sat_stage.json) и что о стадии
// заявляет реклама ЖК — GET /api/complexes/{id}/sat-stage (app/satstage.py). Подсказки без вердикта, в балл не входят.
import { apiGet, orNull } from './api'

export type SatStageCode = 'empty_lot' | 'excavation' | 'foundation' | 'frame' | 'roof_facade' | 'completed' | 'unclear'
export interface SatImage {
  role: 'latest' | 'start'; releaseId: number; releaseDate?: string; acquired?: string | null; sensor?: string | null; res?: number | null
  stage: SatStageCode; stageLabel: string; confidence: 'low' | 'medium' | 'high'; reasoning: string; cues: string[]
  model?: string; servedModel?: string | null; generatedAt?: string; costUsd?: number
}
export interface SatClaim {
  adId: string; date: string; platform: string; account: string; basis: string
  kind: 'completed' | 'advanced' | 'building' | 'early' | 'money'; kindLabel: string; quote: string; via: 'claude' | 'rules'
}
export type HintKind = 'ahead' | 'money_early' | 'behind' | 'before_start' | 'stale' | 'match' | 'unclear'
export interface SatHint { kind: HintKind; factor?: string | null; text: string; adId?: string; quote?: string; adDate?: string; releaseId?: number; acquired?: string | null }
export interface SatStageOut {
  complexId: string; images: SatImage[]; claims: SatClaim[]; hints: SatHint[]; discrepancy: boolean
  promptVersion: string; disclaimer: string; note?: string | null
}

/** Расхождения — подсказка аналитику сверить; остальное — пояснения */
export const DISCREPANCY: HintKind[] = ['ahead', 'money_early', 'behind']
export const CONF_RU: Record<SatImage['confidence'], string> = { low: 'низкая', medium: 'средняя', high: 'высокая' }

export const getSatStage = (cid: string) =>
  orNull(apiGet<SatStageOut>(`/complexes/${encodeURIComponent(cid)}/sat-stage`), (x) => !!x && typeof x === 'object' && Array.isArray((x as SatStageOut).images))
