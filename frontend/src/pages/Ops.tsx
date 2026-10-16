import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AuditList } from '../components/Audit'
import { LinkMark } from '../components/Hypothesis'
import MapView from '../components/MapView'
import { Faq, Hero, HeroTile, Highlight, LinkCols, PlatformIcon, Score, SectionHead, StatusTag, ZoneTag, fmtDT, fmtShort, platformMeta } from '../components/ui'
import type { Ad, AuditEvent, Platform, Zone } from '../data/types'
import { errorText } from '../lib/api'
import { HUMAN_KINDS, SYSTEM } from '../lib/audit'
import { adImage } from '../lib/media'
import { from } from '../lib/nav'
import { platformName } from '../lib/platform'
import { adSignals, model } from '../lib/risk'
import { isDemoAd } from '../lib/provenance'
import { useStore } from '../lib/store'
import { IcBuilding, IcClock, IcFlag, IcHelp, IcInbox, IcTraffic } from '../ui/icons'

const RECENT = 8
const WEEKS = 8
const BACK = from('Оперативная сводка', '/ops')

/** Понедельник недели даты (время браузера) */
const monday = (d: Date) => { const x = new Date(d.getFullYear(), d.getMonth(), d.getDate()); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x }
/** Номер недели по ISO 8601 */
function isoWeek(d: Date) {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()))
  t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7))
  return Math.ceil(((t.getTime() - Date.UTC(t.getUTCFullYear(), 0, 1)) / 864e5 + 1) / 7)
}
/** Публикации ленты с признаками продажи (маркеры словаря продажи или денег) по неделям: восемь недель, последняя —
 *  неделя самой свежей такой публикации (сбор ручной — «сегодня» могло ещё ничего не прийти) */
function salesByWeek(ads: Ad[]) {
  const dates = ads.filter((a) => { const x = adSignals(a); return x.sale.length + x.funds.length > 0 })
    .map((a) => new Date(a.date)).filter((d) => !Number.isNaN(d.getTime()))
  const last = monday(dates.length ? new Date(Math.max(...dates.map(Number))) : new Date())
  return Array.from({ length: WEEKS }, (_, i) => {
    const start = new Date(last); start.setDate(start.getDate() - 7 * (WEEKS - 1 - i))
    const end = new Date(start); end.setDate(end.getDate() + 7)
    return { week: isoWeek(start), start, n: dates.filter((d) => d >= start && d < end).length }
  })
}
/** Действия людей — без системных событий сбора и сверки данных и без гипотез, записанных ИИ (их отсекает и сервер) */
const human = (e: AuditEvent) => e.kind !== 'system' && e.kind !== 'data' && e.who !== SYSTEM

