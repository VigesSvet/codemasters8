import { useEffect, useState, type ComponentType } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  IcBell, IcBook, IcBuilding, IcCamera, IcChart, IcCheck, IcChevronDown, IcChevronRight, IcChevronUp, IcDatabase, IcDoc, IcInbox, IcLogout, IcMegaphone, IcPlus, IcSearch, IcUsers,
} from '../ui/icons'
import { useStore } from '../lib/store'

type Item = { to: string; label: string; hint?: string; icon: ComponentType<{ size?: number }>; badge?: 'queue' | 'ads' }
const GROUPS: { key: string; label: string; items: Item[] }[] = [
  { key: 'obj', label: 'Объекты', items: [
    { to: '/complexes', label: 'Реестр ЖК', hint: 'Все строящиеся объекты города', icon: IcBuilding },
    { to: '/developers', label: 'Застройщики', hint: 'Портфель и история', icon: IcUsers },
    { to: '/sites', label: 'Фото-мониторинг', hint: 'Снимки с дрона и камер', icon: IcCamera },
  ] },
  { key: 'ads', label: 'Реклама', items: [
    { to: '/ads', label: 'Лента публикаций', hint: 'Instagram, TikTok, Krisha…', icon: IcMegaphone, badge: 'ads' },
    { to: '/queue', label: 'Очередь проверки', hint: 'Решения аналитика', icon: IcInbox, badge: 'queue' },
    { to: '/ads/new', label: 'Добавить публикацию', hint: 'Ручной ввод + OCR', icon: IcPlus },
  ] },
  { key: 'an', label: 'Аналитика', items: [
    { to: '/ops', label: 'Оперативная сводка', hint: 'Цифры за сегодня', icon: IcChart },
    { to: '/reports', label: 'Отчёты', hint: 'Еженедельная сводка', icon: IcDoc },
    { to: '/method', label: 'Методика скоринга', hint: 'Как считается риск', icon: IcBook },
    { to: '/sources', label: 'Источники данных', hint: 'Откуда берём данные', icon: IcDatabase },
  ] },
]

export default function Shell() {
  const { ads, toast } = useStore()
  const loc = useLocation()
  const nav = useNavigate()
  const [open, setOpen] = useState<string | null>(null)
  useEffect(() => setOpen(null), [loc.pathname])
  const counts = { ads: ads.length, queue: ads.filter((a) => a.status === 'new' || a.status === 'needs_check').length }
  const inGroup = (g: (typeof GROUPS)[number]) => g.items.some((i) => loc.pathname === i.to || (i.to !== '/ads' && loc.pathname.startsWith(i.to + '/')) || (i.to === '/ads' && /^\/ads\/(?!new)/.test(loc.pathname)))
  const isMap = loc.pathname === '/'

  return (
    <>
      <header className="head">
        <Link to="/" className="logo"><span className="mark">S</span>SAQ <span className="sub">Qurylys</span></Link>
        <nav className="nav">
          <NavLink to="/" end className={({ isActive }) => `nav-btn ${isActive ? 'active' : ''}`}>Карта</NavLink>
          {GROUPS.map((g) => (
            <div className="nav-item" key={g.key}>
              <button className={`nav-btn ${open === g.key ? 'open' : ''} ${inGroup(g) ? 'active' : ''}`} onClick={() => setOpen(open === g.key ? null : g.key)} aria-expanded={open === g.key}>
                {g.label}{g.key === 'ads' && counts.queue > 0 && <span className="badge">{counts.queue}</span>}
                {open === g.key ? <IcChevronUp size={18} /> : <IcChevronDown size={18} />}
              </button>
              {open === g.key && (
                <>
                  <div className="menu-scrim" onClick={() => setOpen(null)} />
                  <div className="menu fade" role="menu">
                    {g.items.map((i) => (
                      <Link key={i.to} to={i.to} role="menuitem">
                        <span className="ic"><i.icon size={20} /></span>
                        <span className="lbl">{i.label}<small>{i.hint}</small></span>
                        {i.badge && <span className={`badge ${i.badge === 'ads' ? 'gray' : ''}`}>{counts[i.badge]}</span>}
                        <IcChevronRight size={20} />
                      </Link>
                    ))}
                  </div>
                </>
              )}
            </div>
          ))}
          <NavLink to="/residents" className={({ isActive }) => `nav-btn ${isActive ? 'active' : ''}`}>Жителям</NavLink>
        </nav>
        <div className="head-right">
          <button className="icon-btn" aria-label="Поиск" onClick={() => nav('/complexes')}><IcSearch /></button>
          <button className="icon-btn" aria-label="Уведомления" onClick={() => nav('/queue')}><IcBell />{counts.queue > 0 && <span className="dot" />}</button>
          <div className="nav-item">
            <button className="user-btn" onClick={() => setOpen(open === 'me' ? null : 'me')}><span className="ava">ВН</span><IcChevronDown size={18} /></button>
            {open === 'me' && (
              <>
                <div className="menu-scrim" onClick={() => setOpen(null)} />
                <div className="menu fade" style={{ left: 'auto', right: 0, minWidth: 280 }}>
                  <div style={{ padding: '8px 24px 14px', borderBottom: '1px solid var(--line)', marginBottom: 6 }}>
                    <div className="bold">Нестерова Алия</div>
                    <div className="t-sm c2">Аналитик · ДЭР ВКО</div>
                  </div>
                  <Link to="/queue"><span className="ic"><IcCheck size={20} /></span><span className="lbl">Мои проверки</span><IcChevronRight size={20} /></Link>
                  <Link to="/login"><span className="ic"><IcLogout size={20} /></span><span className="lbl">Выйти</span><IcChevronRight size={20} /></Link>
                </div>
              </>
            )}
          </div>
        </div>
      </header>
      {isMap ? <Outlet /> : <main className="screen"><div className="wrap"><Outlet /></div></main>}
      {toast && <div className="snack"><span className="ok"><IcCheck size={14} strokeWidth={3} /></span>{toast}</div>}
    </>
  )
}
