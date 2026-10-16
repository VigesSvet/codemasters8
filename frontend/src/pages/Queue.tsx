import { useState } from 'react'
import { DndContext, DragOverlay, KeyboardSensor, MouseSensor, TouchSensor, pointerWithin, useDraggable, useDroppable, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { LinkMark } from '../components/Hypothesis'
import { Faq, Modal, Hero, HeroTile, Highlight, PlatformIcon, Score, StatusDot, Seg, adKey, fmtShort, statusMeta } from '../components/ui'
import type { Ad, AdStatus, Complex } from '../data/types'
import { initialsOf } from '../lib/audit'
import { from } from '../lib/nav'
import { scoreAd } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcCheck, IcClock, IcClose, IcFlag, IcHelp, IcInbox, IcUsers } from '../ui/icons'

const COLS: AdStatus[] = ['new', 'needs_check', 'in_review', 'confirmed', 'dismissed']
type Item = { a: Ad; c?: Complex; s: ReturnType<typeof scoreAd> }
/** Чьи карточки показывать — в адресе (?who=me|none), чтобы «назад» из публикации не сбрасывал выбор */
type Who = 'all' | 'me' | 'none'
const WHO: Who[] = ['all', 'me', 'none']

function QCard({ x, overlay }: { x: Item; overlay?: boolean }) {
  const { a, c, s } = x
  const { analysts } = useStore()
  return (
    <div className={`bcard ${overlay ? 'lift' : ''}`}>
      <div className="row between">
        <div className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={24} /><span className="t-xs c3 num">{adKey(a.id)}</span></div>
        <Score value={s.score} zone={s.zone} />
      </div>
      <div className="bold">{c ? `ЖК «${c.name}»` : 'ЖК не определён'}</div>
      {c && <LinkMark a={a} style={{ alignSelf: 'flex-start' }} />}
      <div className="t-sm c2 clamp2" style={{ lineHeight: '19px' }}>{a.text}</div>
      <div className="row between">
        <span className="t-xs c3">{fmtShort(a.date)}</span>
        {a.assignee ? <span className="ava sm" title={a.assignee}>{initialsOf(a.assignee, analysts)}</span> : <span className="ava sm ghost" title="Без исполнителя" />}
      </div>
    </div>
  )
}

function Draggable({ x, onOpen }: { x: Item; onOpen: (id: string) => void }) {
  const { setNodeRef, listeners, attributes, isDragging } = useDraggable({ id: x.a.id })
  return (
    <div ref={setNodeRef} {...listeners} {...attributes} className={`dwrap ${isDragging ? 'ghost' : ''}`}
      onClick={() => onOpen(x.a.id)} onKeyDown={(e) => { if (e.key === 'Enter') onOpen(x.a.id) }}>
      <QCard x={x} />
    </div>
  )
}

function Column({ st, items, dragging, fromSt, onOpen }: { st: AdStatus; items: Item[]; dragging: string | null; fromSt?: AdStatus; onOpen: (id: string) => void }) {
  const { setNodeRef, isOver } = useDroppable({ id: st })
  const target = !!dragging && fromSt !== st
  return (
    <div ref={setNodeRef} className={`bcol ${target ? 'target' : ''} ${isOver && target ? 'over' : ''}`}>
      <div className="h"><StatusDot s={st} />{statusMeta[st].name}<span className="c3 num" style={{ fontWeight: 400 }}>{items.length}</span></div>
      {isOver && target && <div className="dropph">{st === 'confirmed' ? 'Отпустите — попросим основание' : 'Отпустите, чтобы перенести'}</div>}
      {items.map((x) => <Draggable key={x.a.id} x={x} onOpen={onOpen} />)}
      {items.length === 0 && !(isOver && target) && <div className="t-sm c3" style={{ padding: 24, textAlign: 'center' }}>Перетащите сюда</div>}
    </div>
  )
}