/** Оперативная сводка — шапка с показателями, карта с рейтингом прямо в шапке, ниже карусель свежей рекламы */
export default function Ops() {
  const { complexes, developers, ads, riskOf, adScoreOf, signals, mode, getAudit, revision } = useStore()
  const nav = useNavigate()
  const [zone, setZone] = useState<Zone | 'all'>('all')
  const [recent, setRecent] = useState<{ events?: AuditEvent[]; err?: string } | null>(null)
  const [ver, setVer] = useState(0)
  // последние действия аналитиков: виды действий людей и автора не «Система» отбирает сервер до лимита (иначе поток
  // гипотез ИИ при ручных добавлениях вытеснит людей из окна); здесь — та же проверка про запас; после изменений — заново
  useEffect(() => {
    if (mode !== 'api') return
    let alive = true
    getAudit({ kind: HUMAN_KINDS, human: true, limit: RECENT }).then(
      (xs) => { if (alive) setRecent({ events: xs.filter(human).slice(-RECENT).reverse() }) },
      (e: unknown) => { if (alive) setRecent((r) => ({ events: r?.events, err: errorText(e) })) },
    )
    return () => { alive = false }
  }, [mode, revision, ver]) // eslint-disable-line react-hooks/exhaustive-deps
  const strip = useRef<HTMLDivElement>(null)
  const scored = useMemo(() => complexes.map((c) => { const r = riskOf(c); return { c, r, zone: r.zone, score: r.score } }).sort((a, b) => b.score - a.score), [complexes, riskOf])
  const shown = scored.filter((x) => zone === 'all' || x.zone === zone)
  const count = (z: Zone) => scored.filter((x) => x.zone === z).length
  const queue = ads.filter((a) => a.status === 'new' || a.status === 'needs_check')
  const waiting = signals.filter((s) => s.status === 'new')
  const byPlat = (Object.keys(platformMeta) as Platform[]).map((p) => ({ p, n: ads.filter((a) => a.platform === p).length })).filter((x) => x.n).sort((a, b) => b.n - a.n)
  const maxP = Math.max(...byPlat.map((x) => x.n))
  const idle = scored.filter((x) => x.c.idleDays >= 30).sort((a, b) => b.c.idleDays - a.c.idleDays)
  const fresh = [...ads].sort((a, b) => b.date.localeCompare(a.date))
  const collected = ads.filter((a) => a.origin === 'collected').length
  const scroll = (d: number) => strip.current?.scrollBy({ left: d * strip.current.clientWidth, behavior: 'smooth' })
  const weeks = useMemo(() => salesByWeek(ads), [ads])
  const maxW = Math.max(1, ...weeks.map((w) => w.n))
  const [prevW, curW] = weeks.slice(-2)
  // изменение к предыдущей неделе — только когда есть с чем сравнить
  const delta = prevW.n ? Math.round(((curW.n - prevW.n) / prevW.n) * 100) : null

  return (
    <>
      <Hero crumb="Оперативная сводка" title={`Сводка по городу на ${new Date().toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })}`}
        info="Нажмите на плитку зоны — карта и рейтинг покажут только её объекты"
        tiles={<>
          <HeroTile icon={<IcBuilding size={32} />} label="На контроле" value={scored.length} onClick={() => setZone('all')} active={zone === 'all'} />
          <HeroTile icon={<IcTraffic size={32} />} label="Красная зона" value={count('red')} tone="red" onClick={() => setZone('red')} active={zone === 'red'} />
          <HeroTile icon={<IcTraffic size={32} />} label="Жёлтая зона" value={count('yellow')} tone="yellow" onClick={() => setZone('yellow')} active={zone === 'yellow'} />
          <HeroTile icon={<IcTraffic size={32} />} label="Зелёная зона" value={count('green')} tone="green" onClick={() => setZone('green')} active={zone === 'green'} />
          <HeroTile icon={<IcInbox size={32} />} label="Ждут решения" value={queue.length} tone="blue" to="/queue" />
          <HeroTile icon={<IcFlag size={32} />} label="Сигналы ждут решения" value={waiting.length} tone={waiting.length ? 'red' : undefined} to="/signals?status=new" />
          <HeroTile icon={<IcClock size={32} />} label="Стройка стоит 30+ дн." value={idle.length} to="/sites" />
        </>}>
        <div className="panel" style={{ gridTemplateColumns: 'minmax(0, 1fr) 380px' }}>
          <div className="ph" style={{ height: 460, borderRadius: 12 }}>
            <MapView items={shown} onSelect={(id) => nav(`/complexes/${id}`)} controls fit />
          </div>
          <div className="pr" style={{ padding: '20px 20px 12px', maxHeight: 460, overflow: 'auto' }}>
            <div className="row between" style={{ marginBottom: 6 }}><span className="t-h3">Рейтинг риска</span><span className="t-sm c2">{shown.length} ЖК · демо</span></div>
            {shown.map(({ c, r }, i) => (
              <Link key={c.id} to={`/complexes/${c.id}`} className="rrow">
                <span className="c3 num t-sm" style={{ width: 18 }}>{i + 1}</span>
                <img src={c.cover} alt="" />
                <span style={{ flex: 1, minWidth: 0 }}><span className="bold">{c.name}</span><span className="t-xs c2" style={{ display: 'block' }}>{c.district} · {c.stage}</span></span>
                <Score value={r.score} zone={r.zone} />
              </Link>
            ))}
          </div>
        </div>
      </Hero>

      {waiting.length > 0 && (
        <>
          <SectionHead title="Сигналы ждут решения" to="/signals?status=new" />
          <div className="sigmini">
            {waiting.slice(0, 5).map((s) => (
              <Link key={s.id} to={`/signals/${s.id}`} state={BACK}>
                <span className="row between"><span className="t-sm c2 num">{s.code}</span><Score value={s.score} zone={s.zone} /></span>
                <span className="bold clamp2">ЖК «{s.complexName}»</span>
                <span className="t-xs c2">{s.createdBy} · {fmtDT(s.createdAt)}</span>
              </Link>
            ))}
          </div>
        </>
      )}

      <SectionHead title="Демо-сценарии ТЗ §5" to="/method#cases" right={<span className="t-sm c2">вымышленные ЖК: на них показана модель риска</span>} />
      <div className="sigmini" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
        {model.cases.map((k) => {
          const c = complexes.find((x) => x.id === k.complexId)
          if (!c) return null
          const r = riskOf(c)
          return (
            <Link key={k.id} to={`/complexes/${c.id}?tab=risk`}>
              <span className="row between"><span className="tag dark">Сценарий {k.id}</span><Score value={r.score} zone={r.zone} /></span>
              <span className="bold clamp2">ЖК «{c.name}»</span>
              <span className="t-xs c2 clamp2">{k.title}</span>
              <span className="row" style={{ gap: 6 }}><span className="t-xs c2">ожидание:</span><ZoneTag zone={k.zone} /><span className="t-xs c2 num">{k.score}</span></span>
            </Link>
          )
        })}
      </div>

      {mode === 'api' && (
        <>
          <SectionHead title="Последние действия" right={<span className="t-sm c2">кто, что и когда — из журнала</span>} />
          {recent?.err && <div className="alog-err t-sm">Журнал не загрузился: {recent.err} <button className="link" onClick={() => setVer((v) => v + 1)}>Повторить</button></div>}
          {!recent ? <div className="t-sm c2 alist-empty">Загружаем журнал…</div>
            : recent.events && <div className="alog-grid"><AuditList events={recent.events} links={['ad', 'signal', 'complex']} from={BACK.from} empty="Действий аналитиков пока нет" /></div>}
        </>
      )}

      <SectionHead title="Свежая реклама" to="/ads" onPrev={() => scroll(-1)} onNext={() => scroll(1)} />
      <div className="hscroll" ref={strip}>
        {fresh.map((a) => {
          const c = complexes.find((x) => x.id === a.complexId); const s = adScoreOf(a); const img = adImage(a)
          return (
            <Link key={a.id} to={`/ads/${a.id}`} state={BACK} className="pcard">
              <div className="ph">{img ? <img src={img} alt="" /> : <div style={{ height: '100%', display: 'grid', placeItems: 'center' }}><PlatformIcon p={a.platform} size={44} /></div>}<div className="tl"><span className="tag white">{platformName(a.platform)}</span></div><div className="tr"><Score value={s.score} zone={s.zone} /></div></div>
              <span className="big">{c ? c.name : 'ЖК не определён'}</span>
              {c && <LinkMark a={a} label="привязка — гипотеза ИИ" style={{ alignSelf: 'flex-start' }} />}
              <span className="t-sm c2">{a.account} · {fmtShort(a.date)}</span>
              <span className="t-sm clamp2" style={{ lineHeight: '19px' }}><Highlight text={a.text} /></span>
              <span style={{ marginTop: 6 }}><StatusTag s={a.status} /></span>
            </Link>
          )
        })}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 1fr', gap: 16, marginTop: 48 }}>
        <div className="box">
          <div className="row between" style={{ marginBottom: 4 }}><h3 className="t-h3">Реклама продажи</h3>
            {delta === null ? <span className="tag gray">за неделю: {curW.n}</span>
              : <span className={`tag ${delta > 0 ? 'red' : delta < 0 ? 'green' : 'gray'}`}>{delta > 0 ? '+' : delta < 0 ? '−' : ''}{Math.abs(delta)}% к прошлой неделе</span>}</div>
          <p className="t-xs c2" style={{ marginBottom: 16 }}>Публикации ленты с признаками продажи по неделям (ISO), последняя — неделя с {curW.start.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })}</p>
          {/* столбик — белый на сером фоне блока (как дорожка «По площадкам»), последняя неделя — жёлтая из стилей .bars */}
          <div className="bars">{weeks.map((w, i) => <div key={+w.start} title={`Неделя с ${w.start.toLocaleDateString('ru-RU')}: ${w.n}`}><span className="num">{w.n}</span>
            <b style={{ height: Math.max(2, (w.n / maxW) * 120), background: i < weeks.length - 1 ? '#fff' : undefined }} /><span className="t-xs c3">н{w.week}</span></div>)}</div>
        </div>
        <div className="box">
          <h3 className="t-h3" style={{ marginBottom: 16 }}>По площадкам</h3>
          {byPlat.map(({ p, n }) => (
            <Link key={p} to="/ads" style={{ display: 'grid', gridTemplateColumns: '24px 84px 1fr 20px', gap: 10, alignItems: 'center', padding: '6px 0' }}>
              <PlatformIcon p={p} size={22} /><span className="t-sm">{platformMeta[p].name}</span>
              <div className="hbar" style={{ background: '#fff' }}><i style={{ width: `${(n / maxP) * 100}%`, background: 'var(--dark)' }} /></div><span className="t-sm num bold" style={{ textAlign: 'right' }}>{n}</span>
            </Link>
          ))}
        </div>
        <div className="box">
          <h3 className="t-h3" style={{ marginBottom: 8 }}>Стройка стоит</h3>
          {idle.map(({ c }) => (
            <Link key={c.id} to={`/complexes/${c.id}?tab=photos`} className="rrow" style={{ borderColor: 'rgba(0,0,0,.06)' }}>
              <img src={c.cover} alt="" /><span style={{ flex: 1 }} className="bold">{c.name}</span>
              <span className={`tag ${c.idleDays >= 90 ? 'red' : 'yellow'}`}>{c.idleDays} дн.</span>
            </Link>
          ))}
        </div>
      </div>

      <Faq title="Как читать сводку"
        items={[
          ['Почему объект в жёлтой зоне, хотя балл выше 60?', <>Красная зона ставится только при подтверждённых R1 и R2 одновременно. Высокий балл без этой пары — повод проверить, но не нарушение. Подробнее — в <Link className="link" to="/method">методике</Link>.</>],
          ['Что значит «Ждут решения»?', 'Публикации со статусом «Новая» или «Нужна проверка». Их разбирает аналитик в очереди: подтверждает нарушение, отклоняет или запрашивает сведения.'],
          ['Что значит «Сигналы ждут решения»?', 'Сигнальные карточки по ЖК, по которым аналитик ещё не решил: подтвердить или вернуть на доработку. Сигнал формируется из карточки ЖК и фиксирует риск и доказательства на момент создания.'],
          ['Откуда «Стройка стоит»?', <>Из фото-мониторинга: на снимках дрона или камеры нет изменений. Простой 30–89 дней — наблюдение вне балла: аналитик его видит, но балл не меняется. От 90 дней, если это подтверждают два снимка и больше с активностью «нет» или «низкая», — фактор R6 (+5 баллов); если таких снимков меньше двух, R6 помечается «Нужна проверка». R6 также ставится при просрочке срока сдачи на 180 дней и больше. Подробнее — в <Link className="link" to="/method">методике</Link>.</>],
          ['Как часто обновляются данные?', <>Публикации — подключёнными сборщиками (страница <Link className="link" to="/sources">«Источники»</Link>), через Telegram-бота заявок и при ручном вводе; открытые данные Homeportal и Казреестра — при импорте файла на той же странице. ЖК, снимки и перечень ГАСК в сводке — демонстрационные.</>],
        ]} />

      <LinkCols cols={[
        [['Объекты в красной зоне', '/complexes?zone=red', count('red')], ['Объекты в жёлтой зоне', '/complexes?zone=yellow', count('yellow')], ['Объекты в зелёной зоне', '/complexes?zone=green', count('green')]],
        [['Очередь проверки', '/queue', queue.length], ['Сигналы ждут решения', '/signals?status=new', waiting.length], ['Вся реклама', '/ads', ads.length], ['Добавлены аналитиком', '/ads?chip=manual', ads.filter((a) => !isDemoAd(a) && (a.origin === 'analyst' || a.source === 'manual')).length],
          ...(collected ? [['Собраны автоматически', '/ads?chip=collected', collected] as [string, string, number]] : [])],
        [['Фото-мониторинг', '/sites', idle.length], ['Застройщики', '/developers', developers.length], ['Отчёт для руководства', '/reports', 1]],
      ]} />
      <div className="row t-sm c2" style={{ gap: 6, marginTop: 24 }}><IcHelp size={16} />{collected
        ? 'ЖК и застройщики сводки — демонстрационные (вымышленные); публикации — демо-набор, добавленные вручную и собранные подключёнными источниками.'
        : 'ЖК, застройщики и публикации сводки — демонстрационные (вымышленные).'} Решение о нарушении принимает сотрудник.</div>
    </>
  )
}
