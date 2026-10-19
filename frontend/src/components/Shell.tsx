import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { IcBell, IcCheck, IcChevronDown, IcChevronRight, IcClose, IcDatabase, IcFlag, IcLogout, IcPhone, IcQueue, IcRefresh, IcUsers, IcWarning } from '../ui/icons'
import { API } from '../lib/api'
import { fmtStamp } from '../lib/audit'
import { roleIn, useT, type I18n } from '../lib/i18n'
import { useStore } from '../lib/store'
import LangSwitch from './LangSwitch'
import QuickSearch from './QuickSearch'

type Conn = 'api' | 'snapshot' | 'loading' | 'down'
/** Цвет точки связи; подпись, заголовок и пояснение — в словаре (conn.<состояние>.label/title/text) */
const CONN_DOT: Record<Conn, string> = { api: 'green', snapshot: 'yellow', loading: 'gray', down: 'red' }
/** «данные на 19.10.2026, выгружена 19.10.2026 03:35» — откуда копия */
const snapshotLine = (t: I18n['t'], asOf: string | null, exportedAt: string | null) =>
  [asOf && t('conn.dataAsOf', { date: asOf.split('-').reverse().join('.') }), exportedAt && t('conn.exportedAt', { date: fmtStamp(exportedAt) })].filter(Boolean).join(', ')
const apiUrl = () => new URL(API, window.location.href).href

