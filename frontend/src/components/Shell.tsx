import { useEffect, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  Header, HeaderGlobalAction, HeaderGlobalBar, HeaderName, SideNav, SideNavDivider, SideNavItems, SideNavLink, SkipToContent, Theme, ToastNotification,
} from '@carbon/react'
import {
  Analytics, Asleep, Building, Bullhorn, Camera, DataBase, Education, Enterprise, Light, ListChecked, Logout, Map, Mobile, Notification, Report, Search, AddAlt,
} from '@carbon/icons-react'
import { useStore } from '../lib/store'

const NAV = [
  { to: '/', label: 'Карта объектов', icon: Map, end: true },
  { to: '/ops', label: 'Оперативная сводка', icon: Analytics },
  { to: '/complexes', label: 'Реестр ЖК', icon: Building },
  { to: '/ads', label: 'Мониторинг рекламы', icon: Bullhorn, count: 'ads' as const },
  { to: '/queue', label: 'Очередь проверки', icon: ListChecked, count: 'queue' as const },
  { to: '/developers', label: 'Застройщики', icon: Enterprise },
  null,
  { to: '/ads/new', label: 'Добавить публикацию', icon: AddAlt },
  { to: '/sites', label: 'Фото-мониторинг', icon: Camera },
  { to: '/method', label: 'Методика скоринга', icon: Education },
  { to: '/sources', label: 'Источники данных', icon: DataBase },
  { to: '/reports', label: 'Отчёты', icon: Report },
  { to: '/residents', label: 'Жителям (идея)', icon: Mobile },
]

function useClock() {
  const [now, setNow] = useState(new Date())
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 30000); return () => clearInterval(t) }, [])
  return now
}

export default function Shell() {
  const { ads, theme, setTheme, toast } = useStore()
  const loc = useLocation()
  const nav = useNavigate()
  const now = useClock()
  const counts = { ads: ads.length, queue: ads.filter((a) => a.status === 'new' || a.status === 'needs_check').length }
  const isActive = (to: string, end?: boolean) => (end ? loc.pathname === to : loc.pathname === to || (to !== '/ads' && loc.pathname.startsWith(to + '/')) || (to === '/ads' && /^\/ads\/(?!new)/.test(loc.pathname)))

  return (
    <Theme theme={theme}>
      <Theme theme="g100">
      <Header aria-label="SAQ Qurylys">
        <SkipToContent />
        <HeaderName as={Link} to="/" prefix="ДЭР ВКО">SAQ Qurylys</HeaderName>
        <HeaderGlobalBar>
          <span className="header-date">
            {now.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }).toUpperCase()} · {now.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })} ALMT · Өскемен
          </span>
          <HeaderGlobalAction aria-label="Поиск"><Search size={20} /></HeaderGlobalAction>
          <HeaderGlobalAction aria-label={theme === 'g10' ? 'Тёмная тема' : 'Светлая тема'} onClick={() => setTheme(theme === 'g10' ? 'g100' : 'g10')}>
            {theme === 'g10' ? <Asleep size={20} /> : <Light size={20} />}
          </HeaderGlobalAction>
          <HeaderGlobalAction aria-label="Уведомления" onClick={() => nav('/queue')}><Notification size={20} /></HeaderGlobalAction>
          <HeaderGlobalAction aria-label="Выйти" tooltipAlignment="end" onClick={() => nav('/login')}><Logout size={20} /></HeaderGlobalAction>
        </HeaderGlobalBar>
      </Header>
      </Theme>
        <SideNav aria-label="Навигация" isFixedNav expanded isChildOfHeader={false}>
          <SideNavItems>
            {NAV.map((n, i) => n === null ? <SideNavDivider key={i} /> : (
              <SideNavLink key={n.to} as={Link} to={n.to} renderIcon={n.icon} isActive={isActive(n.to, n.end)}>
                <span style={{ display: 'flex', width: '100%', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                  {n.label}
                  {n.count && <span className={`count ${n.count === 'queue' ? 'alert' : ''}`}>{counts[n.count]}</span>}
                </span>
              </SideNavLink>
            ))}
          </SideNavItems>
          <div className="side-footer">
            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
              <span className="avatar" style={{ width: 32, height: 32 }}>ВН</span>
              <div>
                <div className="cell-title">Нестерова А.</div>
                <div className="cell-sub">Аналитик · ДЭР ВКО</div>
              </div>
            </div>
          </div>
        </SideNav>
      <main className="app-content" id="main-content">
        <Outlet />
      </main>
      {toast && (
        <div style={{ position: 'fixed', right: 16, top: 64, zIndex: 9000 }}>
          <ToastNotification kind={toast.kind} title={toast.title} subtitle="Действие записано в журнал" timeout={3000} lowContrast />
        </div>
      )}
    </Theme>
  )
}
