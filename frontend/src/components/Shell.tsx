import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Bell, BookOpen, Building2, Camera, Database, FileText, Inbox, LogOut, Map, Megaphone, PlusSquare, Search, Smartphone, Users } from 'lucide-react'
import { useStore, type Theme } from '../lib/store'

const themes: { id: Theme; name: string; sw: string }[] = [
  { id: 'fortis', name: 'Fortis', sw: 'linear-gradient(90deg,#f2f0eb 50%,#111 50%)' },
  { id: 'siera', name: 'Siera', sw: 'linear-gradient(90deg,#0f100e 50%,#c9a66b 50%)' },
  { id: 'banda', name: 'Banda', sw: 'linear-gradient(90deg,#ece4d6 50%,#6b6a3f 50%)' },
]

const titles: Record<string, string> = {
  '': 'Карта города', complexes: 'Реестр ЖК', ads: 'Лента рекламы', queue: 'Очередь проверки', developers: 'Застройщики',
  sites: 'Фото-мониторинг', method: 'Методика скоринга', sources: 'Источники данных', residents: 'Жителям', reports: 'Отчёты', new: 'Новая публикация',
}

export default function Shell() {
  const { ads, theme, setTheme, toast } = useStore()
  const loc = useLocation()
  const nav = useNavigate()
  const queue = ads.filter((a) => a.status === 'new' || a.status === 'needs_check').length
  const parts = loc.pathname.split('/').filter(Boolean)

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <div className="brand-mark">SAQ <em>Qurylys</em></div>
          <span className="label">Мониторинг долевого строительства · Өскемен</span>
        </div>
        <nav className="nav">
          <div className="nav-group label">Работа</div>
          <NavLink to="/" end><Map size={16} />Карта города</NavLink>
          <NavLink to="/complexes"><Building2 size={16} />Реестр ЖК</NavLink>
          <NavLink to="/ads" end><Megaphone size={16} />Лента рекламы<span className="count">{ads.length}</span></NavLink>
          <NavLink to="/queue"><Inbox size={16} />Очередь проверки<span className="count alert">{queue}</span></NavLink>
          <NavLink to="/developers"><Users size={16} />Застройщики</NavLink>
          <div className="nav-group label">Модули</div>
          <NavLink to="/ads/new"><PlusSquare size={16} />Добавить публикацию</NavLink>
          <NavLink to="/sites"><Camera size={16} />Фото-мониторинг</NavLink>
          <NavLink to="/method"><BookOpen size={16} />Методика скоринга</NavLink>
          <NavLink to="/sources"><Database size={16} />Источники данных</NavLink>
          <NavLink to="/reports"><FileText size={16} />Отчёты</NavLink>
          <NavLink to="/residents"><Smartphone size={16} />Жителям <span className="count">β</span></NavLink>
        </nav>
        <div className="side-foot">
          <div className="label">Вариант дизайна</div>
          <div className="themes">
            {themes.map((t) => (
              <button key={t.id} className={theme === t.id ? 'on' : ''} onClick={() => setTheme(t.id)}>
                <span className="sw" style={{ background: t.sw }} />{t.name}
              </button>
            ))}
          </div>
          <div className="user">
            <div className="avatar">ВН</div>
            <div style={{ lineHeight: 1.2 }}>
              <div style={{ fontWeight: 500 }}>Нестерова А.</div>
              <div className="label" style={{ fontSize: 10 }}>Аналитик · ДЭР ВКО</div>
            </div>
            <button className="btn icon sm ghost" style={{ marginLeft: 'auto', width: 28 }} onClick={() => nav('/login')} title="Выйти"><LogOut size={14} /></button>
          </div>
        </div>
      </aside>
      <div className="main">
        <div className="topbar">
          <div className="crumbs label">
            <span>SAQ</span>
            {parts.length === 0 ? <span style={{ color: 'var(--ink)' }}>{titles['']}</span> : parts.map((p, i) => (
              <span key={i} style={{ color: i === parts.length - 1 ? 'var(--ink)' : undefined }}>{titles[p] ?? p.toUpperCase()}</span>
            ))}
          </div>
          <label className="search">
            <Search size={14} className="muted" />
            <input placeholder="ЖК, застройщик, БИН, аккаунт…" />
            <kbd>⌘K</kbd>
          </label>
          <button className="btn icon ghost" title="Уведомления" style={{ position: 'relative' }}>
            <Bell size={16} />
            <span style={{ position: 'absolute', top: 7, right: 8, width: 7, height: 7, borderRadius: 9, background: 'var(--red)' }} />
          </button>
        </div>
        <Outlet />
      </div>
      {toast && (
        <div className="fade-in" style={{ position: 'fixed', bottom: 24, left: '50%', transform: 'translateX(-50%)', background: 'var(--ink)', color: 'var(--bg)', padding: '12px 20px', zIndex: 100, fontSize: 13 }}>
          {toast}
        </div>
      )}
    </div>
  )
}
