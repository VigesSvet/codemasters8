import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { FilterPop, Highlight, PageTitle, PlatformIcon, Score, StatusTag, adKey, fmtDT, platformMeta, statusMeta } from '../components/ui'
import type { AdStatus, Platform, Zone } from '../data/types'
import { useStore } from '../lib/store'
import { IcClose, IcPlus, IcRefresh, IcSearch } from '../ui/icons'

const ORDER: AdStatus[] = ['new', 'needs_check', 'in_review', 'confirmed', 'dismissed']
type Sort = 'risk' | 'date' | 'reach'

/** Лента рекламы — как выдача объявлений realty.yandex.ru/…/kupit/kvartira: фильтры сверху, строки с фото */
export default function Ads() {
  const { ads, complexes, notify, adScoreOf } = useStore()
  const [q, setQ] = useState('')
  const [plat, setPlat] = useState<Platform[]>([])
  const [status, setStatus] = useState<AdStatus[]>([])
  const [zone, setZone] = useState<Zone[]>([])
  const [chip, setChip] = useState('')
  const [sort, setSort] = useState<Sort>('risk')

  const all = useMemo(() => ads.map((a) => { const c = complexes.find((x) => x.id === a.complexId); return { a, c, s: adScoreOf(a) } }), [ads, complexes, adScoreOf])
  type R = (typeof all)[number]
  const chips: [string, string, (x: R) => boolean][] = [
    ['queue', 'Ждут решения', (x) => x.a.status === 'new' || x.a.status === 'needs_check'],
    ['nocx', 'ЖК не определён', (x) => !x.c],
    ['ocr', 'Есть текст на фото', (x) => !!x.a.ocr],
    ['manual', 'Добавлены вручную', (x) => x.a.source === 'manual'],
  ]
  const match = (x: R, f: { plat?: Platform[]; status?: AdStatus[]; zone?: Zone[] } = {}) => {
    const p = f.plat ?? plat, st = f.status ?? status, z = f.zone ?? zone
    return (!q || `${x.a.text} ${x.a.account} ${x.c?.name ?? ''}`.toLowerCase().includes(q.toLowerCase()))
      && (!p.length || p.includes(x.a.platform)) && (!st.length || st.includes(x.a.status)) && (!z.length || z.includes(x.s.zone))
      && (!chip || chips.find((c) => c[0] === chip)![2](x))
  }
  const rows = all.filter((x) => match(x)).sort((x, y) => sort === 'risk' ? y.s.score - x.s.score : sort === 'date' ? y.a.date.localeCompare(x.a.date) : (y.a.reach ?? 0) - (x.a.reach ?? 0))
  const plats = (Object.keys(platformMeta) as Platform[]).filter((p) => ads.some((a) => a.platform === p))
  const dirty = plat.length || status.length || zone.length || chip || q

  return (
    <>
      <div className="fbar" style={{ marginTop: 12 }}>
        <button className="fbtn gray" onClick={() => { setPlat([]); setStatus([]); setZone([]); setChip(''); setQ('') }}><IcSearch />{dirty ? 'Сбросить' : 'Вся реклама'}</button>
        <span className="vsep" />
        <FilterPop label="Площадка" title="Где опубликовано" multi value={plat} onChange={setPlat} count={(v) => all.filter((x) => match(x, { plat: v })).length}
          options={plats.map((p) => [p, <span className="row" style={{ gap: 8 }}><PlatformIcon p={p} size={20} />{platformMeta[p].name}</span>] as [Platform, React.ReactNode])} />
        <FilterPop label="Статус" title="Статус проверки" multi value={status} onChange={setStatus} count={(v) => all.filter((x) => match(x, { status: v })).length}
          options={ORDER.map((s) => [s, statusMeta[s].name] as [AdStatus, string])} />
        <FilterPop label="Оценка" title="Оценка публикации" multi value={zone} onChange={setZone} count={(v) => all.filter((x) => match(x, { zone: v })).length}
          options={[['red', 'Красная'], ['yellow', 'Жёлтая'], ['green', 'Зелёная']]} />
        <label className="fsearch">
          <input placeholder="Текст объявления, аккаунт или ЖК" value={q} onChange={(e) => setQ(e.target.value)} />
          {q && <button className="c3" onClick={() => setQ('')}><IcClose size={16} /></button>}
        </label>
      </div>

      <PageTitle title="Реклама новостроек в Усть-Каменогорске"
        sub={<>Подсветка: <mark>продажа</mark>, <mark className="fund">предложение денег (R2)</mark>, <mark className="safe">признаки законности</mark></>}
        actions={<><button className="btn g m" onClick={() => notify('Сбор публикаций запущен (демо)')}><IcRefresh />Запустить сбор</button><Link to="/ads/new" className="btn y m"><IcPlus />Добавить вручную</Link></>} />
      <div className="chips" style={{ marginTop: -8, marginBottom: 24 }}>
        {chips.map(([k, l, fn]) => <button key={k} className={`chip ${chip === k ? 'on' : ''}`} onClick={() => setChip(chip === k ? '' : k)}>{l}<span className="n">{all.filter(fn).length}</span></button>)}
      </div>

      <div className="sorter">
        <b>{rows.length} публикаций:</b>
        {([['risk', 'по риску'], ['date', 'по дате'], ['reach', 'по охвату']] as const).map(([k, l]) => <button key={k} className={sort === k ? 'on' : ''} onClick={() => setSort(k)}>{l}</button>)}
      </div>

      {rows.length === 0 && <div className="empty">Ничего не найдено</div>}
      <div className="fade">
        {rows.map(({ a, c, s }) => (
          <Link key={a.id} to={`/ads/${a.id}`} className="offer">
            <div className="ph">
              {a.image ? <img src={a.image} alt="" /> : <div className="noimg"><PlatformIcon p={a.platform} size={48} /></div>}
              <div className="tl"><span className="tag white">{platformMeta[a.platform].name}</span>{a.source === 'manual' && <span className="tag white">вручную</span>}</div>
            </div>
            <div className="body">
              <div className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={20} /><span className="bold">{a.account}</span><span className="c2 t-sm">{fmtDT(a.date)}</span></div>
              <div className="nm">{c ? `ЖК «${c.name}»` : 'ЖК не определён'}</div>
              <p className="txt clamp3"><Highlight text={a.text} /></p>
              <div className="row wrap-f" style={{ gap: 6, marginTop: 'auto' }}>
                <StatusTag s={a.status} />
                {a.price && <span className="tag gray">{a.price}</span>}
                {a.ocr && <span className="tag gray">текст на фото</span>}
              </div>
            </div>
            <div className="side">
              <div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}><span className="c2 t-sm">оценка</span><Score value={s.score} zone={s.zone} /></div>
              <div className="t-sm c2" style={{ textAlign: 'right' }}>{a.reach ? `охват ~${(a.reach / 1000).toFixed(1)} тыс.` : 'охват неизвестен'}</div>
              <div className="t-xs c3" style={{ textAlign: 'right' }}>{adKey(a.id)}</div>
              <span className="btn g m" style={{ marginTop: 'auto' }}>Разобрать</span>
            </div>
          </Link>
        ))}
      </div>
    </>
  )
}