/** Очередь проверки: доска по статусам, карточки перетаскиваются между колонками */
export default function Queue() {
  const { ads, complexes, setAdStatus, notify, adScoreOf, signals, user } = useStore()
  const nav = useNavigate()
  const [drag, setDrag] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<Item | null>(null)
  // черновик основания — по публикации: при ошибке сохранения не теряется
  const [reasons, setReasons] = useState<Record<string, string>>({})
  const reason = confirm ? reasons[confirm.a.id] ?? '' : ''
  // мышь: начинаем тащить после сдвига на 6px, чтобы клик по карточке открывал её; тач — после удержания
  const sensors = useSensors(useSensor(MouseSensor, { activationConstraint: { distance: 6 } }), useSensor(TouchSensor, { activationConstraint: { delay: 180, tolerance: 6 } }), useSensor(KeyboardSensor))
  const [params, setParams] = useSearchParams()
  const who: Who = WHO.find((w) => w === params.get('who')) ?? 'all'
  const setWho = (w: Who) => setParams((p) => { const q = new URLSearchParams(p); if (w === 'all') q.delete('who'); else q.set('who', w); return q }, { replace: true })
  // откуда пришли в разбор — очередь с тем же выбором «Все / Мои / Без исполнителя»
  const back = from('Очередь проверки', who === 'all' ? '/queue' : `/queue?who=${who}`)
  const open = (id: string) => nav(`/ads/${id}`, { state: back })
  const me = user?.name
  const items = ads.map((a) => { const c = complexes.find((x) => x.id === a.complexId); return { a, c, s: adScoreOf(a) } })
    .filter(({ a }) => who === 'all' || (who === 'me' ? !!a.assignee && a.assignee === me : !a.assignee))
    .sort((x, y) => y.s.score - x.s.score)
  const n = (st: AdStatus) => ads.filter((a) => a.status === st).length
  const dragItem = items.find((x) => x.a.id === drag)
  const onDragEnd = (e: DragEndEvent) => {
    setDrag(null)
    const it = items.find((x) => x.a.id === e.active.id), to = e.over?.id as AdStatus | undefined
    if (!it || !to || to === it.a.status) return
    if (to === 'confirmed') { setConfirm(it); return }
    // перенос виден сразу; при ошибке стор вернёт карточку и покажет баннер
    setAdStatus(it.a.id, to, { assignee: me }).then((ok) => { if (ok) notify(`${adKey(it.a.id)} → «${statusMeta[to].name}»`) })
  }
  const confirmIt = (x: Item, why: string) => {
    setConfirm(null)
    setAdStatus(x.a.id, 'confirmed', { assignee: me, comment: why }).then((ok) => {
      if (!ok) return
      setReasons((r) => ({ ...r, [x.a.id]: '' }))
      notify('Нарушение подтверждено, основание записано в журнал')
    })
  }
  const top = items.find((x) => x.a.status === 'new' || x.a.status === 'needs_check')
  // карточка открывается сразу (статус виден оптимистично); её журнал перечитается, когда PATCH сохранится — по revision стора
  const take = (x: Item) => { void setAdStatus(x.a.id, 'in_review', { assignee: me }); open(x.a.id) }
  const sigNew = signals.filter((x) => x.status === 'new').length

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
          <HeroTile icon={<IcUsers size={32} />} label="Без исполнителя" value={ads.filter((a) => !a.assignee && a.status !== 'dismissed').length} onClick={() => setWho(who === 'none' ? 'all' : 'none')} active={who === 'none'} />
          <HeroTile icon={<IcFlag size={32} />} label="Сигналы ждут решения" value={sigNew} tone={sigNew ? 'red' : undefined} to="/signals?status=new" />
        </>}>
        {top && (
          <div className="panel" style={{ gridTemplateColumns: 'minmax(0, 1fr) 320px' }}>
            <div className="pl row" style={{ gap: 20, alignItems: 'flex-start', paddingBottom: 16 }}>
              {top.a.image ? <img src={top.a.image} alt="" style={{ width: 140, height: 105, borderRadius: 12, objectFit: 'cover', flex: 'none' }} /> : <PlatformIcon p={top.a.platform} size={64} />}
              <div style={{ minWidth: 0 }}>
                <div className="t-sm c2">Следующая по риску · {adKey(top.a.id)}</div>
                <div className="t-h3 row wrap-f" style={{ margin: '2px 0 6px', gap: 10 }}>{top.c ? `Реклама ЖК «${top.c.name}»` : 'Реклама без ЖК'}<LinkMark a={top.a} label="привязка — гипотеза ИИ" /></div>
                <div className="t-sm clamp2" style={{ lineHeight: '19px' }}><Highlight text={top.a.text} /></div>
              </div>
            </div>
            <div className="pr" style={{ justifyContent: 'center', gap: 8 }}>
              <div className="row between"><span className="c2 t-sm">Оценка</span><Score value={top.s.score} zone={top.s.zone} /></div>
              <button className="btn y full" onClick={() => take(top)}>Взять в работу</button>
            </div>
          </div>
        )}
      </Hero>
      <DndContext sensors={sensors} collisionDetection={pointerWithin} onDragStart={(e) => setDrag(String(e.active.id))} onDragCancel={() => setDrag(null)} onDragEnd={onDragEnd}>
        <div className="board">
          {COLS.map((st) => <Column key={st} st={st} items={items.filter((x) => x.a.status === st)} dragging={drag} fromSt={dragItem?.a.status} onOpen={open} />)}
        </div>
        <DragOverlay dropAnimation={{ duration: 180, easing: 'cubic-bezier(.2,.8,.2,1)' }}>
          {dragItem && <QCard x={dragItem} overlay />}
        </DragOverlay>
      </DndContext>

      {confirm && (
        <Modal title="Подтвердить нарушение?" onClose={() => setConfirm(null)}
          footer={<><button className="btn g" onClick={() => setConfirm(null)}>Отмена</button><button className="btn y" disabled={!reason.trim()} onClick={() => confirmIt(confirm, reason.trim())}>Подтвердить нарушение</button></>}>
          <div className="row" style={{ gap: 14, padding: '4px 24px 8px' }}>
            {confirm.a.image ? <img src={confirm.a.image} alt="" style={{ width: 72, height: 54, borderRadius: 10, objectFit: 'cover' }} /> : <PlatformIcon p={confirm.a.platform} size={48} />}
            <div style={{ minWidth: 0 }}><div className="bold">{confirm.c ? `ЖК «${confirm.c.name}»` : 'ЖК не определён'}</div><div className="t-sm c2">{confirm.a.account} · оценка {confirm.s.score}</div></div>
          </div>
          <div style={{ padding: '8px 24px 4px' }}>
            <label className="t-sm c2" htmlFor="qreason">Основание решения — попадёт в журнал</label>
            <textarea id="qreason" className="input" style={{ marginTop: 6 }} autoFocus maxLength={2000} placeholder="Например: сверено с картотекой ГАСК, гарантии ЕО и разрешения МИО нет" value={reason} onChange={(e) => setReasons((r) => ({ ...r, [confirm.a.id]: e.target.value }))} />
          </div>
        </Modal>
      )}

      <Faq title="Как работать с очередью"
        items={[
          ['В каком порядке разбирать?', 'Сверху вниз: карточки отсортированы по оценке. Кнопка «Взять в работу» в шапке открывает самую рискованную необработанную публикацию.'],
          ['Можно ли подтвердить нарушение перетаскиванием?', 'Да, но при переносе в «Нарушение» система попросит основание решения — оно попадёт в журнал. Без основания статус не сменится.'],
          ['Что делать, если ЖК не определён?', 'Откройте публикацию и привяжите объект вручную — оценка пересчитается.'],
          ['Как из публикации дойти до сигнала?', <>Откройте публикацию → привязанный ЖК → «Сформировать сигнал» (или сразу в публикации: «Сформировать сигнал по ЖК»). В сигнал попадут риск объекта и все его публикации, обращения и снимки на этот момент. Решение по сигналу — подтвердить или вернуть на доработку — в разделе <Link className="link" to="/signals">Сигналы</Link>.</>],
        ]} />
    </>
  )
}
