// Собственный набор иконок в манере Яндекс Go: сетка 24, линия 2px, скруглённые концы.
import type { SVGProps } from 'react'

type P = SVGProps<SVGSVGElement> & { size?: number }
const base = (size = 24, rest: SVGProps<SVGSVGElement>) => ({
  width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true, ...rest,
})
const mk = (d: React.ReactNode) => ({ size, ...rest }: P) => <svg {...base(size, rest)}>{d}</svg>

export const IcChevronRight = mk(<path d="m9 5 7 7-7 7" />)
export const IcChevronLeft = mk(<path d="m15 5-7 7 7 7" />)
export const IcChevronDown = mk(<path d="m6 9 6 6 6-6" />)
export const IcChevronUp = mk(<path d="m6 15 6-6 6 6" />)
export const IcPlus = mk(<path d="M12 5v14M5 12h14" />)
export const IcMinus = mk(<path d="M5 12h14" />)
export const IcClose = mk(<path d="m6 6 12 12M18 6 6 18" />)
export const IcCheck = mk(<path d="m5 12.5 4.5 4.5L19 7.5" />)
export const IcSearch = mk(<><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></>)
export const IcLocate = ({ size = 24, ...rest }: P) => <svg {...base(size, rest)} fill="currentColor" stroke="none"><path d="M20.6 3.4 3.9 10.2c-.8.3-.7 1.5.1 1.7l6.6 1.5 1.5 6.6c.2.8 1.4.9 1.7.1l6.8-16.7z" /></svg>
export const IcBell = mk(<><path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15z" /><path d="M10 20.5a2.2 2.2 0 0 0 4 0" /></>)
export const IcLogin = mk(<><path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4" /><path d="M3 12h11M10 8l4 4-4 4" /></>)
export const IcLogout = mk(<><path d="M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4" /><path d="M21 12H10M17 8l4 4-4 4" /></>)
export const IcMap = mk(<><path d="m3 6 6-2 6 2 6-2v14l-6 2-6-2-6 2z" /><path d="M9 4v14M15 6v14" /></>)
export const IcBuilding = mk(<><path d="M5 21V5a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v16" /><path d="M15 10h3a1 1 0 0 1 1 1v10M3 21h18M8.5 8h3M8.5 12h3M8.5 16h3" /></>)
export const IcCrane = mk(<><path d="M6 21V4l12 3M6 7h14M17 7v6M15 13h4v3h-4zM3 21h8" /><path d="m6 4 4 3" /></>)
export const IcMegaphone = mk(<><path d="M4 10v4a1 1 0 0 0 1 1h3l8 4V5L8 9H5a1 1 0 0 0-1 1z" /><path d="M8 15l1.5 5M19 9.5a3 3 0 0 1 0 5" /></>)
export const IcInbox = mk(<><path d="M4 13 6.5 5h11L20 13v6H4z" /><path d="M4 13h4.5a3.5 3.5 0 0 0 7 0H20" /></>)
export const IcUsers = mk(<><circle cx="9" cy="8" r="3.5" /><path d="M3 20a6 6 0 0 1 12 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5a6 6 0 0 1 3 5.5" /></>)
export const IcChart = mk(<><path d="M4 20V4M4 20h16" /><path d="M8 16v-4M12 16V8M16 16v-6" /></>)
export const IcCamera = mk(<><path d="M4 8h3l2-3h6l2 3h3v11H4z" /><circle cx="12" cy="13" r="3.5" /></>)
export const IcBook = mk(<><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z" /><path d="M4 19a2 2 0 0 1 2-2h13M8 7h7" /></>)
export const IcDatabase = mk(<><ellipse cx="12" cy="6" rx="7" ry="3" /><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3" /></>)
export const IcDoc = mk(<><path d="M6 3h8l5 5v13H6z" /><path d="M14 3v5h5M9 13h6M9 17h6" /></>)
export const IcPhone = mk(<><rect x="7" y="2.5" width="10" height="19" rx="2.5" /><path d="M11 18.5h2" /></>)
export const IcFlag = mk(<path d="M5 21V4h11l-2 4 2 4H5" />)
export const IcHelp = mk(<><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6M12 17.2v.1" /></>)
export const IcInfo = mk(<><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7.5v.1" /></>)
export const IcWarning = mk(<><path d="M12 3.5 2.5 20h19z" /><path d="M12 10v4.5M12 17.3v.1" /></>)
export const IcUpload = mk(<><path d="M12 15V4M7.5 8.5 12 4l4.5 4.5" /><path d="M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" /></>)
export const IcDownload = mk(<><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5" /><path d="M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" /></>)
export const IcRefresh = mk(<><path d="M20 11a8 8 0 0 0-14.5-4.5L4 8" /><path d="M4 4v4h4M4 13a8 8 0 0 0 14.5 4.5L20 16" /><path d="M20 20v-4h-4" /></>)
export const IcExternal = mk(<><path d="M14 4h6v6M20 4l-9 9" /><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></>)
export const IcGrid = mk(<><rect x="4" y="4" width="6.5" height="6.5" rx="1.5" /><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5" /><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5" /><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5" /></>)
export const IcList = mk(<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.1M4.5 12h.1M4.5 18h.1" />)
export const IcSend = mk(<path d="M21 3 3 10.5l7 2.5 2.5 7zM10 13l5-5" />)
export const IcGlobe = mk(<><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z" /></>)
export const IcDrone = mk(<><rect x="9" y="10" width="6" height="4" rx="1" /><path d="M9 11 6 8M15 11l3-3M9 13l-3 3M15 13l3 3" /><path d="M3 7h6M15 7h6M3 17h6M15 17h6" /></>)
export const IcClock = mk(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>)
export const IcKey = mk(<><circle cx="8" cy="15" r="4" /><path d="m11 12 8-8M16 7l2 2M14 9l2 2" /></>)
export const IcLayers = mk(<><path d="m12 3 9 5-9 5-9-5z" /><path d="m3 13 9 5 9-5" /></>)
export const IcFilter = mk(<path d="M4 5h16l-6 7.5V19l-4 1.5v-8z" />)
export const IcBolt = ({ size = 24, ...rest }: P) => <svg {...base(size, rest)} fill="currentColor" stroke="none"><path d="M13.5 2 5 13.5h6L9.5 22 19 9.5h-6.5z" /></svg>
export const IcDot = ({ size = 24, ...rest }: P) => <svg {...base(size, rest)}><circle cx="12" cy="12" r="6" strokeWidth="3" /></svg>
export const IcEye = mk(<><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" /><circle cx="12" cy="12" r="3" /></>)
export const IcPin = ({ size = 24, ...rest }: P) => <svg {...base(size, rest)} fill="currentColor" stroke="none"><path d="M12 2a7 7 0 0 0-7 7c0 5 7 13 7 13s7-8 7-13a7 7 0 0 0-7-7zm0 9.5a2.5 2.5 0 1 1 0-5 2.5 2.5 0 0 1 0 5z" /></svg>
