import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Compare from '../components/Compare'
import { Faq, Hero, HeroTile, SectionHead, Seg, fmtDate, fmtShort, plural } from '../components/ui'
import type { Tri } from '../data/types'
import { factorDef, model } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcCalendar, IcCamera, IcCheck, IcClock, IcClose, IcCrane, IcDrone, IcHelp, IcUpload } from '../ui/icons'

type F = 'all' | 'idle' | 'ok' | 'none'
const T = model.thresholds
// Заявок на облёт и загрузки снимков в MVP нет — кнопки неактивны и подписаны, без имитации отправки (ТЗ §4)
const SHOTS = ['снимок', 'снимка', 'снимков'] as const
const NOT_YET = 'После пилота: в демо ничего не отправляется и не загружается — снимки берутся из демо-набора'

/** Фото-мониторинг — шапка с показателями и сравнением снимков в главном блоке, ниже карусель площадок */
export default function Sites() {
  const { complexes } = useStore()
  const strip = useRef<HTMLDivElement>(null)
  const all = complexes.filter((c) => c.stage !== 'введён').sort((a, b) => b.idleDays - a.idleDays)
  const [f, setF] = useState<F>('all')
  const test: Record<F, (c: (typeof all)[number]) => boolean> = { all: () => true, idle: (c) => c.idleDays >= 30, ok: (c) => c.photos.length > 0 && c.idleDays < 30, none: (c) => c.photos.length === 0 }
  const list = all.filter(test[f])
  const [sel, setSel] = useState(all.find((c) => c.photos.length > 2 && c.idleDays === 0)?.id ?? all[0].id)
  const c = complexes.find((x) => x.id === sel)!
  const last = c.photos[c.photos.length - 1]
  const signs = last ? ([['Ограждение', last.fence], ['Мусорный контейнер', last.bins], ['Мойка колёс', last.wheelWash]] as [string, Tri | undefined][]) : []
  const scroll = (d: number) => strip.current?.scrollBy({ left: d * strip.current.clientWidth, behavior: 'smooth' })
  // самый свежий снимок среди строящихся — реальная дата из данных, а не график облётов
  const latest = all.flatMap((x) => x.photos.map((p) => ({ x, date: p.date }))).sort((a, b) => b.date.localeCompare(a.date))[0]

  return (
    <>
      <Hero crumb="Фото-мониторинг" title="Фото-мониторинг площадок"
        info="Дополнительный модуль: снимки дрона и камер сравниваются во времени. Заявки на облёт и загрузка снимков — после пилота, в демо ничего не отправляется"
        actions={<><button className="btn g m" style={{ background: '#fff' }} disabled title={NOT_YET}><IcDrone />Заявка на облёт · после пилота</button><button className="btn g m" style={{ background: '#fff' }} disabled title={NOT_YET}><IcUpload />Загрузить снимки · после пилота</button></>}
        tiles={<>
          <HeroTile icon={<IcCrane size={32} />} label="Строящихся объектов" value={all.length} onClick={() => setF('all')} active={f === 'all'} />
          <HeroTile icon={<IcClock size={32} />} label="Стройка стоит 30+ дн." value={all.filter(test.idle).length} tone="red" onClick={() => setF('idle')} active={f === 'idle'} />
          <HeroTile icon={<IcCheck size={32} />} label="Работы идут" value={all.filter(test.ok).length} tone="green" onClick={() => setF('ok')} active={f === 'ok'} />
          <HeroTile icon={<IcCamera size={32} />} label="Без снимков" value={all.filter(test.none).length} tone="yellow" onClick={() => setF('none')} active={f === 'none'} />
          <HeroTile icon={<IcCamera size={32} />} label="Снимков всего" value={all.reduce((s, x) => s + x.photos.length, 0)} onClick={() => setF('all')} />
          <HeroTile icon={<IcCalendar size={32} />} label="Последний снимок" value={latest ? fmtShort(latest.date) : '—'} tone="blue" onClick={() => latest && setSel(latest.x.id)} />
        </>}>
        <div className="panel fade" key={c.id} style={{ gridTemplateColumns: 'minmax(0, 1fr) 360px' }}>
          <div style={{ padding: 8 }}>
            {c.photos.length >= 2 ? <Compare before={c.photos[0]} after={last} /> : c.photos.length === 1 ? <div className="ph" style={{ aspectRatio: '16/9', borderRadius: 16 }}><img src={c.photos[0].src} alt="" /></div> : <div className="drop" style={{ aspectRatio: '16/9' }}><div><IcCamera /><div className="bold" style={{ marginTop: 8 }}>Снимков ещё нет</div><div className="t-sm c2">Простой по снимкам не оценить. Загрузка снимков и заявки на облёт — после пилота</div></div></div>}
          </div>
          <div className="pr">
            <Link to={`/complexes/${c.id}?tab=photos`} className="t-h2 link" style={{ color: 'var(--text)' }}>ЖК «{c.name}»</Link>
            <span className="t-sm c2" style={{ marginTop: 4 }}>{c.address} · {c.stage}, {c.progress}%</span>
            <div style={{ margin: '18px 0' }}>
              {c.idleDays >= 30
                ? <><span className="big-num" style={{ color: c.idleDays >= 90 ? 'var(--red)' : 'var(--amber)' }}>{c.idleDays}</span><span className="c2"> дней без изменений</span></>
                : <><span className="big-num" style={{ color: 'var(--green)' }}>{c.photos.length}</span><span className="c2"> {plural(c.photos.length, SHOTS)}{c.photos.length ? ' · работы идут' : ''}</span></>}
            </div>
            {last && <div className="kv" style={{ gridTemplateColumns: '1fr auto' }}>
              <span>Последний снимок</span><span>{fmtDate(last.date)}</span>
              <span>Источник</span><span>{last.source}</span>
              {signs.map(([k, v]) => [<span key={k}>{k}</span>, <span key={k + 'v'} className="row" style={{ gap: 4, color: v === 'yes' ? 'var(--green)' : v === 'no' ? 'var(--red)' : 'var(--text-3)' }}>{v === 'yes' ? <IcCheck size={14} /> : v === 'no' ? <IcClose size={14} /> : <IcHelp size={14} />}{v === 'yes' ? 'есть' : v === 'no' ? 'нет' : 'не видно'}</span>])}
            </div>}
            <Link to={`/complexes/${c.id}`} className="btn d full" style={{ marginTop: 'auto', paddingTop: 0 }}>Карточка объекта</Link>
          </div>
        </div>
      </Hero>

      <SectionHead title={`Площадки · ${list.length}`} onPrev={() => scroll(-1)} onNext={() => scroll(1)}
        right={<Seg value={f} onChange={setF} items={[['all', 'Все'], ['idle', 'Стоят'], ['ok', 'Идут'], ['none', 'Без снимков']]} />} />
      <div className="hscroll" ref={strip}>
        {list.map((x) => (
          <button key={x.id} className={`pcard ${sel === x.id ? 'sel' : ''}`} onClick={() => { setSel(x.id); window.scrollTo({ top: 0, behavior: 'smooth' }) }} style={{ textAlign: 'left' }}>
            <div className="ph"><img src={x.photos.at(-1)?.src ?? x.cover} alt="" /><div className="tl"><span className="tag white">{x.photos.length} {plural(x.photos.length, SHOTS)}</span></div></div>
            <span className="big">{x.name}</span>
            <span className="t-sm c2">{x.photos.at(-1) ? `последний ${fmtDate(x.photos.at(-1)!.date)}` : 'снимков нет'}</span>
            <span style={{ marginTop: 6 }}><span className={`tag ${x.photos.length === 0 ? 'gray' : x.idleDays >= 90 ? 'red' : x.idleDays >= 30 ? 'yellow' : 'green'}`}>{x.photos.length === 0 ? 'нет данных' : x.idleDays ? `стоит ${x.idleDays} дн.` : 'работы идут'}</span></span>
          </button>
        ))}
      </div>

      {c.photos.length > 0 && <>
        <SectionHead title={`Хронология · ЖК «${c.name}»`} />
        <div className="gcards" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 16 }}>
          {c.photos.map((p, i) => (
            <div key={i} className="pcard">
              <div className="ph"><img src={p.src} alt="" /><div className="tl"><span className="tag white">{p.source}</span></div></div>
              <span className="big" style={{ fontSize: 17 }}>{fmtDate(p.date)}</span>
              <span className="t-sm c2">{p.note ?? '—'}</span>
              <span style={{ marginTop: 4 }}><span className={`tag ${p.activity === 'есть' ? 'green' : p.activity === 'низкая' ? 'yellow' : 'red'}`}>активность: {p.activity}</span></span>
            </div>
          ))}
        </div>
      </>}

      <Faq title="Как работает фото-мониторинг"
        items={[
          ['Откуда снимки?', 'Сейчас — из демо-набора: у каждого снимка дата и источник. В рабочей версии — дроны управления цифровизации ВКО, стационарные камеры на площадках и фото с выезда (дата — из файла); загрузка появится после пилота.'],
          ['Как система понимает, что стройка стоит?', `Дни без изменений — сведения по объекту с отметкой активности на каждом снимке; в демо они заданы в наборе данных, автоматического распознавания снимков в MVP нет. От ${T.idleShortDays} дней ставится отметка «стоит». Простой от ${T.idleDays} дней, подтверждённый не менее чем ${T.idlePhotosMin} датированными снимками с активностью «нет» или «низкая», — фактор R6 (+${factorDef('R6')?.weight}). ${T.idleShortDays}–${T.idleDays - 1} дней — наблюдение вне балла: за площадкой следят, но баллы не начисляются.`],
          ['Зачем ограждение, контейнер и мойка колёс?', 'Это обязательные элементы строительной площадки по требованиям ГАСК. Их отсутствие — повод для выезда, но в риск-скоринг ТЗ они не входят.'],
          ['Нужно ли управлять дроном из системы?', 'Нет. В MVP — только сравнение снимков во времени. Заявку на облёт в управление цифровизации можно будет отправить после пилота; сейчас она не отправляется.'],
        ]} />
    </>
  )
}
