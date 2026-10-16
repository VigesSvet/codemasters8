// Быстрый поиск в шапке (ТЗ §7 «быстрый поиск ЖК»): ЖК демо-набора, объекты открытых данных, публикации, сигналы.
// «/» — к полю (если фокус не в поле ввода), ↑/↓ и Enter — выбор, Esc — закрыть. Нормализация и транслит — lib/search.ts.
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { adHit, codeQuery, finder, rankComplex, rankObject } from '../lib/find'
import { useRegistry } from '../lib/provenance'
import { norm, rankIn } from '../lib/search'
import { useStore } from '../lib/store'
import { IcClose, IcDatabase, IcFlag, IcSearch } from '../ui/icons'
import { SignalStatusTag } from './SignalPanel'
import { PlatformIcon, Score, StatusTag, adKey } from './ui'

interface Hit { key: string; to: string; icon?: ReactNode; title: ReactNode; sub?: ReactNode; end?: ReactNode; more?: boolean }
interface Group { title: string; hits: Hit[] }

const LIMIT = 5
const enc = encodeURIComponent
const editable = (t: EventTarget | null) => t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))
const more = (key: string, label: string, to: string): Hit => ({ key, to, title: label, more: true })

export default function QuickSearch() {
  const { mode, complexes, developers, ads, signals, riskOf } = useStore()
  const nav = useNavigate()
  const loc = useLocation()
  const [q, setQ] = useState('')
  // список открыт только на той странице, где его открыли: переход по ссылке или «назад» его закрывают
  const here = loc.pathname + loc.search
  const [openAt, setOpenAt] = useState<string | null>(null)
  const open = openAt === here
  const setOpen = (v: boolean) => setOpenAt(v ? here : null)
  const [active, setActive] = useState(0)
  // открытые данные грузим при первом обращении к поиску, а не на каждой странице
  const [touched, setTouched] = useState(false)
  const reg = useRegistry(touched)
  const root = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)

  const s = q.trim()
  const groups = useMemo<Group[]>(() => {
    if (!norm(s)) return []
    const code = codeQuery(s)
    const devOf = (id: string) => developers.find((d) => d.id === id)
    const cxOf = (id?: string | null) => complexes.find((c) => c.id === id)
    const out: Group[] = []

    // по тексту публикации — только от трёх букв, иначе находится почти всё
    const text = norm(s).length >= 3 ? norm(s) : null
    const all = reg.data ? [...reg.data.objects, ...reg.data.withoutCoords] : []
    // ранг: 2 — как есть, 1 — только по скелету (lib/search). В каждой группе, как в её списке (finder): есть точные
    // совпадения — только они, скелетные («Береке» ≈ «Барака», «Ертис» ≈ «Протозанова») — когда точных нет
    const best = <T,>(xs: T[], rank: (x: T) => number) => xs.filter(finder(s, xs, (_, x) => rank(x)))
    const cx = code ? [] : best(complexes, (c) => rankComplex(s, c, devOf(c.developerId)))
    if (cx.length) out.push({ title: 'Новостройки · демо-сценарии', hits: [
      ...cx.slice(0, LIMIT).map((c): Hit => {
        const r = riskOf(c), d = devOf(c.developerId)
        return { key: `c:${c.id}`, to: `/complexes/${c.id}`, icon: <img src={c.cover} alt="" />, title: `ЖК «${c.name}»`,
          sub: [`${c.address}, ${c.district}`, d && `${d.name} · БИН ${d.bin}`].filter(Boolean).join(' · '), end: <Score value={r.score} zone={r.zone} /> }
      }),
      ...(cx.length > LIMIT ? [more('c:more', `Все ${cx.length} в реестре новостроек`, `/complexes?q=${enc(s)}`)] : []),
    ] })

    const od = code ? [] : best(all, (o) => rankObject(s, o))
    if (od.length) out.push({ title: 'Открытые данные · без оценки риска', hits: [
      ...od.slice(0, LIMIT).map((o): Hit => ({ key: `o:${o.key}`, to: `/registry/${enc(o.key)}`, icon: <IcDatabase size={18} />, title: o.name,
        sub: [o.address || 'адрес источником не указан', o.developer && `${o.developer}${o.developerBin ? ` · БИН ${o.developerBin}` : ''}`].filter(Boolean).join(' · ') })),
      ...(od.length > LIMIT ? [more('o:more', `Все ${od.length} в открытых данных`, `/complexes?layer=open&q=${enc(s)}`)] : []),
    ] })

    // номер (AD-007, AD-M1A2…) — только точно; иначе номер, аккаунт, ЖК или фраза из текста
    const ad = code ? (code.kind === 'ad' ? ads.filter((a) => adHit(code.key, a.id)) : [])
      : best(ads, (a) => Math.max(rankIn(s, [adKey(a.id), a.account, cxOf(a.complexId)?.name], [a.account, cxOf(a.complexId)?.name]), text && norm(a.text).includes(text) ? 2 : 0))
    if (ad.length) out.push({ title: 'Публикации', hits: [
      ...ad.slice(0, LIMIT).map((a): Hit => {
        const c = cxOf(a.complexId)
        return { key: `a:${a.id}`, to: `/ads/${a.id}`, icon: <PlatformIcon p={a.platform} size={36} />, title: `${adKey(a.id)} · ${a.account}`,
          sub: `${c ? `ЖК «${c.name}»` : 'ЖК не определён'} · ${a.text}`, end: <StatusTag s={a.status} /> }
      }),
      ...(ad.length > LIMIT && !code ? [more('a:more', `Все ${ad.length} в ленте публикаций`, `/ads?q=${enc(s)}`)] : []),
    ] })

    const sig = code ? (code.kind === 'signal' ? signals.filter((x) => x.id === code.n) : []) : best(signals, (x) => rankIn(s, [x.code, x.complexName], [x.complexName]))
    if (sig.length) out.push({ title: 'Сигналы', hits: [
      ...sig.slice(0, LIMIT).map((x): Hit => ({ key: `s:${x.id}`, to: `/signals/${x.id}`, icon: <IcFlag size={18} />, title: `${x.code} · ЖК «${x.complexName}»`,
        sub: `балл ${x.score}`, end: <SignalStatusTag s={x.status} /> })),
      ...(sig.length > LIMIT ? [more('s:more', `Все сигналы (${sig.length})`, '/signals')] : []),
    ] })
    return out
  }, [s, complexes, developers, ads, signals, riskOf, reg.data])
  const flat = useMemo(() => groups.flatMap((g) => g.hits), [groups])

  // «/» с любой страницы (и та же клавиша в русской раскладке — «.»); не перехватываем ввод в полях и в открытом окне
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || !(e.key === '/' || (e.code === 'Slash' && !e.shiftKey))) return
      if (editable(e.target) || document.querySelector('.scrim')) return
      e.preventDefault()
      input.current?.focus()
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [])
  // клик мимо — закрыть
  useEffect(() => {
    if (!open) return
    const down = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpenAt(null) }
    document.addEventListener('mousedown', down)
    return () => document.removeEventListener('mousedown', down)
  }, [open])
  useEffect(() => { root.current?.querySelector('.qs-hit.on')?.scrollIntoView({ block: 'nearest' }) }, [active])

  const done = () => { setOpen(false); setQ(''); input.current?.blur() }
  const go = (h?: Hit) => { if (h) { nav(h.to); done() } }
  // Esc — и из поля, и с результата (Tab уводит фокус в список): первое закрывает список, второе очищает поле
  const onEsc = (e: React.KeyboardEvent) => {
    if (e.key !== 'Escape') return
    e.preventDefault()
    // сначала фокус (его onFocus открывает список), потом закрыть
    if (open && q) { input.current?.focus(); setOpen(false) } else done()
  }
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setOpen(true)
      if (flat.length) setActive((i) => (i + (e.key === 'ArrowDown' ? 1 : flat.length - 1)) % flat.length)
    } else if (e.key === 'Enter') { e.preventDefault(); go(flat[active]) }
  }

  let i = -1
  const loadingOd = touched && !reg.data && reg.loading
  // пусто по номеру — говорим про номер, а не про название
  const asked = codeQuery(s)
  const nothing = asked?.kind === 'signal'
    ? (mode === 'demo' ? 'Сигналы хранятся на сервере — в демо-режиме их нет.' : `Сигнала S-${String(asked.n).padStart(4, '0')} нет — все сигналы в разделе «Сигналы».`)
    : asked ? `Публикации с номером ${asked.key} нет в ленте — проверьте номер.`
      : <>Ничего не нашли по «{s}». Проверьте название или ищите по адресу, застройщику или БИН.{loadingOd && ' Открытые данные ещё загружаются…'}</>
  return (
    <div className="qsearch" ref={root} onKeyDown={onEsc} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false) }}>
      <label className="fsearch">
        <IcSearch size={18} />
        <input ref={input} value={q} placeholder="Найти ЖК, адрес, БИН" aria-label="Быстрый поиск"
          role="combobox" aria-expanded={open} aria-controls="qs-list" aria-autocomplete="list"
          aria-activedescendant={open && flat[active] ? `qs-${active}` : undefined}
          onFocus={() => { setTouched(true); setOpen(true) }}
          onChange={(e) => { setQ(e.target.value); setActive(0); setOpen(true) }} onKeyDown={onKey} />
        {q ? <button className="c3" onClick={() => { setQ(''); input.current?.focus() }} aria-label="Очистить"><IcClose size={16} /></button>
          : !open && <kbd title="Клавиша «/» — к поиску с любой страницы">/</kbd>}
      </label>
      {open && (
        // mousedown внутри списка не уводит фокус из поля: иначе список закрылся бы до клика
        <div className="qs-pop fade" id="qs-list" role="listbox" aria-label="Результаты поиска" onMouseDown={(e) => e.preventDefault()}>
          {!s ? (
            <div className="qs-help">
              <b>Быстрый поиск</b><br />
              ЖК — по названию (можно латиницей и без казахских букв: «Shygys», «Нур Алем»), адресу, району, застройщику или БИН.
              Публикации — по номеру (AD-001), аккаунту или тексту. Сигналы — по номеру (S-0001) или ЖК{mode === 'demo' && ' — когда подключён сервер'}.
            </div>
          ) : groups.length === 0 ? (
            <div className="qs-help">{nothing}</div>
          ) : groups.map((g) => (
            <div key={g.title} role="group" aria-label={g.title}>
              <div className="qs-g">{g.title}</div>
              {g.hits.map((h) => {
                const n = ++i
                return (
                  <Link key={h.key} id={`qs-${n}`} to={h.to} role="option" aria-selected={n === active}
                    className={`qs-hit ${h.more ? 'more' : ''} ${n === active ? 'on' : ''}`}
                    onMouseMove={() => setActive(n)} onClick={done}>
                    {h.icon && <span className="ic">{h.icon}</span>}
                    <span className="main"><span className="ttl">{h.title}</span>{h.sub && <span className="sub">{h.sub}</span>}</span>
                    {h.end}
                  </Link>
                )
              })}
            </div>
          ))}
          {s && groups.length > 0 && loadingOd && <div className="qs-help">Открытые данные ещё загружаются…</div>}
          <div className="qs-keys"><span><kbd>↑</kbd><kbd>↓</kbd> выбор</span><span><kbd>Enter</kbd> открыть</span><span><kbd>Esc</kbd> закрыть</span><span><kbd>/</kbd> поиск с любой страницы</span></div>
        </div>
      )}
    </div>
  )
}
