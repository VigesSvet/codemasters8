// Площадки публикаций и внешние ссылки: подписи по коду площадки и безопасный адрес публикации.
// Сборщик или бот заявок может прислать площадку не из списка — сервер пишет 'other': подпись «Другая площадка», страницы не падают.
import type { Platform } from '../data/types'

export type PlatformInfo = { name: string; color: string }

/** Площадка не из списка: сборщик или бот заявок прислал публикацию с другой площадки (сервер пишет 'other') */
const OTHER_PLATFORM: PlatformInfo = { name: 'Другая площадка', color: '#8A8C91' }

/** Площадки по коду, вместе с 'other' — у неё своя подпись и общий значок */
export const PLATFORMS = {
  instagram: { name: 'Instagram', color: '#E4405F' },
  tiktok: { name: 'TikTok', color: '#000000' },
  krisha: { name: 'Krisha.kz', color: '#FFD84D' },
  olx: { name: 'OLX', color: '#23E5DB' },
  telegram: { name: 'Telegram', color: '#26A5E4' },
  facebook: { name: 'Facebook', color: '#0866FF' },
  '2gis': { name: '2ГИС', color: '#19AA1E' },
  other: OTHER_PLATFORM,
} satisfies Record<Platform | 'other', PlatformInfo>

/** Название площадки; код не из списка — «Другая площадка» */
export const platformName = (p?: string | null) => (p && Object.hasOwn(PLATFORMS, p) ? PLATFORMS[p as Platform].name : OTHER_PLATFORM.name)

/** Площадки, которые аналитик выбирает при ручном вводе — без «Другой площадки» */
export const KNOWN_PLATFORMS = (Object.keys(PLATFORMS) as (Platform | 'other')[]).filter((p): p is Platform => p !== 'other')

/** Внешняя ссылка публикации — только http(s): адрес из источника или от жителя не исполняется как javascript: */
export function externalUrl(u?: string | null): string | undefined {
  if (!u?.trim()) return undefined
  try {
    const x = new URL(u.trim())
    return x.protocol === 'http:' || x.protocol === 'https:' ? x.href : undefined
  } catch {
    return undefined
  }
}
