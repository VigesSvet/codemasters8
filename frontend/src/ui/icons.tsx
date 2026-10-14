// Иконки — Gravity UI Icons, открытая (MIT) иконочная библиотека дизайн-системы Яндекса.
// Обёртка оставляет единый API: <IcX size={20} />.
import type { ComponentType, SVGProps } from 'react'
import {
  ArrowDownToLine, ArrowRightFromSquare, ArrowUpFromLine, ArrowUpRightFromSquare, ArrowsRotateRight, Bell, Book, Camera, ChartColumn, Check, ChevronDown,
  ChevronLeft, ChevronRight, ChevronUp, CircleInfo, CircleQuestion, Clock, Comment, Database, Ellipsis, Eye, Factory, FileText, Flag, Funnel, GeoFill, Globe,
  Hammer, Heart, House, Key, Layers, LayoutCellsLarge, ListCheck, ListUl, LocationArrowFill, Magnifier, Megaphone, Minus, PaperPlane, Persons, Plane, Plus,
  ShieldCheck, Sliders, Smartphone, ThunderboltFill, TrafficLight, Tray, TriangleExclamation, Xmark, Calendar, Picture, PersonWorker, Briefcase,
} from '@gravity-ui/icons'

type P = SVGProps<SVGSVGElement> & { size?: number; strokeWidth?: number | string }
const w = (C: ComponentType<SVGProps<SVGSVGElement>>) => {
  const I = ({ size = 20, strokeWidth: _sw, ...rest }: P) => <C width={size} height={size} aria-hidden {...rest} />
  return I
}

export const IcChevronRight = w(ChevronRight)
export const IcChevronLeft = w(ChevronLeft)
export const IcChevronDown = w(ChevronDown)
export const IcChevronUp = w(ChevronUp)
export const IcPlus = w(Plus)
export const IcMinus = w(Minus)
export const IcClose = w(Xmark)
export const IcCheck = w(Check)
export const IcSearch = w(Magnifier)
export const IcLocate = w(LocationArrowFill)
export const IcBell = w(Bell)
export const IcHeart = w(Heart)
export const IcLogout = w(ArrowRightFromSquare)
export const IcMap = w(GeoFill)
export const IcBuilding = w(House)
export const IcFactory = w(Factory)
export const IcCrane = w(Hammer)
export const IcWorker = w(PersonWorker)
export const IcMegaphone = w(Megaphone)
export const IcInbox = w(Tray)
export const IcQueue = w(ListCheck)
export const IcUsers = w(Persons)
export const IcChart = w(ChartColumn)
export const IcCamera = w(Camera)
export const IcBook = w(Book)
export const IcDatabase = w(Database)
export const IcDoc = w(FileText)
export const IcPhone = w(Smartphone)
export const IcFlag = w(Flag)
export const IcHelp = w(CircleQuestion)
export const IcInfo = w(CircleInfo)
export const IcWarning = w(TriangleExclamation)
export const IcUpload = w(ArrowUpFromLine)
export const IcDownload = w(ArrowDownToLine)
export const IcRefresh = w(ArrowsRotateRight)
export const IcExternal = w(ArrowUpRightFromSquare)
export const IcGrid = w(LayoutCellsLarge)
export const IcList = w(ListUl)
export const IcSend = w(PaperPlane)
export const IcGlobe = w(Globe)
export const IcDrone = w(Plane)
export const IcClock = w(Clock)
export const IcKey = w(Key)
export const IcLayers = w(Layers)
export const IcFilter = w(Funnel)
export const IcSliders = w(Sliders)
export const IcBolt = w(ThunderboltFill)
export const IcEye = w(Eye)
export const IcTraffic = w(TrafficLight)
export const IcComment = w(Comment)
export const IcShield = w(ShieldCheck)
export const IcMore = w(Ellipsis)
export const IcCalendar = w(Calendar)
export const IcPicture = w(Picture)
export const IcBriefcase = w(Briefcase)
