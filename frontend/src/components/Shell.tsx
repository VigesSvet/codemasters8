import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { IcBell, IcBuilding, IcCheck, IcChevronDown, IcChevronRight, IcClose, IcDatabase, IcFlag, IcLogout, IcPhone, IcQueue, IcRefresh, IcUsers, IcWarning } from '../ui/icons'
import { API } from '../lib/api'
import { fmtStamp } from '../lib/audit'
import { useStore } from '../lib/store'
import QuickSearch from './QuickSearch'

type Conn = 'api' | 'snapshot' | 'loading' | 'down'
const CONN: Record<Conn, { label: string; dot: string; title: string; text: string }> = {
  api: { label: 'Данные из API', dot: 'green', title: 'Работаем с сервером', text: 'Публикации, риск, сигналы и журнал берутся с бэкенда. Решения сохраняются на сервере с автором и временем.' },
  snapshot: { label: 'Офлайн-копия', dot: 'yellow', title: 'Сервер недоступен — офлайн-копия', text: 'Сервер не ответил при запуске — открыта копия стартового набора (shared/snapshot): реальные ЖК, застройщики и публичная реклама с анализом ИИ, риск и «Проверка по рекламе» — как их посчитал сервер. Только просмотр: решения, сверка, сигналы и журнал — с сервером.' },
  loading: { label: 'Подключение…', dot: 'gray', title: 'Подключаемся к серверу', text: 'Загружаем данные с бэкенда. Если он не ответит, откроется офлайн-копия стартового набора — только для просмотра.' },
  down: { label: 'Нет связи с сервером', dot: 'red', title: 'Нет связи с сервером', text: 'Последний запрос не дошёл до сервера. Несохранённые изменения отменены — проверьте связь и повторите действие.' },
}
/** «на 19.10.2026, выгружена 19.10.2026 03:35» — откуда копия */
const snapshotLine = (asOf: string | null, exportedAt: string | null) =>
  [asOf && `данные на ${asOf.split('-').reverse().join('.')}`, exportedAt && `выгружена ${fmtStamp(exportedAt)}`].filter(Boolean).join(', ')
const apiUrl = () => new URL(API, window.location.href).href

