import { useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import MapView from '../components/MapView'
import { Faq, Hero, HeroTile, Highlight, LinkCols, PlatformIcon, Score, SectionHead, StatusTag, fmtShort, platformMeta } from '../components/ui'
import type { Platform, Zone } from '../data/types'
import { scoreAd, scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcBuilding, IcClock, IcHelp, IcInbox, IcTraffic } from '../ui/icons'

const WEEKS = [3, 5, 4, 7, 6, 9, 8, 12]

/** Оперативная сводка — шапка с показателями, карта с рейтингом прямо в шапке, ниже карусель свежей рекламы */
export default function Ops() {
  const { complexes, ads } = useStore()
  const nav = useNavigate()
  const [zone, setZone] = useState<Zone | 'all'>('all')
  const strip = useRef<HTMLDivElement>(null)
  const scored = useMemo(() => complexes.map((c) => { const r = scoreComplex(c, ads); return { c, r, zone: r.zone, score: r.score } }).sort((a, b) => b.score - a.score), [complexes, ads])
  const shown = scored.filter((x) => zone === 'all' || x.zone === zone)
  const count = (z: Zone) => scored.filter((x) => x.zone === z).length
  const queue = ads.filter((a) => a.status === 'new' || a.status === 'needs_check')
  const byPlat = (Object.keys(platformMeta) as Platform[]).map((p) => ({ p, n: ads.filter((a) => a.platform === p).length })).filter((x) => x.n).sort((a, b) => b.n - a.n)
  const maxP = Math.max(...byPlat.map((x) => x.n))
  const idle = scored.filter((x) => x.c.idleDays >= 30).sort((a, b) => b.c.idleDays - a.c.idleDays)
  const fresh = [...ads].sort((a, b) => b.date.localeCompare(a.date))
  const scroll = (d: number) => strip.current?.scrollBy({ left: d * strip.current.clientWidth, behavior: 'smooth' })

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
          <HeroTile icon={<IcClock size={32} />} label="Стройка стоит 30+ дн." value={idle.length} to="/sites" />
        </>}>
        <div className="panel" style={{ gridTemplateColumns: 'minmax(0, 1fr) 380px' }}>
          <div className="ph" style={{ height: 460, borderRadius: 12 }}>
            <MapView items={shown} onSelect={(id) => nav(`/complexes/${id}`)} controls fit />
          </div>
          <div className="pr" style={{ padding: '20px 20px 12px', maxHeight: 460, overflow: 'auto' }}>
            <div className="row between" style={{ marginBottom: 6 }}><span className="t-h3">Рейтинг риска</span><span className="t-sm c2">{shown.length} ЖК</span></div>
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

      <SectionHead title="Свежая реклама" to="/ads" onPrev={() => scroll(-1)} onNext={() => scroll(1)} />
      <div className="hscroll" ref={strip}>
        {fresh.map((a) => {
          const c = complexes.find((x) => x.id === a.complexId); const s = scoreAd(a, c)
          return (
            <Link key={a.id} to={`/ads/${a.id}`} className="pcard">
              <div className="ph">{a.image ? <img src={a.image} alt="" /> : <div style={{ height: '100%', display: 'grid', placeItems: 'center' }}><PlatformIcon p={a.platform} size={44} /></div>}<div className="tl"><span className="tag white">{platformMeta[a.platform].name}</span></div><div className="tr"><Score value={s.score} zone={s.zone} /></div></div>
              <span className="big">{c ? c.name : 'ЖК не определён'}</span>
              <span className="t-sm c2">{a.account} · {fmtShort(a.date)}</span>
              <span className="t-sm clamp2" style={{ lineHeight: '19px' }}><Highlight text={a.text} /></span>
              <span style={{ marginTop: 6 }}><StatusTag s={a.status} /></span>
            </Link>
          )
        })}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 1fr', gap: 16, marginTop: 48 }}>
        <div className="box">
          <div className="row between" style={{ marginBottom: 20 }}><h3 className="t-h3">Реклама продажи</h3><span className="tag red">+50% за неделю</span></div>
          <div className="bars">{WEEKS.map((v, i) => <div key={i}><span className="num">{v}</span><b style={{ height: v * 11 }} /><span className="t-xs c3">н{34 + i}</span></div>)}</div>
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
          ['Откуда «Стройка стоит»?', 'Из фото-мониторинга: если на снимках дрона или камеры нет изменений 30 дней и больше. Это дополнительный модуль — он усиливает риск, но сам нарушением не считается.'],
          ['Как часто обновляются данные?', 'Публикации — при каждом сборе и ручном вводе, снимки — после облёта, перечень ГАСК — при его обновлении в Instagram.'],
        ]} />

      <LinkCols cols={[
        [['Объекты в красной зоне', '/complexes?zone=red', count('red')], ['Объекты в жёлтой зоне', '/complexes?zone=yellow', count('yellow')], ['Объекты в зелёной зоне', '/complexes?zone=green', count('green')]],
        [['Очередь проверки', '/queue', queue.length], ['Вся реклама', '/ads', ads.length], ['Добавлены вручную', '/ads', ads.filter((a) => a.source === 'manual').length]],
        [['Фото-мониторинг', '/sites', idle.length], ['Застройщики', '/developers', 7], ['Отчёт для руководства', '/reports', 1]],
      ]} />
      <div className="row t-sm c2" style={{ gap: 6, marginTop: 24 }}><IcHelp size={16} />Данные демонстрационные. Решение о нарушении принимает сотрудник.</div>
    </>
  )
}
