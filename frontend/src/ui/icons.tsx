// Иконки интерфейса. Единый API: size(по умолчанию 20) и любые свойства <svg>; иконка декоративная(aria-hidden),
// подпись даёт кнопка или текст рядом.
// 1) Gravity UI Icons(@gravity-ui/icons, MIT) - основной набор.
// 2) Чего в Gravity UI нет - контуры Tabler Icons(MIT, https://tabler.io/icons), перенесены сюда как данные путей.
// Источники и лицензии - CREDITS.md.
import type { ComponentType, SVGProps } from 'react'
import {
  ArrowDownToLine, ArrowRightFromSquare, ArrowUpFromLine, ArrowUpRightFromSquare, ArrowsRotateRight, Bell, Calendar, Camera, Check, ChevronDown,
  ChevronLeft, ChevronRight, ChevronUp, CircleExclamationFill, CircleInfo, CircleQuestion, Clock, Comment, Database, FileText, Flag, Gear, Geo, Globe,
  House, Key, LayoutCellsLarge, Link, ListCheck, ListUl, LocationArrow, Lock, Magnifier, MagnifierPlus, MapPin, Megaphone, Minus, PaperPlane,
  Pencil, PersonWorker, Persons, Picture, Plane, Play, PlugConnection, Plus, ShieldCheck, Sliders, Smartphone, Sparkles, SparklesFill,
  ThunderboltFill, TriangleExclamation, Wallet, Xmark,
} from '@gravity-ui/icons'

type P = SVGProps<SVGSVGElement> & { size?: number; strokeWidth?: number | string }

/** Иконка Gravity UI: заливка currentColor, сетка 16×16 */
const g = (C: ComponentType<SVGProps<SVGSVGElement>>) => {
  const I = ({ size = 20, strokeWidth: _sw, ...rest }: P) => <C width={size} height={size} aria-hidden {...rest} />
  return I
}

/** Контурная иконка Tabler: сетка 24×24, линия currentColor, толщина по умолчанию 2 */
const t = (paths: string[]) => {
  const I = ({ size = 20, strokeWidth = 2, ...rest }: P) => (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden {...rest}>
      {paths.map((d) => <path key={d} d={d} />)}
    </svg>
  )
  return I
}

// - навигация и действия
export const IcChevronRight = g(ChevronRight)
export const IcChevronLeft = g(ChevronLeft)
export const IcChevronDown = g(ChevronDown)
export const IcChevronUp = g(ChevronUp)
export const IcPlus = g(Plus)
export const IcMinus = g(Minus)
export const IcClose = g(Xmark)
export const IcCheck = g(Check)
export const IcSearch = g(Magnifier)
export const IcZoomIn = g(MagnifierPlus) // просмотр файлов публикации: "Открыть" на обложке и миниатюрах
export const IcSliders = g(Sliders)
export const IcList = g(ListUl)
export const IcGrid = g(LayoutCellsLarge)
export const IcPencil = g(Pencil)
export const IcSend = g(PaperPlane)
export const IcExternal = g(ArrowUpRightFromSquare)
export const IcLink = g(Link)
export const IcUpload = g(ArrowUpFromLine)
export const IcDownload = g(ArrowDownToLine)
export const IcRefresh = g(ArrowsRotateRight)
export const IcLogout = g(ArrowRightFromSquare)
export const IcPlay = g(Play)
export const IcGear = g(Gear)
export const IcLock = g(Lock)
export const IcKey = g(Key)

// - карта
export const IcMap = g(Geo)
export const IcMapPin = g(MapPin)
export const IcLocate = g(LocationArrow)
export const IcGlobe = g(Globe)
export const IcDrone = g(Plane)

// - состояние и подсказки
export const IcInfo = g(CircleInfo)
export const IcHelp = g(CircleQuestion)
export const IcWarning = g(CircleExclamationFill)
export const IcTriangleExclamation = g(TriangleExclamation)
export const IcShield = g(ShieldCheck)
export const IcFlag = g(Flag)
export const IcBell = g(Bell)
export const IcClock = g(Clock)
export const IcCalendar = g(Calendar)

// - содержимое
export const IcDoc = g(FileText)
export const IcPicture = g(Picture)
export const IcCamera = g(Camera)
export const IcComment = g(Comment)
export const IcMegaphone = g(Megaphone)
export const IcDatabase = g(Database)
export const IcQueue = g(ListCheck)
export const IcPhone = g(Smartphone)
export const IcPlugConnection = g(PlugConnection) // подключения источников(страница "Источники"), сверка адреса 2ГИС
export const IcSparkles = g(Sparkles)
export const IcSparkSearch = g(SparklesFill) // гипотеза ИИ
export const IcBolt = g(ThunderboltFill)

// - люди, деньги, стройка
export const IcUsers = g(Persons)
export const IcUsersFamily = g(Persons)
export const IcWorker = g(PersonWorker)
export const IcBuilding = g(House)
export const IcWallet = g(Wallet)

// - Tabler Icons(MIT): camera-plus, building-bank, crane
export const IcCameraAdd = t([
  'M12 20h-7a2 2 0 0 1 -2 -2v-9a2 2 0 0 1 2 -2h1a2 2 0 0 0 2 -2a1 1 0 0 1 1 -1h6a1 1 0 0 1 1 1a2 2 0 0 0 2 2h1a2 2 0 0 1 2 2v3.5',
  'M16 19h6', 'M19 16v6', 'M9 13a3 3 0 1 0 6 0a3 3 0 0 0 -6 0',
])
export const IcBank = t(['M3 21l18 0', 'M3 10l18 0', 'M5 6l7 -3l7 3', 'M4 10l0 11', 'M20 10l0 11', 'M8 14l0 3', 'M12 14l0 3', 'M16 14l0 3'])
export const IcCrane = t(['M6 21h6', 'M9 21v-18l-6 6h18', 'M9 3l10 6', 'M17 9v4a2 2 0 1 1 -2 2'])
