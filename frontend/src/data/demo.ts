// ДЕМО-ДАННЫЕ. Все названия ЖК, застройщиков, БИН, аккаунтов и текстов объявлений вымышлены.
// Совпадения с реальными организациями случайны. Используются только для демонстрации интерфейса.
// Сам набор — shared/seed_data.json (общий с бэкендом); здесь только приведение типов для офлайн-режима.
// Журнал событий (seed.audit) живёт на сервере: без бэкенда интерфейс показывает только действия текущей сессии.
import seedJson from '../../../shared/seed_data.json'
import type { Ad, Analyst, Appeal, Complex, Developer } from './types'

interface Seed {
  developers: Developer[]
  complexes: Complex[]
  ads: Ad[]
  appeals: Appeal[]
  analysts: Analyst[]
  publishedList: { source: string; updated: string; count: number; url?: string | null }
}

const seed = seedJson as unknown as Seed

export const developers: Developer[] = seed.developers
export const complexes: Complex[] = seed.complexes
export const ads: Ad[] = seed.ads
export const appeals: Appeal[] = seed.appeals
export const analysts: Analyst[] = seed.analysts
export const publishedListMeta = seed.publishedList
