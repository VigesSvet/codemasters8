import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Faq, Hero, HeroTile, Score, Seg, Toggle, ZoneTag } from '../components/ui'
import { developers } from '../data/demo'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcCheck, IcClock, IcDoc, IcDownload, IcMegaphone, IcTraffic } from '../ui/icons'

const SERIES = { w: [3, 5, 4, 7, 6, 9, 8, 12], m: [14, 18, 22, 31], q: [41, 56, 67] }
const LABELS = { w: ['н34', 'н35', 'н36', 'н37', 'н38', 'н39', 'н40', 'н41'], m: ['июль', 'авг', 'сен', 'окт'], q: ['II кв', 'III кв', 'IV кв'] }
const PERIOD = { w: '7–14 октября 2026', m: 'июль — октябрь 2026', q: 'II — IV квартал 2026' }

/** Отчёты — шапка с конструктором отчёта (как ипотечный калькулятор), ниже рейтинг и FAQ */
export default function Reports() {
  const nav = useNavigate()
  const { complexes, ads, notify } = useStore()
  const [period, setPeriod] = useState<'w' | 'm' | 'q'>('w')
  const [parts, setParts] = useState({ map: true, rating: true, ads: true, photos: false, method: true })
  const scored = complexes.map((c) => ({ c, r: scoreComplex(c, ads) })).sort((a, b) => b.r.score - a.r.score)
  const confirmed = ads.filter((a) => a.status === 'confirmed').length
  const red = scored.filter((x) => x.r.zone === 'red').length
  const series = SERIES[period]
  const max = Math.max(...series)
  const pages = 1 + Object.values(parts).filter(Boolean).length
  const part = (k: keyof typeof parts, label: string, sub: string) => (
    <div className="crow"><span>{label}<span className="t-sm c2" style={{ display: 'block' }}>{sub}</span></span><Toggle on={parts[k]} onChange={(v) => setParts((p) => ({ ...p, [k]: v }))} /></div>
  )

  return (
    <>
      <Hero crumb="Отчёты" title="Отчёт для руководства" info={`Период: ${PERIOD[period]} · Усть-Каменогорск`}
        tiles={<>
          <HeroTile icon={<IcMegaphone size={32} />} label="Новых публикаций" value={ads.length} to="/ads" />
          <HeroTile icon={<IcCheck size={32} />} label="Подтверждено нарушений" value={confirmed} tone="red" to="/queue" />
          <HeroTile icon={<IcTraffic size={32} />} label="Объектов в красной зоне" value={red} tone="red" to="/complexes?zone=red" />
          <HeroTile icon={<IcClock size={32} />} label="Среднее время решения" value="1,4 дн." to="/queue" />
          <HeroTile icon={<IcDoc size={32} />} label="Отчётов за месяц" value={4} onClick={() => notify('Архив отчётов (демо)')} />
          <HeroTile icon={<IcDownload size={32} />} label="Скачиваний" value={11} onClick={() => notify('Статистика (демо)')} />
        </>}>
        <div className="panel" style={{ gridTemplateColumns: 'minmax(0, 1fr) 380px' }}>
          <div className="pl">
            <div className="row between" style={{ marginBottom: 8 }}><h2 className="t-h2">Соберите отчёт</h2><Seg value={period} onChange={setPeriod} items={[['w', 'Неделя'], ['m', 'Месяц'], ['q', 'Квартал']]} /></div>
            {part('map', 'Карта-светофор', 'Все объекты города по зонам риска')}
            {part('rating', 'Рейтинг риска', 'Объекты по убыванию балла с факторами')}
            {part('ads', 'Реклама за период', 'Публикации с признаками продажи и решения')}
            {part('photos', 'Фото-мониторинг', 'Простой площадок по снимкам')}
            {part('method', 'Методика', 'Как считается балл — для приложения')}
          </div>
          <div className="pr">
            <span className="c2 t-sm">Реклама с признаками продажи</span>
            <div className="row" style={{ alignItems: 'flex-end', gap: 8, margin: '8px 0 16px' }}><span className="big-num">{series[series.length - 1]}</span><span className="tag red" style={{ marginBottom: 8 }}>+{Math.round((series[series.length - 1] / series[series.length - 2] - 1) * 100)}%</span></div>
            <div className="bars" style={{ height: 130 }}>{series.map((v, i) => <div key={i}><b style={{ height: (v / max) * 100, background: i === series.length - 1 ? 'var(--yellow)' : '#fff' }} /><span className="t-xs c3">{LABELS[period][i]}</span></div>)}</div>
            <button className="btn y full" style={{ marginTop: 20 }} onClick={() => notify(`Отчёт сформирован: ${pages} стр. (PDF, демо)`)}><IcDownload />Скачать PDF · {pages} стр.</button>
          </div>
        </div>
      </Hero>

      <h2 className="t-h2" style={{ marginBottom: 4 }}>Рейтинг риска</h2>
      <table className="ytable">
        <thead><tr><th>#</th><th>Жилой комплекс</th><th>Застройщик</th><th>Факторы</th><th className="r">Балл</th></tr></thead>
        <tbody>
          {scored.map(({ c, r }, i) => (
            <tr key={c.id} onClick={() => nav(`/complexes/${c.id}`)}>
              <td className="c3 num">{i + 1}</td>
              <td><div className="row" style={{ gap: 12 }}><img src={c.cover} alt="" style={{ width: 56, height: 42, borderRadius: 8, objectFit: 'cover' }} /><div><span className="link">ЖК «{c.name}»</span><div className="t-sm c2">{c.district} · {c.stage}</div></div></div></td>
              <td className="t-sm c2">{developers.find((d) => d.id === c.developerId)?.name}</td>
              <td><div className="row wrap-f" style={{ gap: 4 }}>{r.factors.filter((f) => f.state === 'hit').map((f) => <span key={f.code} className="tag gray">{f.code}</span>)}{!r.factors.some((f) => f.state === 'hit') && <span className="c3">—</span>}</div></td>
              <td className="r"><div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}><ZoneTag zone={r.zone} /><Score value={r.score} zone={r.zone} /></div></td>
            </tr>
          ))}
        </tbody>
      </table>

      <Faq title="Вопросы об отчёте"
        items={[
          ['Кому отправлять отчёт?', 'Руководителю департамента и в ГАСК. Отчёт — внутренний документ: в нём рекомендации системы и решения аналитиков, автоматически в госорганы он не уходит.'],
          ['Можно ли использовать отчёт как доказательство нарушения?', 'Нет. Отчёт показывает приоритеты для проверки. Нарушение подтверждает сотрудник по документам: картотеке ГАСК, гарантиям ЕО, разрешениям МИО.'],
          ['Почему цифры за неделю и месяц не совпадают с лентой?', 'В отчёт попадают публикации, собранные за выбранный период. Лента показывает всё, что есть в базе сейчас.'],
        ]} />
    </>
  )
}
