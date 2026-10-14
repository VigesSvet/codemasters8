import { useCallback, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import MapView from '../components/MapView'
import { Cell, Highlight, PlatformIcon, Score, StatusTag, Toggle, ZoneTag, fmtShort } from '../components/ui'
import { developers } from '../data/demo'
import type { Tri, Zone } from '../data/types'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcBuilding, IcCamera, IcCheck, IcChevronLeft, IcChevronRight, IcClose, IcGlobe, IcHelp, IcInfo, IcMegaphone, IcPlus, IcUsers } from '../ui/icons'

type F = Zone | 'all' | 'check'

/** Главная — повторяет раскладку taxi.yandex.ru: карта, слева стопка карточек, справа промо и кнопки карты */
export default function Dashboard() {
  const { complexes, ads } = useStore()
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const [f, setF] = useState<F>('all')
  const [sel, setSel] = useState<string | undefined>()
  const [labels, setLabels] = useState(false)
  const [withAds, setWithAds] = useState(false)
  const [idle, setIdle] = useState(false)

  const scored = useMemo(() => complexes.map((c) => { const r = scoreComplex(c, ads); return { c, r, zone: r.zone, score: r.score } }).sort((a, b) => b.score - a.score), [complexes, ads])
  const visible = useMemo(() => scored.filter((x) =>
    (f === 'all' || (f === 'check' ? x.r.needsCheck : x.zone === f))
    && (!q || `${x.c.name} ${x.c.address} ${developers.find((d) => d.id === x.c.developerId)?.bin}`.toLowerCase().includes(q.toLowerCase()))
    && (!withAds || ads.some((a) => a.complexId === x.c.id && a.status !== 'dismissed'))
    && (!idle || x.c.idleDays >= 30)), [scored, f, q, withAds, idle, ads])
  const n = (z: F) => scored.filter((x) => (z === 'all' ? true : z === 'check' ? x.r.needsCheck : x.zone === z)).length
  const queue = ads.filter((a) => a.status === 'new' || a.status === 'needs_check').length
  const s = scored.find((x) => x.c.id === sel)
  const onSelect = useCallback((id: string) => setSel(id), [])
  const dev = s && developers.find((d) => d.id === s.c.developerId)
  const linked = s ? ads.filter((a) => a.complexId === s.c.id) : []

  const tiles: [F, string, Zone | 'all'][] = [['all', 'Все', 'all'], ['red', 'Красная', 'red'], ['yellow', 'Жёлтая', 'yellow'], ['green', 'Зелёная', 'green'], ['check', 'Проверка', 'yellow']]
  const basisTile = (name: string, v: Tri) => (
    <div className="tile" style={{ minHeight: 0, background: 'var(--bg)' }}>
      <div className="pic" style={{ height: 28, marginBottom: 2 }}>
        <span className={`zone-pic ${v === 'yes' ? 'green' : v === 'no' ? 'red' : 'all'}`} style={{ width: 28, height: 28, borderRadius: 9 }}>
          {v === 'yes' ? <IcCheck size={16} strokeWidth={3} /> : v === 'no' ? <IcClose size={16} strokeWidth={3} /> : <IcHelp size={16} />}
        </span>
      </div>
      <div className="nm c2" style={{ fontSize: 13 }}>{name}</div>
      <div className="vl" style={{ fontSize: 15 }}>{v === 'yes' ? 'Есть' : v === 'no' ? 'Нет' : 'Нет сведений'}</div>
    </div>
  )

  return (
    <div className="mapscreen">
      <MapView items={visible} selected={sel} onSelect={onSelect} controls labels={labels} padLeft={380} />

      <div className="left">
        {!s ? (
          <div className="scroll">
            <div className="card">
              <div className="field">
                <span className="mk"><span className="mk ring" /></span>
                <div className="body"><label>Город</label><span style={{ fontSize: 17 }}>Усть-Каменогорск</span></div>
              </div>
              <div className="field">
                <span className="mk"><span className="mk ring dark" /></span>
                <div className="body"><input placeholder="Найти ЖК, адрес или БИН" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Поиск" /></div>
                {q && <button onClick={() => setQ('')} className="c3" aria-label="Очистить"><IcClose size={18} /></button>}
              </div>
              <Link to="/ads/new" className="field" style={{ minHeight: 64 }}>
                <span className="mk c2"><IcPlus /></span>
                <div className="body" style={{ fontSize: 17 }}>Добавить публикацию</div>
              </Link>
            </div>

            <div className="card">
              <div className="tiles">
                {tiles.map(([k, label, z]) => (
                  <button key={k} className={`tile ${f === k ? 'on' : ''}`} onClick={() => setF(k)}>
                    {k === 'all' && <Link to="/method" className="i" onClick={(e) => e.stopPropagation()} aria-label="Как считается риск"><IcInfo size={18} /></Link>}
                    <span className="pic"><span className={`zone-pic ${z}`}>{k === 'check' ? <IcHelp size={20} /> : <IcBuilding size={20} />}</span></span>
                    <span className="nm">{label}</span>
                    <span className="vl num">{n(k)}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="card">
              {visible.length === 0 && <div className="empty">Ничего не найдено</div>}
              {visible.map(({ c, r }) => (
                <Cell key={c.id} onClick={() => setSel(c.id)} inset
                  icon={<img className="th" src={c.cover} alt="" style={{ width: 48, height: 48, borderRadius: 12 }} />}
                  title={c.name} sub={`${c.address} · ${c.stage}${r.needsCheck ? ' · нужна проверка' : ''}`}
                  end={<Score value={r.score} zone={r.zone} />} />
              ))}
            </div>

            <div className="card">
              <Cell title="Подписи на карте" end={<Toggle on={labels} onChange={setLabels} label="Подписи" />} />
              <Cell title="Только с рекламой" end={<Toggle on={withAds} onChange={setWithAds} label="Только с рекламой" />} />
              <Cell title="Стройка стоит 30+ дней" end={<Toggle on={idle} onChange={setIdle} label="Простой" />} />
            </div>
          </div>
        ) : (
          <div className="scroll fade" key={s.c.id}>
            <div className="card">
              <Cell onClick={() => setSel(undefined)} icon={<IcChevronLeft />} title="Все объекты" />
              <div className="ph" style={{ height: 190, margin: '0 8px', borderRadius: 18 }}>
                <img src={s.c.cover} alt="" />
                <div className="tl"><ZoneTag zone={s.zone} /></div>
              </div>
              <div className="card-pad">
                <div className="row between" style={{ alignItems: 'flex-start' }}>
                  <div>
                    <h2 className="t-h2">ЖК «{s.c.name}»</h2>
                    <div className="c2 t-sm" style={{ marginTop: 4 }}>{s.c.address} · {s.c.district}</div>
                  </div>
                  <Score value={s.score} zone={s.zone} />
                </div>
                <p className="t-sm" style={{ marginTop: 12, lineHeight: '20px' }}>{s.r.summary}</p>
              </div>
            </div>
            <div className="card">
              <div className="tiles" style={{ gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                {basisTile('Гарантия ЕО', s.c.basis.guarantee)}
                {basisTile('Разрешение МИО', s.c.basis.mio)}
                {basisTile('Банк (БВУ)', s.c.basis.bvu)}
                {basisTile('Перечень ГАСК', s.c.inPublishedList)}
              </div>
            </div>
            <div className="card">
              <Cell icon={<span className="ico"><IcMegaphone size={20} /></span>} title="Реклама" sub={linked.length ? `${linked.length} публ. · последняя ${fmtShort(linked[0].date)}` : 'Не найдено'} chevron to={`/complexes/${s.c.id}?tab=ads`} inset />
              <Cell icon={<span className="ico"><IcCamera size={20} /></span>} title="Фото-мониторинг" sub={s.c.idleDays ? `Нет активности ${s.c.idleDays} дн.` : `${s.c.photos.length} снимков · стройка идёт`} chevron to={`/complexes/${s.c.id}?tab=photos`} inset />
              <Cell icon={<span className="ico"><IcUsers size={20} /></span>} title={dev?.name.replace('ТОО ', '') ?? '—'} sub={`БИН ${dev?.bin}`} chevron to="/developers" inset />
            </div>
            {linked.slice(0, 2).map((a) => (
              <Link key={a.id} to={`/ads/${a.id}`} className="card card-pad" style={{ display: 'block' }}>
                <div className="row between"><div className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={24} /><span className="t-sm c2">{a.account}</span></div><StatusTag s={a.status} /></div>
                <p className="t-sm clamp3" style={{ marginTop: 10, lineHeight: '20px' }}><Highlight text={a.text} /></p>
              </Link>
            ))}
          </div>
        )}
        <div className="dock">
          {s
            ? <button className="btn y full" onClick={() => nav(`/complexes/${s.c.id}`)}>Открыть карточку объекта</button>
            : <button className="btn y full" onClick={() => nav('/queue')}>Открыть очередь проверки · {queue}</button>}
        </div>
      </div>

      <Link to="/queue" className="promo" style={{ left: 392, top: 104, height: 96 }}>
        <div className="tx">
          <div className="big num">{queue} ждут решения<IcChevronRight size={22} /></div>
          <div className="sm">Публикации в очереди аналитика</div>
        </div>
        <img src="/img/site-i.jpg" alt="" style={{ width: 110, borderRadius: '0 24px 24px 0' }} />
      </Link>

      <Link to="/ops" className="promo blue" style={{ right: 16, top: 104, width: 310 }}>
        <div className="tx">
          <div className="bold" style={{ fontSize: 17, lineHeight: '21px' }}>Оперативная сводка<br />по городу</div>
          <span className="mini">Открыть</span>
        </div>
        <span style={{ marginLeft: 'auto', marginRight: 18, width: 64, height: 64, borderRadius: 20, background: 'rgba(255,255,255,.6)', display: 'grid', placeItems: 'center' }}><IcBuilding size={34} /></span>
      </Link>
      <Link to="/complexes?zone=red" className="promo" style={{ right: 16, top: 220, width: 310 }}>
        <div className="tx" style={{ flex: 1 }}>
          <div className="big" style={{ fontSize: 22 }}>Красная зона<IcChevronRight size={20} /></div>
          <div className="sm">{n('red')} объекта — реклама без основания</div>
        </div>
        <img src="/img/site-c.jpg" alt="" style={{ width: 96, height: 96 }} />
      </Link>

      <div className="mfoot">
        <span className="row" style={{ gap: 6 }}><IcGlobe size={18} />Язык: Русский</span>
        <Link to="/method">Методика скоринга</Link>
        <Link to="/sources">Источники данных</Link>
        <Link to="/reports">Отчёты</Link>
        <Link to="/residents">Жителям</Link>
        <span className="cp">© 2026 SAQ Qurylys — цифровой помощник ДЭР ВКО. Данные демонстрационные, решение принимает сотрудник.</span>
      </div>
    </div>
  )
}

