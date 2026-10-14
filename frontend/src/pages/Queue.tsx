import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Faq, Hero, HeroTile, Highlight, PlatformIcon, Score, StatusDot, Seg, adKey, fmtShort, statusMeta } from '../components/ui'
import type { AdStatus } from '../data/types'
import { scoreAd } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcCheck, IcClock, IcClose, IcHelp, IcInbox, IcUsers } from '../ui/icons'

const COLS: AdStatus[] = ['new', 'needs_check', 'in_review', 'confirmed', 'dismissed']

/** Очередь проверки: доска по статусам, карточки перетаскиваются между колонками */
export default function Queue() {
  const { ads, complexes, setAdStatus, notify } = useStore()
  const nav = useNavigate()
  const [drag, setDrag] = useState<string | null>(null)
  const [over, setOver] = useState<AdStatus | null>(null)
  const [who, setWho] = useState<'all' | 'me' | 'none'>('all')
  const items = ads.map((a) => { const c = complexes.find((x) => x.id === a.complexId); return { a, c, s: scoreAd(a, c) } })
    .filter(({ a }) => who === 'all' || (who === 'me' ? !!a.assignee : !a.assignee))
    .sort((x, y) => y.s.score - x.s.score)
  const n = (st: AdStatus) => ads.filter((a) => a.status === st).length
  const top = items.find((x) => x.a.status === 'new' || x.a.status === 'needs_check')

  return (
    <>
      <Hero crumb="Очередь проверки" title="Очередь проверки"
        info="Сверху — самые рискованные. Перетащите карточку в колонку, чтобы сменить статус"
        actions={<Seg value={who} onChange={setWho} items={[['all', 'Все'], ['me', 'Мои'], ['none', 'Без исполнителя']]} />}
        tiles={<>
          <HeroTile icon={<IcInbox size={32} />} label="Новые" value={n('new')} tone="blue" />
          <HeroTile icon={<IcHelp size={32} />} label="Нужна проверка" value={n('needs_check')} tone="yellow" />
          <HeroTile icon={<IcClock size={32} />} label="На проверке" value={n('in_review')} />
          <HeroTile icon={<IcCheck size={32} />} label="Нарушение подтверждено" value={n('confirmed')} tone="red" />
          <HeroTile icon={<IcClose size={32} />} label="Отклонено" value={n('dismissed')} tone="green" />
          <HeroTile icon={<IcUsers size={32} />} label="Без исполнителя" value={ads.filter((a) => !a.assignee && a.status !== 'dismissed').length} onClick={() => setWho('none')} active={who === 'none'} />
        </>}>
        {top && (
          <div className="panel" style={{ gridTemplateColumns: 'minmax(0, 1fr) 320px' }}>
            <div className="pl row" style={{ gap: 20, alignItems: 'flex-start', paddingBottom: 16 }}>
              {top.a.image ? <img src={top.a.image} alt="" style={{ width: 140, height: 105, borderRadius: 12, objectFit: 'cover', flex: 'none' }} /> : <PlatformIcon p={top.a.platform} size={64} />}
              <div style={{ minWidth: 0 }}>
                <div className="t-sm c2">Следующая по риску · {adKey(top.a.id)}</div>
                <div className="t-h3" style={{ margin: '2px 0 6px' }}>{top.c ? `Реклама ЖК «${top.c.name}»` : 'Реклама без ЖК'}</div>
                <div className="t-sm clamp2" style={{ lineHeight: '19px' }}><Highlight text={top.a.text} /></div>
              </div>
            </div>
            <div className="pr" style={{ justifyContent: 'center', gap: 8 }}>
              <div className="row between"><span className="c2 t-sm">Оценка</span><Score value={top.s.score} zone={top.s.zone} /></div>
              <button className="btn y full" onClick={() => { setAdStatus(top.a.id, 'in_review', 'Нестерова А.'); nav(`/ads/${top.a.id}`) }}>Взять в работу</button>
            </div>
          </div>
        )}
      </Hero>
      <div className="board">
        {COLS.map((st) => {
          const col = items.filter((x) => x.a.status === st)
          return (
            <div key={st} className={`bcol ${over === st ? 'over' : ''}`} onDragOver={(e) => { e.preventDefault(); setOver(st) }} onDragLeave={() => setOver(null)}
              onDrop={() => {
                if (drag && st === 'confirmed') { nav(`/ads/${drag}`); notify('Подтверждение — с указанием основания') }
                else if (drag) { setAdStatus(drag, st, 'Нестерова А.'); notify(`Статус: ${statusMeta[st].name}`) }
                setDrag(null); setOver(null)
              }}>
              <div className="h"><StatusDot s={st} />{statusMeta[st].name}<span className="c3 num" style={{ fontWeight: 400 }}>{col.length}</span></div>
              {col.map(({ a, c, s }) => (
                <div key={a.id} className="bcard" draggable onDragStart={() => setDrag(a.id)} onDragEnd={() => setDrag(null)} onClick={() => nav(`/ads/${a.id}`)} style={{ opacity: drag === a.id ? 0.4 : 1 }}>
                  <div className="row between">
                    <div className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={24} /><span className="t-xs c3 num">{adKey(a.id)}</span></div>
                    <Score value={s.score} zone={s.zone} />
                  </div>
                  <div className="bold">{c ? `ЖК «${c.name}»` : 'ЖК не определён'}</div>
                  <div className="t-sm c2 clamp2" style={{ lineHeight: '19px' }}>{a.text}</div>
                  <div className="row between">
                    <span className="t-xs c3">{fmtShort(a.date)}</span>
                    {a.assignee ? <span className="ava sm" title={a.assignee}>ВН</span> : <span className="ava sm ghost" title="Без исполнителя" />}
                  </div>
                </div>
              ))}
              {col.length === 0 && <div className="t-sm c3" style={{ padding: 24, textAlign: 'center' }}>Перетащите сюда</div>}
            </div>
          )
        })}
      </div>
      <Faq title="Как работать с очередью"
        items={[
          ['В каком порядке разбирать?', 'Сверху вниз: карточки отсортированы по оценке. Кнопка «Взять в работу» в шапке открывает самую рискованную необработанную публикацию.'],
          ['Можно ли подтвердить нарушение перетаскиванием?', 'Нет — при переносе в «Нарушение» откроется карточка публикации: подтверждение требует текстового основания и попадает в журнал.'],
          ['Что делать, если ЖК не определён?', 'Откройте публикацию и привяжите объект вручную — оценка пересчитается.'],
        ]} />
    </>
  )
}
