import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { IcBell, IcBuilding, IcCheck, IcChevronDown, IcChevronRight, IcDatabase, IcLogout, IcPhone, IcQueue } from '../ui/icons'
import { useStore } from '../lib/store'

/** Шапка по образцу realty.yandex.ru: логотип · город · действия, ниже вкладки с чёрным подчёркиванием */
export default function Shell() {
  const { ads, toast } = useStore()
  const loc = useLocation()
  const nav = useNavigate()
  const [open, setOpen] = useState<string | null>(null)
  useEffect(() => { setOpen(null); window.scrollTo(0, 0) }, [loc.pathname])
  const queue = ads.filter((a) => a.status === 'new' || a.status === 'needs_check').length
  const isMap = loc.pathname === '/'
  const adsActive = loc.pathname === '/ads' || /^\/ads\/(?!new)/.test(loc.pathname)

  const tab = (to: string, label: React.ReactNode, active?: boolean) => (
    <NavLink to={to} end={to === '/'} className={({ isActive }) => `ytab ${(active ?? isActive) ? 'active' : ''}`}>{label}</NavLink>
  )

  return (
    <>
      <header className="yhead">
        <div className="bar">
          <Link to="/" className="logo"><span className="mark"><IcBuilding /></span>SAQ Qurylys</Link>
          <button className="city">Усть-Каменогорск<IcChevronDown size={16} /></button>
          <div className="head-right">
            <button className="icon-btn" aria-label="Уведомления" onClick={() => nav('/queue')}><IcBell />{queue > 0 && <span className="dot">{queue}</span>}</button>
            <Link to="/ads/new" className="btn g m">Добавить публикацию</Link>
            <Link to="/queue" className="btn g m">Моя очередь</Link>
            <div className="nav-item">
              <button className="user-btn" onClick={() => setOpen(open === 'me' ? null : 'me')} aria-label="Профиль"><span className="ava">ВН</span><IcChevronDown size={16} /></button>
              {open === 'me' && (
                <>
                  <div className="menu-scrim" onClick={() => setOpen(null)} />
                  <div className="menu fade" style={{ left: 'auto', right: 0, minWidth: 280 }}>
                    <div style={{ padding: '8px 16px 12px', borderBottom: '1px solid var(--line)', marginBottom: 6 }}>
                      <div className="bold">Нестерова Алия</div>
                      <div className="t-sm c2">Аналитик · ДЭР ВКО</div>
                    </div>
                    <Link to="/queue"><span className="ic"><IcCheck size={16} /></span><span className="lbl">Мои проверки</span><IcChevronRight size={16} /></Link>
                    <Link to="/login"><span className="ic"><IcLogout size={16} /></span><span className="lbl">Выйти</span><IcChevronRight size={16} /></Link>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
        <nav className="ytabs">
          {tab('/', 'Карта')}
          {tab('/complexes', 'Новостройки')}
          {tab('/ads', <>Реклама<span className="badge gray">{ads.length}</span></>, adsActive)}
          {tab('/queue', <>Очередь проверки{queue > 0 && <span className="badge">{queue}</span>}</>)}
          {tab('/developers', 'Застройщики')}
          {tab('/sites', 'Фото-мониторинг')}
          {tab('/ops', 'Сводка')}
          {tab('/reports', 'Отчёты')}
          {tab('/method', 'Методика')}
          <div className="nav-item">
            <button className={`ytab ${['/sources', '/residents'].includes(loc.pathname) ? 'active' : ''}`} onClick={() => setOpen(open === 'more' ? null : 'more')}>ещё<IcChevronDown size={16} /></button>
            {open === 'more' && (
              <>
                <div className="menu-scrim" onClick={() => setOpen(null)} />
                <div className="menu fade" style={{ top: '100%' }}>
                  <Link to="/sources"><span className="ic"><IcDatabase size={16} /></span><span className="lbl">Источники данных<small>Откуда берём данные</small></span><IcChevronRight size={16} /></Link>
                  <Link to="/residents"><span className="ic"><IcPhone size={16} /></span><span className="lbl">Жителям<small>Идея: push-уведомления</small></span><IcChevronRight size={16} /></Link>
                  <Link to="/ads/new"><span className="ic"><IcQueue size={16} /></span><span className="lbl">Ручной ввод публикации<small>Текст, ссылка, скриншот</small></span><IcChevronRight size={16} /></Link>
                </div>
              </>
            )}
          </div>
        </nav>
        <div className="sep" />
      </header>
      {isMap ? <Outlet /> : (
        <>
          <main className="screen"><div className="wrap"><Outlet /></div></main>
          <footer className="foot"><div className="wrap">
            <div><h6>Объекты</h6><Link to="/complexes">Новостройки</Link><Link to="/developers">Застройщики</Link><Link to="/sites">Фото-мониторинг</Link><Link to="/" className="blue">На карте</Link></div>
            <div><h6>Реклама</h6><Link to="/ads">Лента публикаций</Link><Link to="/queue">Очередь проверки</Link><Link to="/ads/new">Добавить вручную</Link></div>
            <div><h6>Аналитика</h6><Link to="/ops">Оперативная сводка</Link><Link to="/reports">Отчёты</Link><Link to="/method">Методика скоринга</Link><Link to="/sources">Источники данных</Link></div>
            <div><h6>О сервисе</h6><Link to="/residents">Жителям</Link><a>ДЭР ВКО · ГАСК</a><a>Поддержка аналитиков</a></div>
            <div className="copy"><span>© 2026 SAQ Qurylys — цифровой помощник ДЭР ВКО</span><span>Данные демонстрационные. Решение о нарушении принимает сотрудник.</span></div>
          </div></footer>
        </>
      )}
      {toast && <div className="snack"><span className="ok"><IcCheck /></span>{toast}</div>}
    </>
  )
}