/** Шапка по образцу realty.yandex.ru: логотип · город · быстрый поиск · действия, ниже вкладки с чёрным подчёркиванием */
export default function Shell() {
  const { ads, toast, signals, user, logout, mode, snapshot, apiError, offline, clearError, reconnect, lastSync, stats } = useStore()
  const loc = useLocation()
  const nav = useNavigate()
  const [open, setOpen] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  useEffect(() => { setOpen(null) }, [loc.pathname])
  // новая страница — сверху; с якорем (/method#cases) — к нему: эффект оболочки идёт после эффектов страницы, страница уже отрисована
  useEffect(() => {
    const el = loc.hash && document.getElementById(decodeURIComponent(loc.hash.slice(1)))
    if (el) el.scrollIntoView({ block: 'start' })
    else window.scrollTo(0, 0)
  }, [loc.pathname, loc.hash])
  // связь — по итогу последнего запроса, а не по баннеру: «Скрыть» не делает её зелёной, успешный запрос — снимает красный
  const conn: Conn = mode === 'api' && offline ? 'down' : mode
  const check = () => { setChecking(true); reconnect().finally(() => setChecking(false)) }
  const here = encodeURIComponent(loc.pathname + loc.search)
  const queue = ads.filter((a) => a.status === 'new' || a.status === 'needs_check').length
  const sigNew = signals.filter((s) => s.status === 'new').length
  // объекты из рекламы ждут решения аналитика (сводка сервера; в офлайн-копии — из неё)
  const discNew = stats?.screening?.discovered?.new ?? 0
  const isMap = loc.pathname === '/'
  const adsActive = loc.pathname === '/ads' || /^\/ads\/(?!new)/.test(loc.pathname)

  // вкладки не помещаются на узком экране — лента прокручивается по горизонтали (колесо мыши тоже), края затухают,
  // активная вкладка подъезжает в видимую часть; «ещё» — вне ленты, чтобы её меню не обрезалось прокруткой
  const tabsRef = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ l: false, r: false })
  // не scrollIntoView: он прокрутил бы и окно, а у страницы своя прокрутка (вверх или к якорю)
  const revealActive = () => {
    const el = tabsRef.current
    const a = el?.querySelector<HTMLElement>('.ytab.active')
    if (!el || !a) return
    if (a.offsetLeft < el.scrollLeft) el.scrollLeft = a.offsetLeft - 24
    else if (a.offsetLeft + a.offsetWidth > el.scrollLeft + el.clientWidth) el.scrollLeft = a.offsetLeft + a.offsetWidth - el.clientWidth + 24
  }
  useEffect(() => {
    const el = tabsRef.current
    if (!el) return
    const upd = () => setEdges({ l: el.scrollLeft > 1, r: el.scrollLeft + el.clientWidth < el.scrollWidth - 1 })
    const wheel = (e: WheelEvent) => {
      if (el.scrollWidth <= el.clientWidth || Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return
      e.preventDefault()
      el.scrollLeft += e.deltaY
    }
    // ширина вкладок меняется после загрузки шрифтов и счётчиков — следим и за лентой, и за каждой вкладкой
    const ro = new ResizeObserver(() => { revealActive(); upd() })
    ro.observe(el)
    for (const c of el.children) ro.observe(c)
    el.addEventListener('scroll', upd, { passive: true })
    el.addEventListener('wheel', wheel, { passive: false })
    return () => { ro.disconnect(); el.removeEventListener('scroll', upd); el.removeEventListener('wheel', wheel) }
  }, [])
  useEffect(revealActive, [loc.pathname])

  const tab = (to: string, label: React.ReactNode, active?: boolean) => (
    <NavLink to={to} end={to === '/'} className={({ isActive }) => `ytab ${(active ?? isActive) ? 'active' : ''}`}>{label}</NavLink>
  )

  return (
    <>
      <header className="yhead">
        <div className="bar">
          <Link to="/" className="logo"><span className="mark"><IcBuilding /></span>SAQ Qurylys</Link>
          <span className="city">Усть-Каменогорск</span>
          <QuickSearch />
          <div className="head-right">
            <div className="nav-item">
              <button className={`conn ${conn}`} onClick={() => setOpen(open === 'conn' ? null : 'conn')} aria-expanded={open === 'conn'} title={CONN[conn].title}>
                <span className={`cdot ${CONN[conn].dot}`} />{CONN[conn].label}
              </button>
              {open === 'conn' && (
                <>
                  <div className="menu-scrim" onClick={() => setOpen(null)} />
                  <div className="pop conn-pop fade">
                    <h5 className="row" style={{ gap: 8 }}><span className={`cdot ${CONN[conn].dot}`} />{CONN[conn].title}</h5>
                    <p className="t-sm" style={{ lineHeight: '19px' }}>{CONN[conn].text}</p>
                    <div className="kv t-sm">
                      <span>Адрес API</span><span className="num" style={{ overflowWrap: 'anywhere' }}>{apiUrl()}</span>
                      <span>Синхронизация</span><span>{lastSync ? fmtStamp(lastSync) : 'ещё не было'}</span>
                      {conn === 'snapshot' && snapshot && <><span>Офлайн-копия</span><span>{snapshotLine(snapshot.asOf, snapshot.exportedAt) || 'стартовый набор'} · {snapshot.counts.complexes} ЖК, {snapshot.counts.ads} публ.</span></>}
                      {apiError && <><span>Ошибка</span><span>{apiError.message}{apiError.detail && <span className="c2"> · {apiError.detail}</span>}<span className="c3"> · {fmtStamp(apiError.at)}</span></span></>}
                    </div>
                    <button className="btn y m full" disabled={checking || mode === 'loading'} onClick={check}><IcRefresh size={18} />{checking ? 'Проверяем…' : 'Проверить связь'}</button>
                  </div>
                </>
              )}
            </div>
            <button className="icon-btn" aria-label="Уведомления" onClick={() => nav('/queue')}><IcBell />{queue > 0 && <span className="dot">{queue}</span>}</button>
            <Link to="/ads/new" className="btn g m">Добавить публикацию</Link>
            <Link to="/queue?who=me" className="btn g m">Моя очередь</Link>
            <div className="nav-item">
              <button className="user-btn" onClick={() => setOpen(open === 'me' ? null : 'me')} aria-label="Профиль"><span className="ava">{user?.short ?? '?'}</span><IcChevronDown size={16} /></button>
              {open === 'me' && (
                <>
                  <div className="menu-scrim" onClick={() => setOpen(null)} />
                  <div className="menu fade" style={{ left: 'auto', right: 0, minWidth: 280 }}>
                    <div style={{ padding: '8px 16px 12px', borderBottom: '1px solid var(--line)', marginBottom: 6 }}>
                      <div className="bold">{user?.name}</div>
                      <div className="t-sm c2">{user?.role}</div>
                      <div className="t-xs c3" style={{ marginTop: 4 }}>Вход пилота без пароля: от этого имени действия пишутся в журнал</div>
                    </div>
                    <Link to="/queue?who=me"><span className="ic"><IcCheck size={16} /></span><span className="lbl">Мои проверки</span><IcChevronRight size={16} /></Link>
                    <Link to="/signals?status=new"><span className="ic"><IcFlag size={16} /></span><span className="lbl">Сигналы ждут решения</span><IcChevronRight size={16} /></Link>
                    <Link to={`/login?next=${here}`}><span className="ic"><IcUsers size={16} /></span><span className="lbl">Сменить пользователя</span><IcChevronRight size={16} /></Link>
                    {/* без ?next: следующий сотрудник на этом месте не должен попасть на страницу предыдущего */}
                    <button onClick={() => { logout(); nav('/login', { replace: true }) }}><span className="ic"><IcLogout size={16} /></span><span className="lbl">Выйти</span><IcChevronRight size={16} /></button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
        <nav className="ytabs">
          <div ref={tabsRef} className={`ytabs-scroll${edges.l ? ' fade-l' : ''}${edges.r ? ' fade-r' : ''}`}>
            {tab('/', 'Карта')}
            {tab('/complexes', 'Новостройки', loc.pathname.startsWith('/complexes') || loc.pathname.startsWith('/registry/'))}
            {tab('/ads', <>Реклама<span className="badge gray">{ads.length}</span></>, adsActive)}
            {tab('/queue', <>Очередь проверки{queue > 0 && <span className="badge">{queue}</span>}</>)}
            {tab('/signals', <>Сигналы{sigNew > 0 && <span className="badge">{sigNew}</span>}</>)}
            {tab('/discovered', <span title="Объекты из рекламы: ЖК, которых нет в реестре и справочнике">Из рекламы{discNew > 0 && <span className="badge" style={{ marginLeft: 6 }}>{discNew}</span>}</span>)}
            {tab('/developers', 'Застройщики')}
            {tab('/sites', 'Фото-мониторинг')}
            {tab('/ops', 'Сводка')}
            {tab('/reports', 'Отчёты')}
            {tab('/method', 'Методика')}
          </div>
          <div className="nav-item ytabs-more">
            <button className={`ytab ${['/sources', '/residents'].includes(loc.pathname) ? 'active' : ''}`} onClick={() => setOpen(open === 'more' ? null : 'more')}>ещё<IcChevronDown size={16} /></button>
            {open === 'more' && (
              <>
                <div className="menu-scrim" onClick={() => setOpen(null)} />
                <div className="menu fade" style={{ top: '100%', left: 'auto', right: 0 }}>
                  <Link to="/sources"><span className="ic"><IcDatabase size={16} /></span><span className="lbl">Источники данных<small>Откуда берём данные</small></span><IcChevronRight size={16} /></Link>
                  <Link to="/residents"><span className="ic"><IcPhone size={16} /></span><span className="lbl">Жителям<small>Идея: push-уведомления</small></span><IcChevronRight size={16} /></Link>
                  <Link to="/ads/new"><span className="ic"><IcQueue size={16} /></span><span className="lbl">Ручной ввод публикации<small>Текст, ссылка, скриншот</small></span><IcChevronRight size={16} /></Link>
                </div>
              </>
            )}
          </div>
        </nav>
        <div className="sep" />
        {apiError && (
          <div className="api-banner" role="alert">
            <div className="wrap row">
              <IcWarning size={20} />
              <span className="txt"><b>{apiError.message}</b>{apiError.detail && <> · {apiError.detail}</>}<span className="c2"> · {fmtStamp(apiError.at)}</span></span>
              <button className="btn s w" disabled={checking} onClick={check}><IcRefresh size={14} />{checking ? 'Проверяем…' : 'Повторить'}</button>
              <button className="btn s g" onClick={clearError} aria-label="Скрыть сообщение"><IcClose size={14} />Скрыть</button>
            </div>
          </div>
        )}
      </header>
      {isMap ? <Outlet /> : (
        <>
          <main className="screen"><div className="wrap"><Outlet /></div></main>
          <footer className="foot"><div className="wrap">
            <div><h6>Объекты</h6><Link to="/complexes">Новостройки</Link><Link to="/discovered">Объекты из рекламы</Link><Link to="/developers">Застройщики</Link><Link to="/sites">Фото-мониторинг</Link><Link to="/" className="blue">На карте</Link></div>
            <div><h6>Реклама</h6><Link to="/ads">Лента публикаций</Link><Link to="/queue">Очередь проверки</Link><Link to="/signals">Сигнальные карточки</Link><Link to="/ads/new">Добавить вручную</Link></div>
            <div><h6>Аналитика</h6><Link to="/ops">Оперативная сводка</Link><Link to="/reports">Отчёты</Link><Link to="/method">Методика скоринга</Link><Link to="/sources">Источники данных</Link></div>
            <div><h6>О сервисе</h6><Link to="/residents">Жителям</Link><Link to="/complexes?layer=open">Открытые данные</Link><Link to="/method#cases">Сценарии A, B, C</Link></div>
            <div className="copy"><span>© 2026 SAQ Qurylys — цифровой помощник ДЭР ВКО</span><span>Данные реальные: открытые реестры и публичная реклама застройщиков и агентств (стартовый набор собран 18–19.10.2026), у каждой записи — источник и время сбора. «Проверка по рекламе» — предварительная автоматическая оценка, не вывод о нарушении; решение принимает сотрудник.</span></div>
          </div></footer>
        </>
      )}
      {toast && <div className="snack"><span className="ok"><IcCheck /></span>{toast}</div>}
    </>
  )
}
