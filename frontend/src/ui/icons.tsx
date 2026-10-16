// Иконки интерфейса.
// 1) Оригинальные SVG сервисов Яндекса (Недвижимость, Такси, Карты, Диск, Еда, Лавка) — ./yandex-svg.ts
// 2) Чего на этих страницах нет — Gravity UI Icons (MIT), иконочная библиотека дизайн-системы Яндекса.
import type { ComponentType, CSSProperties, SVGProps } from 'react'
import {
  ArrowDownToLine, ArrowUpFromLine, ArrowsRotateRight, Bell, Book, ChartColumn, Check, CircleQuestion, Database, Eye, Factory, Flag, House, Key,
  LayoutCellsLarge, ListCheck, Megaphone, PersonWorker, Persons, Plane, ShieldCheck, Smartphone, TrafficLight, Tray, Calendar,
  Gear, Lock, MapPin, Play, PlugConnection, Sparkles, TriangleExclamation,
} from '@gravity-ui/icons'
import { Y } from './yandex-svg'

type P = SVGProps<SVGSVGElement> & { size?: number; strokeWidth?: number | string }

const y = (name: string, rotate?: number) => {
  const d = Y[name]
  const I = ({ size = 20, strokeWidth: _sw, style, ...rest }: P) => (
    <svg width={size} height={size} viewBox={d.vb} fill={d.fill} aria-hidden {...rest}
      style={{ ...(rotate ? { transform: `rotate(${rotate}deg)` } : null), ...(style as CSSProperties) }}
      dangerouslySetInnerHTML={{ __html: d.inner }} />
  )
  return I
}
const g = (C: ComponentType<SVGProps<SVGSVGElement>>) => {
  const I = ({ size = 20, strokeWidth: _sw, ...rest }: P) => <C width={size} height={size} aria-hidden {...rest} />
  return I
}

// — Яндекс (оригинальные SVG)
export const IcChevronRight = y('ChevronRight')
export const IcChevronLeft = y('ChevronLeft')
export const IcChevronDown = y('ChevronDown')
export const IcChevronUp = y('ChevronDown', 180)
export const IcPlus = y('Plus')
export const IcMinus = y('Minus')
export const IcClose = y('Close')
export const IcCloseCircle = y('CloseCircle')
export const IcSearch = y('Search')
export const IcSparkSearch = y('SparkSearch')
export const IcLocate = y('Locate')
export const IcHeart = y('Heart')
export const IcHeartFill = y('HeartFill')
export const IcComment = y('Chat')
export const IcLogout = y('Login')
export const IcMap = y('Map')
export const IcSliders = y('Sliders')
export const IcFilter = y('Sliders')
export const IcMore = y('More')
export const IcMoreV = y('MoreV')
export const IcClock = y('Clock')
export const IcLayers = y('Layers')
export const IcList = y('List')
export const IcRuler = y('Ruler')
export const IcGlobe = y('Globe')
export const IcInfo = y('Info')
export const IcStar = y('Star')
export const IcWalk = y('Walk')
export const IcMetro = y('Metro')
export const IcBus = y('Bus')
export const IcBurger = y('Burger')
export const IcArrowRight = y('ArrowRight')
export const IcSupport = y('Support')
export const IcSend = y('Navigate')
export const IcCamera = y('Camera')
export const IcCameraAdd = y('CameraAdd')
export const IcPhoneCall = y('Phone')
export const IcExternal = y('Share')
export const IcBookmark = y('Bookmark')
export const IcPencil = y('Pencil')
export const IcLike = y('Like')
export const IcWarning = y('Warning')
export const IcCalculator = y('Calculator')
export const IcBank = y('Bank')
export const IcCrane = y('Crane')
export const IcWallet = y('Wallet')
export const IcUsersFamily = y('Family')
export const IcBriefcase = y('Briefcase')
export const IcPicture = y('Picture')
export const IcLink = y('Link')
export const IcDoc = y('Doc')
export const IcCloud = y('Cloud')
export const IcFolder = y('Folder')
export const IcCompare = y('Compare')
export const IcBolt = y('Bolt')
export const IcRoute = y('Route')

// — Gravity UI (MIT, Яндекс) для того, чего нет на страницах сервисов
export const IcCheck = g(Check)
export const IcBell = g(Bell)
export const IcMegaphone = g(Megaphone)
export const IcUpload = g(ArrowUpFromLine)
export const IcDownload = g(ArrowDownToLine)
export const IcRefresh = g(ArrowsRotateRight)
export const IcFlag = g(Flag)
export const IcHelp = g(CircleQuestion)
export const IcGrid = g(LayoutCellsLarge)
export const IcDrone = g(Plane)
export const IcKey = g(Key)
export const IcEye = g(Eye)
export const IcTraffic = g(TrafficLight)
export const IcShield = g(ShieldCheck)
export const IcCalendar = g(Calendar)
export const IcInbox = g(Tray)
export const IcQueue = g(ListCheck)
export const IcDatabase = g(Database)
export const IcPhone = g(Smartphone)
export const IcBook = g(Book)
export const IcFactory = g(Factory)
export const IcWorker = g(PersonWorker)
export const IcBuilding = g(House)
export const IcUsers = g(Persons)
export const IcChart = g(ChartColumn)
// подключения источников (страница «Источники»), сверка адреса 2ГИС
export const IcPlugConnection = g(PlugConnection)
export const IcPlay = g(Play)
export const IcMapPin = g(MapPin)
export const IcTriangleExclamation = g(TriangleExclamation)
export const IcSparkles = g(Sparkles)
export const IcGear = g(Gear)
export const IcLock = g(Lock)