/** Шапка по образцу realty.yandex.ru: логотип · город · быстрый поиск · действия, ниже вкладки с чёрным подчёркиванием */
export default function Shell() {
  const { ads, toast, signals, user, logout, mode, snapshot, apiError, offline, clearError, reconnect, lastSync, stats } = useStore()
  const loc = useLocation()
  const nav = useNavigate()
  const i = useT()
  const { t } = i
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
  const C = { dot: CONN_DOT[conn], label: t(`conn.${conn}.label`), title: t(`conn.${conn}.title`), text: t(`conn.${conn}.text`) }
  const me = user ? roleIn(i, user) : null
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
          <Link to="/" className="logo"><img src="/logo.svg" alt="" className="mark" />SAQ Qurylys</Link>
          <QuickSearch />
          <div className="head-right">
            <LangSwitch />
            <div className="nav-item">
              <button className={`conn ${conn}`} onClick={() => setOpen(open === 'conn' ? null : 'conn')} aria-expanded={open === 'conn'} title={C.title}>
                <span className={`cdot ${C.dot}`} />{C.label}
              </button>
              {open === 'conn' && (
                <>
                  <div className="menu-scrim" onClick={() => setOpen(null)} />
                  <div className="pop conn-pop fade">
                    <h5 className="row" style={{ gap: 8 }}><span className={`cdot ${C.dot}`} />{C.title}</h5>
                    <p className="t-sm" style={{ lineHeight: '19px' }}>{C.text}</p>
                    <div className="kv t-sm">
                      <span>{t('conn.apiUrl')}</span><span className="num" style={{ overflowWrap: 'anywhere' }}>{apiUrl()}</span>
                      <span>{t('conn.sync')}</span><span>{lastSync ? fmtStamp(lastSync) : t('conn.never')}</span>
                      {conn === 'snapshot' && snapshot && <><span>{t('conn.snapshot.label')}</span><span>{snapshotLine(t, snapshot.asOf, snapshot.exportedAt) || t('conn.starter')} · {t('conn.snapCounts', { c: snapshot.counts.complexes, a: snapshot.counts.ads })}</span></>}
                      {apiError && <><span>{t('conn.error')}</span><span>{apiError.message}{apiError.detail && <span className="c2"> · {apiError.detail}</span>}<span className="c3"> · {fmtStamp(apiError.at)}</span></span></>}
                    </div>
                    <button className="btn y m full" disabled={checking || mode === 'loading'} onClick={check}><IcRefresh size={18} />{checking ? t('conn.checking') : t('conn.check')}</button>
                  </div>
                </>
              )}
            </div>
            <button className="icon-btn" aria-label={t('shell.notifications')} onClick={() => nav('/queue')}><IcBell />{queue > 0 && <span className="dot">{queue}</span>}</button>
            <Link to="/ads/new" className="btn g m">{t('shell.addAd')}</Link>
            <Link to="/queue?who=me" className="btn g m">{t('shell.myQueue')}</Link>
            <div className="nav-item">
              <button className="user-btn" onClick={() => setOpen(open === 'me' ? null : 'me')} aria-label={t('shell.profile')}><span className="ava">{me ? me.short : '?'}</span><IcChevronDown size={16} /></button>
              {open === 'me' && (
                <>
                  <div className="menu-scrim" onClick={() => setOpen(null)} />
                  <div className="menu fade" style={{ left: 'auto', right: 0, minWidth: 280 }}>
                    <div style={{ padding: '8px 16px 12px', borderBottom: '1px solid var(--line)', marginBottom: 6 }}>
                      <div className="bold">{me?.title}</div>
                      <div className="t-sm c2">{me?.dept}</div>
                      <div className="t-xs c3" style={{ marginTop: 4 }}>{t('shell.pilotNote')}</div>
                    </div>
                    <Link to="/queue?who=me"><span className="ic"><IcCheck size={16} /></span><span className="lbl">{t('shell.myChecks')}</span><IcChevronRight size={16} /></Link>
                    <Link to="/signals?status=new"><span className="ic"><IcFlag size={16} /></span><span className="lbl">{t('shell.signalsWaiting')}</span><IcChevronRight size={16} /></Link>
                    <Link to={`/login?next=${here}`}><span className="ic"><IcUsers size={16} /></span><span className="lbl">{t('shell.switchRole')}</span><IcChevronRight size={16} /></Link>
                    {/* без ?next: следующий сотрудник на этом месте не должен попасть на страницу предыдущего */}
                    <button onClick={() => { logout(); nav('/login', { replace: true }) }}><span className="ic"><IcLogout size={16} /></span><span className="lbl">{t('shell.logout')}</span><IcChevronRight size={16} /></button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
        <nav className="ytabs">
          <div ref={tabsRef} className={`ytabs-scroll${edges.l ? ' fade-l' : ''}${edges.r ? ' fade-r' : ''}`}>
            {tab('/', t('nav.map'))}
            {tab('/complexes', t('nav.complexes'), loc.pathname.startsWith('/complexes') || loc.pathname.startsWith('/registry/'))}
            {tab('/ads', <>{t('nav.ads')}<span className="badge gray">{ads.length}</span></>, adsActive)}
            {tab('/queue', <>{t('nav.queue')}{queue > 0 && <span className="badge">{queue}</span>}</>)}
            {tab('/signals', <>{t('nav.signals')}{sigNew > 0 && <span className="badge">{sigNew}</span>}</>)}
            {tab('/discovered', <span title={t('nav.discoveredHint')}>{t('nav.discovered')}{discNew > 0 && <span className="badge" style={{ marginLeft: 6 }}>{discNew}</span>}</span>)}
            {tab('/developers', t('nav.developers'))}
            {tab('/sites', t('nav.sites'))}
            {tab('/ops', t('nav.ops'))}
            {tab('/reports', t('nav.reports'))}
            {tab('/method', t('nav.method'))}
          </div>
          <div className="nav-item ytabs-more">
            <button className={`ytab ${['/sources', '/residents', '/sellers'].includes(loc.pathname) ? 'active' : ''}`} onClick={() => setOpen(open === 'more' ? null : 'more')}>{t('nav.more')}<IcChevronDown size={16} /></button>
            {open === 'more' && (
              <>
                <div className="menu-scrim" onClick={() => setOpen(null)} />
                <div className="menu fade" style={{ top: '100%', left: 'auto', right: 0 }}>
                  <Link to="/sources"><span className="ic"><IcDatabase size={16} /></span><span className="lbl">{t('nav.sources')}<small>{t('nav.sourcesSub')}</small></span><IcChevronRight size={16} /></Link>
                  <Link to="/residents"><span className="ic"><IcPhone size={16} /></span><span className="lbl">{t('nav.residents')}<small>{t('nav.residentsSub')}</small></span><IcChevronRight size={16} /></Link>
                  <Link to="/sellers"><span className="ic"><IcUsers size={16} /></span><span className="lbl">{t('nav.sellers')}<small>{t('nav.sellersSub')}</small></span><IcChevronRight size={16} /></Link>
                  <Link to="/ads/new"><span className="ic"><IcQueue size={16} /></span><span className="lbl">{t('nav.adNew')}<small>{t('nav.adNewSub')}</small></span><IcChevronRight size={16} /></Link>
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
              <button className="btn s w" disabled={checking} onClick={check}><IcRefresh size={14} />{checking ? t('conn.checking') : t('common.retry')}</button>
              <button className="btn s g" onClick={clearError} aria-label={t('conn.hideMsg')}><IcClose size={14} />{t('conn.hide')}</button>
            </div>
          </div>
        )}
      </header>
      {isMap ? <Outlet /> : (
        <>
          <main className="screen"><div className="wrap"><Outlet /></div></main>
          <footer className="foot"><div className="wrap">
            <div><h6>{t('foot.objects')}</h6><Link to="/complexes">{t('nav.complexes')}</Link><Link to="/discovered">{t('foot.discovered')}</Link><Link to="/developers">{t('nav.developers')}</Link><Link to="/sites">{t('nav.sites')}</Link><Link to="/" className="blue">{t('common.onMap')}</Link></div>
            <div><h6>{t('foot.ads')}</h6><Link to="/ads">{t('foot.feed')}</Link><Link to="/queue">{t('nav.queue')}</Link><Link to="/signals">{t('foot.signalCards')}</Link><Link to="/ads/new">{t('foot.addManual')}</Link></div>
            <div><h6>{t('foot.analytics')}</h6><Link to="/ops">{t('foot.ops')}</Link><Link to="/reports">{t('nav.reports')}</Link><Link to="/method">{t('foot.method')}</Link><Link to="/sources">{t('nav.sources')}</Link></div>
            <div><h6>{t('foot.about')}</h6><Link to="/residents">{t('nav.residents')}</Link><Link to="/complexes?layer=open">{t('foot.openData')}</Link><Link to="/method#cases">{t('foot.cases')}</Link></div>
            <div className="copy"><span>{t('foot.copy')}</span><span>{t('foot.note')}</span></div>
          </div></footer>
        </>
      )}
      {toast && <div className="snack"><span className="ok"><IcCheck /></span>{toast}</div>}
    </>
  )
}
