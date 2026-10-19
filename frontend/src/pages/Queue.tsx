import { useState } from 'react'
import { DndContext, DragOverlay, KeyboardSensor, MouseSensor, TouchSensor, pointerWithin, useDraggable, useDroppable, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { LinkMark } from '../components/Hypothesis'
import { Faq, Modal, Hero, Highlight, PlatformIcon, SafeImg, Score, StatusDot, Seg, adKey } from '../components/ui'
import { READ_ONLY } from '../data/snapshot'
import type { Ad, AdStatus, Complex } from '../data/types'
import { initialsOf } from '../lib/audit'
import { useT } from '../lib/i18n'
import { adImage } from '../lib/media'
import { from } from '../lib/nav'
import { scoreAd } from '../lib/risk'
import { useStore } from '../lib/store'
import { Board, Cap, Flow, Meter, Row } from '../components/StatBoard'

const COLS: AdStatus[] = ['new', 'needs_check', 'in_review', 'confirmed', 'dismissed']
type Item = { a: Ad; c?: Complex; s: ReturnType<typeof scoreAd> }
/** Чьи карточки показывать — в адресе (?who=me|none), чтобы «назад» из публикации не сбрасывал выбор */
type Who = 'all' | 'me' | 'none'
const WHO: Who[] = ['all', 'me', 'none']

function QCard({ x, overlay }: { x: Item; overlay?: boolean }) {
  const { a, c, s } = x
  const { analysts } = useStore()
  const { t, dayMonth } = useT()
  return (
    <div className={`bcard ${overlay ? 'lift' : ''}`}>
      <div className="row between">
        <div className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={24} /><span className="t-xs c3 num">{adKey(a.id)}</span></div>
        <Score value={s.score} zone={s.zone} />
      </div>
      <div className="bold">{c ? t('common.complex', { name: c.name }) : t('common.complexUnknown')}</div>
      {c && <LinkMark a={a} style={{ alignSelf: 'flex-start' }} />}
      <div className="t-sm c2 clamp2" style={{ lineHeight: '19px' }}>{a.text}</div>
      <div className="row between">
        <span className="t-xs c3">{dayMonth(a.date)}</span>
        {a.assignee ? <span className="ava sm" title={a.assignee}>{initialsOf(a.assignee, analysts)}</span> : <span className="ava sm ghost" title={t('queue.noAssignee')} />}
      </div>
    </div>
  )
}

/** ro — офлайн-копия: карточки не перетаскиваются, только открываются */
function Draggable({ x, onOpen, ro }: { x: Item; onOpen: (id: string) => void; ro?: boolean }) {
  const { setNodeRef, listeners, attributes, isDragging } = useDraggable({ id: x.a.id, disabled: ro })
  return (
    <div ref={setNodeRef} {...listeners} {...attributes} className={`dwrap ${isDragging ? 'ghost' : ''}`}
      onClick={() => onOpen(x.a.id)} onKeyDown={(e) => { if (e.key === 'Enter') onOpen(x.a.id) }}>
      <QCard x={x} />
    </div>
  )
}

function Column({ st, items, dragging, fromSt, onOpen, ro }: { st: AdStatus; items: Item[]; dragging: string | null; fromSt?: AdStatus; onOpen: (id: string) => void; ro?: boolean }) {
  const { setNodeRef, isOver } = useDroppable({ id: st })
  const target = !!dragging && fromSt !== st
  const { t } = useT()
  return (
    <div ref={setNodeRef} className={`bcol ${target ? 'target' : ''} ${isOver && target ? 'over' : ''}`}>
      <div className="h"><StatusDot s={st} />{t(`status.${st}`)}<span className="c3 num" style={{ fontWeight: 400 }}>{items.length}</span></div>
      {isOver && target && <div className="dropph">{st === 'confirmed' ? t('queue.dropConfirm') : t('queue.drop')}</div>}
      {items.map((x) => <Draggable key={x.a.id} x={x} onOpen={onOpen} ro={ro} />)}
      {items.length === 0 && !(isOver && target) && <div className="t-sm c3" style={{ padding: 24, textAlign: 'center' }}>{ro ? t('queue.empty') : t('queue.dragHere')}</div>}
    </div>
  )
}

/** Очередь проверки: доска по статусам, карточки перетаскиваются между колонками */
export default function Queue() {
  const { mode, ads, complexes, setAdStatus, notify, adScoreOf, signals, user } = useStore()
  // офлайн-копия — только просмотр: статусы меняются с сервером
  const ro = mode === 'snapshot'
  const nav = useNavigate()
  const { t, tr } = useT()
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
  const back = from(t('nav.queue'), who === 'all' ? '/queue' : `/queue?who=${who}`)
  const open = (id: string) => nav(`/ads/${id}`, { state: back })
  const me = user?.name
  const items = ads.map((a) => { const c = complexes.find((x) => x.id === a.complexId); return { a, c, s: adScoreOf(a) } })
    .filter(({ a }) => who === 'all' || (who === 'me' ? !!a.assignee && a.assignee === me : !a.assignee))
    .sort((x, y) => y.s.score - x.s.score)
  const n = (st: AdStatus) => ads.filter((a) => a.status === st).length
  const dragItem = items.find((x) => x.a.id === drag)
  const onDragEnd = (e: DragEndEvent) => {
    setDrag(null)
    if (ro) return
    const it = items.find((x) => x.a.id === e.active.id), to = e.over?.id as AdStatus | undefined
    if (!it || !to || to === it.a.status) return
    if (to === 'confirmed') { setConfirm(it); return }
    // перенос виден сразу; при ошибке стор вернёт карточку и покажет баннер
    setAdStatus(it.a.id, to, { assignee: me }).then((ok) => { if (ok) notify(`${adKey(it.a.id)} → «${t(`status.${to}`)}»`) })
  }
  const confirmIt = (x: Item, why: string) => {
    setConfirm(null)
    setAdStatus(x.a.id, 'confirmed', { assignee: me, comment: why }).then((ok) => {
      if (!ok) return
      setReasons((r) => ({ ...r, [x.a.id]: '' }))
      notify(t('queue.confirmed'))
    })
  }
  const top = items.find((x) => x.a.status === 'new' || x.a.status === 'needs_check')
  // карточка открывается сразу (статус виден оптимистично); её журнал перечитается, когда PATCH сохранится — по revision стора
  const take = (x: Item) => { void setAdStatus(x.a.id, 'in_review', { assignee: me }); open(x.a.id) }
  const sigNew = signals.filter((x) => x.status === 'new').length

  return (
    <>
      <Hero crumb={t('nav.queue')} title={t('nav.queue')}
        info={ro ? t('queue.infoRo', { ro: tr(READ_ONLY) }) : t('queue.info')}
        actions={<Seg value={who} onChange={setWho} items={[['all', t('common.all')], ['me', t('queue.mine')], ['none', t('queue.noAssignee')]]} />}
        board={<Board kind="queue">
          <div className="sb-col">
            <Cap aside={t('queue.solved', { a: n('confirmed') + n('dismissed'), b: ads.length })}>{t('queue.path')}</Cap>
            <Flow
              stages={[
                { em: 'inbox', tone: 'blue', value: n('new'), label: t('queue.fNew') },
                { em: 'search', tone: 'yellow', value: n('needs_check'), label: t('status.needs_check') },
                { em: 'review', value: n('in_review'), label: t('status.in_review') },
              ]}
              outcomes={[
                { em: 'stamp', tone: 'red', value: n('confirmed'), label: t('queue.fConfirmed') },
                { em: 'dismiss', tone: 'green', value: n('dismissed'), label: t('status.dismissed') },
              ]} />
          </div>
          <div className="sb-sep" />
          <div className="sb-col">
            <Cap>{t('queue.waitPeople')}</Cap>
            <Meter label={t('queue.noAssignee')} value={ads.filter((a) => !a.assignee && a.status !== 'dismissed').length} of={ads.filter((a) => a.status !== 'dismissed').length}
              tone="yellow" onClick={() => setWho(who === 'none' ? 'all' : 'none')} active={who === 'none'} />
            <Row em="flag" tone="red" label={t('shell.signalsWaiting')} value={sigNew} to="/signals?status=new" />
          </div>
        </Board>}>
        {top && (
          <div className="panel" style={{ gridTemplateColumns: 'minmax(0, 1fr) 320px' }}>
            <div className="pl row" style={{ gap: 20, alignItems: 'flex-start', paddingBottom: 16 }}>
              <SafeImg src={adImage(top.a)} alt="" style={{ width: 140, height: 105, borderRadius: 12, objectFit: 'cover', flex: 'none' }} fallback={<PlatformIcon p={top.a.platform} size={64} />} />
              <div style={{ minWidth: 0 }}>
                <div className="t-sm c2">{t('queue.next', { key: adKey(top.a.id) })}</div>
                <div className="t-h3 row wrap-f" style={{ margin: '2px 0 6px', gap: 10 }}>{top.c ? t('queue.adOf', { name: top.c.name }) : t('queue.adNoComplex')}<LinkMark a={top.a} label={t('common.aiLink')} /></div>
                <div className="t-sm clamp2" style={{ lineHeight: '19px' }}><Highlight text={top.a.text} /></div>
              </div>
            </div>
            <div className="pr" style={{ justifyContent: 'center', gap: 8 }}>
              <div className="row between"><span className="c2 t-sm">{t('queue.score')}</span><Score value={top.s.score} zone={top.s.zone} /></div>
              {ro ? <button className="btn y full" onClick={() => open(top.a.id)} title={tr(READ_ONLY)}>{t('queue.openAd')}</button>
                : <button className="btn y full" onClick={() => take(top)}>{t('queue.take')}</button>}
            </div>
          </div>
        )}
      </Hero>
      <DndContext sensors={sensors} collisionDetection={pointerWithin} onDragStart={(e) => setDrag(String(e.active.id))} onDragCancel={() => setDrag(null)} onDragEnd={onDragEnd}>
        <div className="board">
          {COLS.map((st) => <Column key={st} st={st} items={items.filter((x) => x.a.status === st)} dragging={drag} fromSt={dragItem?.a.status} onOpen={open} ro={ro} />)}
        </div>
        <DragOverlay dropAnimation={{ duration: 180, easing: 'cubic-bezier(.2,.8,.2,1)' }}>
          {dragItem && <QCard x={dragItem} overlay />}
        </DragOverlay>
      </DndContext>

      {confirm && (
        <Modal title={t('queue.confirmQ')} onClose={() => setConfirm(null)}
          footer={<><button className="btn g" onClick={() => setConfirm(null)}>{t('common.cancel')}</button><button className="btn y" disabled={!reason.trim()} onClick={() => confirmIt(confirm, reason.trim())}>{t('queue.confirm')}</button></>}>
          <div className="row" style={{ gap: 14, padding: '4px 24px 8px' }}>
            <SafeImg src={adImage(confirm.a)} alt="" style={{ width: 72, height: 54, borderRadius: 10, objectFit: 'cover' }} fallback={<PlatformIcon p={confirm.a.platform} size={48} />} />
            <div style={{ minWidth: 0 }}><div className="bold">{confirm.c ? t('common.complex', { name: confirm.c.name }) : t('common.complexUnknown')}</div><div className="t-sm c2">{t('queue.accountScore', { account: confirm.a.account, n: confirm.s.score })}</div></div>
          </div>
          <div style={{ padding: '8px 24px 4px' }}>
            <label className="t-sm c2" htmlFor="qreason">{t('queue.reason')}</label>
            <textarea id="qreason" className="input" style={{ marginTop: 6 }} autoFocus maxLength={2000} placeholder={t('queue.reasonPh')} value={reason} onChange={(e) => setReasons((r) => ({ ...r, [confirm.a.id]: e.target.value }))} />
          </div>
        </Modal>
      )}

      <Faq title={t('queue.faq')}
        items={[
          [t('queue.q1'), t('queue.a1')],
          [t('queue.q2'), t('queue.a2')],
          [t('queue.q3'), t('queue.a3')],
          [t('queue.q4'), <>{t('queue.a4')}<Link className="link" to="/signals">{t('nav.signals')}</Link>{t('queue.a4end')}</>],
        ]} />
    </>
  )
}
