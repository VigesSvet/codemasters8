// Эмблемы SAQ для панелей статистики: свои двухтоновые SVG 48×48 — заливка тоном (--em-tint), контур currentColor.
// Тон задаёт обёртка .em.<tone> (styles/board.css); без тона — графит на светло-сером.
import type { ReactNode } from 'react'

export type EmblemName =
  | 'inbox' | 'search' | 'review' | 'stamp' | 'dismiss' | 'unassigned' | 'flag' | 'city' | 'traffic'
  | 'crane' | 'camera' | 'photos' | 'database' | 'docs' | 'feed' | 'plug' | 'calendar' | 'hourglass' | 'timer' | 'devs'

const T = { fill: 'var(--em-tint)' }
const S = { fill: 'none', stroke: 'currentColor', strokeWidth: 2.2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }

const ART: Record<EmblemName, ReactNode> = {
  // стопка входящих: лоток и два листа
  inbox: <>
    <rect x="9" y="7" width="26" height="20" rx="3" {...T} />
    <path d="M13 6h18a3 3 0 0 1 3 3v12M17 13h12M17 18h8" {...S} />
    <path d="M5 26h11l3 5h10l3-5h11v11a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z" {...T} />
    <path d="M5 26h11l3 5h10l3-5h11v11a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z" {...S} />
  </>,
  // лупа над строками: нужна проверка
  search: <>
    <rect x="6" y="6" width="26" height="32" rx="4" {...T} />
    <path d="M12 14h14M12 20h8M12 26h5" {...S} />
    <circle cx="29" cy="29" r="8" fill="#fff" />
    <circle cx="29" cy="29" r="8" {...S} />
    <path d="M35 35l6 6" {...S} strokeWidth={3.2} />
  </>,
  // документ с часами: на проверке
  review: <>
    <path d="M10 6h18l8 8v24a4 4 0 0 1-4 4H10a4 4 0 0 1-4-4V10a4 4 0 0 1 4-4z" {...T} />
    <path d="M28 6v8h8M12 18h12M12 24h8" {...S} />
    <path d="M10 6h18l8 8v8M20 42H10a4 4 0 0 1-4-4V10a4 4 0 0 1 4-4" {...S} />
    <circle cx="33" cy="33" r="9" fill="#fff" />
    <circle cx="33" cy="33" r="9" {...S} />
    <path d="M33 28.5V33l3 2" {...S} />
  </>,
  // печать: нарушение подтверждено
  stamp: <>
    <rect x="8" y="27" width="32" height="8" rx="3" {...T} />
    <circle cx="24" cy="11" r="5" {...S} />
    <path d="M21 16v5h6v-5M12 21h24a4 4 0 0 1 4 4v10H8V25a4 4 0 0 1 4-4zM8 41h32" {...S} />
  </>,
  // документ с крестом: отклонено
  dismiss: <>
    <rect x="7" y="6" width="26" height="34" rx="4" {...T} />
    <path d="M13 14h14M13 20h9" {...S} />
    <path d="M24 40H11a4 4 0 0 1-4-4V10a4 4 0 0 1 4-4h18a4 4 0 0 1 4 4v12" {...S} />
    <circle cx="34" cy="34" r="8.5" fill="#fff" />
    <circle cx="34" cy="34" r="8.5" {...S} />
    <path d="M31 31l6 6M37 31l-6 6" {...S} />
  </>,
  // пунктирный силуэт: без исполнителя
  unassigned: <>
    <circle cx="24" cy="16" r="9" {...T} />
    <path d="M8 42c0-9 7-15 16-15s16 6 16 15z" {...T} />
    <circle cx="24" cy="16" r="9" {...S} strokeDasharray="4 3.2" />
    <path d="M8 42c0-9 7-15 16-15s16 6 16 15" {...S} strokeDasharray="4 3.2" />
  </>,
  // флажок сигнала
  flag: <>
    <path d="M12 8h25l-6 8 6 8H12z" {...T} />
    <path d="M12 8h25l-6 8 6 8H12M12 5v38" {...S} />
    <path d="M7 43h10" {...S} />
  </>,
  // кварталы города: на контроле
  city: <>
    <rect x="22" y="6" width="18" height="36" rx="2" {...T} />
    <rect x="7" y="18" width="15" height="24" rx="2" {...T} />
    <path d="M22 42V8a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v34M7 42V20a2 2 0 0 1 2-2h13M4 42h40" {...S} />
    <path d="M28 13h6M28 19h6M28 25h6M12 25h5M12 31h5M28 31h6" {...S} />
  </>,
  // светофор риска
  traffic: <>
    <rect x="14" y="4" width="20" height="40" rx="10" {...T} />
    <rect x="14" y="4" width="20" height="40" rx="10" {...S} />
    <circle cx="24" cy="14" r="4" fill="var(--red)" />
    <circle cx="24" cy="24" r="4" fill="var(--amber)" />
    <circle cx="24" cy="34" r="4" fill="var(--green)" />
  </>,
  // башенный кран: мачта-решётка, стрела с противовесом, крюк с грузом
  crane: <>
    <rect x="9" y="38" width="16" height="5" rx="1.5" {...T} />
    <rect x="29" y="25" width="9" height="7" rx="1.5" {...T} />
    <path d="M14 38V10h6v28M14 16l6 6M20 16l-6 6M14 26l6 6M20 26l-6 6M17 4L5 10h38M17 4l13 6M5 10v4h5v-4M34 10v15M29 25h9v7h-9z" {...S} />
    <path d="M6 43h22" {...S} />
  </>,
  // фотоаппарат
  camera: <>
    <rect x="5" y="13" width="38" height="27" rx="5" {...T} />
    <path d="M17 13l3-5h8l3 5h7a5 5 0 0 1 5 5v17a5 5 0 0 1-5 5H10a5 5 0 0 1-5-5V18a5 5 0 0 1 5-5z" {...S} />
    <circle cx="24" cy="26" r="7" fill="#fff" />
    <circle cx="24" cy="26" r="7" {...S} />
    <circle cx="36" cy="19" r="1.4" fill="currentColor" />
  </>,
  // два снимка с горами
  photos: <>
    <rect x="12" y="6" width="30" height="24" rx="4" {...T} />
    <path d="M16 6h22a4 4 0 0 1 4 4v16" {...S} />
    <rect x="6" y="14" width="30" height="26" rx="4" fill="#fff" />
    <rect x="6" y="14" width="30" height="26" rx="4" {...S} />
    <path d="M6 34l9-9 7 7 4-4 10 10" {...S} />
    <circle cx="27" cy="22" r="2.5" {...S} />
  </>,
  // цилиндр базы данных
  database: <>
    <path d="M8 11v26c0 3 7 6 16 6s16-3 16-6V11" {...T} />
    <ellipse cx="24" cy="11" rx="16" ry="6" fill="#fff" />
    <ellipse cx="24" cy="11" rx="16" ry="6" {...S} />
    <path d="M8 11v26c0 3 7 6 16 6s16-3 16-6V11M8 20c0 3 7 6 16 6s16-3 16-6M8 28.5c0 3 7 6 16 6s16-3 16-6" {...S} />
  </>,
  // реестр — веер документов
  docs: <>
    <rect x="15" y="5" width="25" height="31" rx="3" {...T} />
    <path d="M19 5h18a3 3 0 0 1 3 3v24" {...S} />
    <rect x="8" y="12" width="25" height="31" rx="3" fill="#fff" />
    <rect x="8" y="12" width="25" height="31" rx="3" {...S} />
    <path d="M14 21h13M14 27h13M14 33h8" {...S} />
  </>,
  // рупор рекламы
  feed: <>
    <path d="M10 18h7l17-9v30l-17-9h-7a4 4 0 0 1-4-4v-4a4 4 0 0 1 4-4z" {...T} />
    <path d="M10 18h7l17-9v30l-17-9h-7a4 4 0 0 1-4-4v-4a4 4 0 0 1 4-4zM17 18v12M14 30l3 10h5l-2-10M40 19c1.5 1.4 2.3 3 2.3 5s-.8 3.6-2.3 5" {...S} />
  </>,
  // штекер сборщика
  plug: <>
    <rect x="14" y="16" width="20" height="16" rx="4" {...T} />
    <path d="M19 6v10M29 6v10M14 16h20v8a10 10 0 0 1-20 0zM24 34v9" {...S} />
  </>,
  // календарь
  calendar: <>
    <rect x="6" y="9" width="36" height="33" rx="5" {...T} />
    <path d="M11 9h26a5 5 0 0 1 5 5v23a5 5 0 0 1-5 5H11a5 5 0 0 1-5-5V14a5 5 0 0 1 5-5zM6 18h36M15 5v8M33 5v8" {...S} />
    <rect x="13" y="24" width="7" height="6" rx="1.5" fill="currentColor" />
  </>,
  // песочные часы: стройка стоит
  hourglass: <>
    <path d="M14 8h20c0 9-5 12-10 16-5-4-10-7-10-16z" {...T} />
    <path d="M24 30c-4 2.5-8 5.5-8.5 10h17c-.5-4.5-4.5-7.5-8.5-10z" fill="currentColor" opacity="0.85" />
    <path d="M11 5h26M11 43h26M14 5c0 11 6 14 10 19 4-5 10-8 10-19M14 43c0-11 6-14 10-19 4 5 10 8 10 19" {...S} />
  </>,
  // секундомер: время решения
  timer: <>
    <circle cx="24" cy="27" r="15" {...T} />
    <circle cx="24" cy="27" r="15" {...S} />
    <path d="M20 5h8M24 5v7M24 27l6-6M36 13l3-3" {...S} />
  </>,
  // застройщики — каска
  devs: <>
    <path d="M8 30a16 16 0 0 1 32 0z" {...T} />
    <path d="M8 30a16 16 0 0 1 32 0M5 30h38v4a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3zM20 15v10M28 15v10M20 15a12 12 0 0 1 8 0" {...S} />
  </>,
}

export type EmTone = 'red' | 'yellow' | 'green' | 'blue' | 'gray' | 'dark'

export function Emblem({ name, tone, size = 48 }: { name: EmblemName; tone?: EmTone; size?: number }) {
  return (
    <span className={`em ${tone ?? ''}`} style={{ width: size, height: size }} aria-hidden>
      <svg viewBox="0 0 48 48" width={size} height={size}>{ART[name]}</svg>
    </span>
  )
}
