// «Ситуационный центр» (/tv, после входа) — экран для ТВ в духе диспетчерской: один плотный экран без слайд-шоу и украшений.
// Слева — тёмная карта города с метками ЖК, справа — таблица показателей (контроль, очередь, сигналы, простой), «Проверка по
// рекламе» и зоны формального риска тонкими полосами долей; внизу — лента, которая каждые 12 с переключается между рекламой
// с предложением оплаты, сигналами, ждущими решения, и стоящими стройками. Цвет — только у статусов. Часы, «данные на ЧЧ:ММ»,
// обновление данных раз в 60 с; ←/→ — лента, пробел — пауза, курсор прячется. Стили — styles/tv.css
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import MapView, { type MapItem } from '../components/MapView'
import { PlatformIcon } from '../components/ui'
import type { Zone } from '../data/types'
import { isPaymentOffer } from '../lib/hotspots'
import { useT, type Key } from '../lib/i18n'
import { isAssessed, linkConfirmed } from '../lib/risk'
import { DISCLAIMER_SHORT, SCREENING_ORDER, countByStatus, hasPoint, statusOf } from '../lib/screening'
import { useStore } from '../lib/store'
import '../styles/tv.css'

const SLIDE_MS = 12000
const REFRESH_MS = 60000
const IDLE_DAYS = 30
const FEEDS: Key[] = ['tv.s.offers', 'tv.s.signals', 'tv.s.idle']
const ZONES: Zone[] = ['red', 'yellow', 'green']
const ROWS = 5
/** Цитата для экрана: одной строкой, без эмодзи */
const plain = (s: string) => s.replace(/[\p{Extended_Pictographic}\u{FE0F}\u{200D}]/gu, '').replace(/\s+/g, ' ').trim()

/** Следующая лента по кругу: d = +1 вперёд, −1 назад */
const step = (i: number, d: number, n = FEEDS.length) => (((i + d) % n) + n) % n
const num = (n: number) => n.toLocaleString('ru-RU')

/** Полоса долей и строки легенды: цвет — только у метки статуса */
function Split({ title, parts, note }: { title: string; parts: { key: string; cls: string; label: string; n: number }[]; note?: string }) {
  const total = parts.reduce((s, p) => s + p.n, 0)
  return (
    <section className="tv-sec">
      <h3>{title}<span>{num(total)}</span></h3>
      <div className="tv-split">{parts.filter((p) => p.n > 0).map((p) => <i key={p.key} className={p.cls} style={{ flexGrow: p.n }} />)}</div>
      <ul className="tv-legend">
        {parts.map((p) => <li key={p.key} className={p.n ? '' : 'zero'}><span className={`tv-m ${p.cls}`} />{p.label}<b>{num(p.n)}</b></li>)}
      </ul>
      {note && <p className="tv-note">{note}</p>}
    </section>
  )
}

export default function Tv() {
  const { complexes, ads, signals, stats, riskOf, lastSync, mode, refreshAds, refreshSignals, refreshScreening } = useStore()
  const i = useT()
  const { t, tr, locale } = i
  const [feed, setFeed] = useState(0)
  const [paused, setPaused] = useState(false)
  const [now, setNow] = useState(() => new Date())
  const [idle, setIdle] = useState(false)

  useEffect(() => { const h = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(h) }, [])
  useEffect(() => {
    if (paused) return
    const h = setTimeout(() => setFeed((x) => step(x, 1)), SLIDE_MS)
    return () => clearTimeout(h)
  }, [feed, paused])
  // данные — раз в минуту, без баннеров ошибок (на ТВ их некому закрыть)
  useEffect(() => {
    if (mode !== 'api') return
    const h = setInterval(() => { void refreshAds({ quiet: true }); void refreshSignals(); void refreshScreening() }, REFRESH_MS)
    return () => clearInterval(h)
  }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') setFeed((x) => step(x, 1))
      else if (e.key === 'ArrowLeft') setFeed((x) => step(x, -1))
      else if (e.key === ' ' || e.code === 'Space') { e.preventDefault(); setPaused((p) => !p) }
    }
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [])
  // курсор прячется через 2,5 с без движения мыши
  useEffect(() => {
    let h = setTimeout(() => setIdle(true), 2500)
    const move = () => { setIdle(false); clearTimeout(h); h = setTimeout(() => setIdle(true), 2500) }
    window.addEventListener('mousemove', move)
    return () => { clearTimeout(h); window.removeEventListener('mousemove', move) }
  }, [])

  const scored = useMemo(() => complexes.map((c) => { const r = riskOf(c); return { c, r, off: !isAssessed(r), zone: r.zone, score: r.score, screen: statusOf(c) } }), [complexes, riskOf])
  const items: MapItem[] = scored.filter((x) => hasPoint(x.c))
  const zone = (z: Zone) => scored.filter((x) => !x.off && x.zone === z).length
  const off = scored.filter((x) => x.off).length
  const cx = stats?.screening?.complexes ?? countByStatus(complexes, statusOf)
  const queue = ads.filter((a) => a.status === 'new' || a.status === 'needs_check').length
  const waiting = signals.filter((s) => s.status === 'new').sort((a, b) => b.createdAt.localeCompare(a.createdAt))
  const offers = useMemo(() => ads.filter(isPaymentOffer).sort((a, b) => b.date.localeCompare(a.date)), [ads])
  const stalled = scored.filter((x) => x.c.idleDays >= IDLE_DAYS).sort((a, b) => b.c.idleDays - a.c.idleDays)
  const name = (id?: string) => complexes.find((c) => c.id === id)?.name
  const hhmm = (d: Date) => d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
  const full = () => { if (document.fullscreenElement) void document.exitFullscreen(); else void document.documentElement.requestFullscreen?.() }
  const counts = [offers.length, waiting.length, stalled.length]

  const kpis: { label: string; value: number; cls?: string }[] = [
    { label: t('tv.onControl'), value: scored.length },
    { label: t('tv.queue'), value: queue, cls: queue ? 'warn' : '' },
    { label: t('tv.s.signals'), value: waiting.length, cls: waiting.length ? 'alert' : '' },
    { label: t('tv.idleN'), value: stalled.length, cls: stalled.length ? 'warn' : '' },
    { label: t('tv.ads'), value: ads.length },
    { label: t('tv.offersN'), value: offers.length },
  ]

  return (
    <div className={`tv ${idle ? 'idle' : ''}`}>
      <header className="tv-top">
        <Link to="/" className="tv-logo"><img src="/logo.svg" alt="" />SAQ Qurylys</Link>
        <span className="tv-sep" />
        <span className="tv-name">{t('tv.title')} · {t('common.city')}</span>
        <span className="tv-sync">{lastSync ? t('tv.dataAt', { t: hhmm(new Date(lastSync)) }) : t('common.loading')}{paused && ` · ${t('tv.paused')}`}</span>
        <time className="tv-clock">{now.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time>
        <button type="button" className="tv-fs" onClick={full}>{t('tv.full')}</button>
      </header>

      <main className="tv-main">
        <div className="tv-map"><MapView items={items} interactive={false} labels fit zoom={12} /></div>

        <aside className="tv-side">
          <dl className="tv-kpis">
            {kpis.map((k) => <div key={k.label} className={k.cls}><dt>{k.label}</dt><dd>{num(k.value)}</dd></div>)}
          </dl>
          <Split title={t('scr.title')} note={tr(DISCLAIMER_SHORT)} parts={SCREENING_ORDER.map((k) => ({ key: k, cls: `s-${k}`, label: t(`scr.filter.${k}`), n: cx[k] }))} />
          <Split title={t('tv.zones')} parts={[...ZONES.map((z) => ({ key: z, cls: `z-${z}`, label: t(`zone.name.${z}`), n: zone(z) })), { key: 'off', cls: 'z-off', label: t('tv.notAssessed'), n: off }]} />
        </aside>

        <section className="tv-feed">
          <nav className="tv-tabs" role="tablist">
            {FEEDS.map((f, n) => (
              <button key={f} type="button" role="tab" aria-selected={n === feed} className={n === feed ? 'on' : ''} onClick={() => setFeed(n)}>
                {t(f)}<span>{num(counts[n])}</span>
              </button>
            ))}
            <span className="tv-keys">{t('tv.keys')}</span>
          </nav>
          <div className={`tv-prog ${paused ? 'paused' : ''}`} key={`${feed}-${paused}`} style={{ animationDuration: `${SLIDE_MS}ms` }} />

          <table className="tv-table" key={feed}>
            <tbody>
              {feed === 0 && (offers.length ? offers.slice(0, ROWS).map((a) => {
                // ЖК — привязка публикации, а без неё — лучший кандидат ИИ; и то и другое до решения аналитика — гипотеза
                const cand = name(a.complexId) ?? a.llm?.complexCandidates?.[0]?.complexName
                return (
                  <tr key={a.id}>
                    <td className="d">{i.dayMonth(a.date)}</td>
                    <td className="p"><span className="tv-pf"><PlatformIcon p={a.platform} size={18} /><span>{a.account}</span></span></td>
                    <td className="q">{plain(a.text).slice(0, 160)}</td>
                    <td className="c">{cand ?? t('tv.noComplex')}{cand && !(a.complexId && linkConfirmed(a)) && <em>{t('tv.aiLink')}</em>}</td>
                  </tr>
                )
              }) : <tr><td className="e">{t('tv.noOffers')}</td></tr>)}
              {feed === 1 && (waiting.length ? waiting.slice(0, ROWS).map((s) => (
                <tr key={s.id}>
                  <td className="d">{s.code}</td>
                  <td className="c"><span className={`tv-m z-${s.zone}`} />{s.complexName}</td>
                  <td className="n">{t('tv.score', { n: s.score })}</td>
                  <td className="d r">{t('tv.created', { d: i.dayMonth(s.createdAt) })}</td>
                </tr>
              )) : <tr><td className="e">{t('tv.noSignals')}</td></tr>)}
              {feed === 2 && (stalled.length ? stalled.slice(0, ROWS).map((x) => (
                <tr key={x.c.id}>
                  <td className="c"><span className={`tv-m z-${x.off ? 'off' : x.zone}`} />{x.c.name}</td>
                  <td className="q">{x.c.address}</td>
                  <td className="n r">{t('tv.idleDays', { n: x.c.idleDays })}</td>
                </tr>
              )) : <tr><td className="e">{t('tv.noIdle')}</td></tr>)}
            </tbody>
          </table>
        </section>
      </main>
    </div>
  )
}
