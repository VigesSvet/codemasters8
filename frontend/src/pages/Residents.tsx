import { coverSrc } from '../lib/gallery'
import { useMemo, useState } from 'react'
import { Cell, Faq, Hero, Modal, RiskScore, Seg } from '../components/ui'
import type { Complex, Zone } from '../data/types'
import { isAssessed, model } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcCheck, IcChevronDown, IcSend } from '../ui/icons'
import { Board, Cap, Kpi, Row, Shares } from '../components/StatBoard'

const T = model.thresholds
type Tpl = 'basis' | 'idle' | 'ok'
interface TplDef { label: string; title: string; text: (c: Complex) => string; fits: (c: Complex) => boolean; why: string }
// Шаблоны не утверждают нарушение: только проверяемый факт по объекту и совет жителю. fits — факт есть в данных объекта
const TPL: Record<Tpl, TplDef> = {
  basis: {
    label: 'Проверьте основание', title: 'Проверьте основание перед оплатой',
    text: (c) => `Перед бронью или взносом в ЖК «${c.name}» попросите у застройщика номер гарантии ЕО или разрешения МИО и сверьте его в ГАСК. Не вносите деньги без договора с этими реквизитами.`,
    fits: (c) => c.stage !== model.builtStage, why: 'объект уже введён в эксплуатацию',
  },
  idle: {
    label: 'Стройка стоит', title: 'Стройка приостановлена',
    text: (c) => `По снимкам на площадке ЖК «${c.name}» нет работ ${c.idleDays} дн. Уточните сроки у застройщика, прежде чем вносить деньги.`,
    fits: (c) => c.stage !== model.builtStage && c.idleDays >= T.idleDays
      && c.photos.filter((p) => p.date && T.idleActivities.includes(p.activity)).length >= T.idlePhotosMin,
    why: `простой от ${T.idleDays} дн. не подтверждён снимками`,
  },
  ok: {
    label: 'Есть в перечне', title: 'Объект в перечне ГАСК',
    text: (c) => `ЖК «${c.name}» есть в опубликованном перечне ГАСК. Договор долевого участия заключайте только с номером гарантии ЕО или разрешения МИО.`,
    fits: (c) => c.inPublishedList === 'yes', why: 'объекта нет в опубликованном перечне ГАСК или перечень не сверен',
  },
}
const TPLS = Object.keys(TPL) as Tpl[]
const firstFit = (c: Complex, cur?: Tpl): Tpl => (cur && TPL[cur].fits(c) ? cur : TPLS.find((k) => TPL[k].fits(c)) ?? 'basis')
// Страница-идея: подписчиков, рассылок и согласования в MVP нет — ничего не отправляется и не имитируется (ТЗ §4)
const IDEA = 'Макет: сообщения не отправляются — рассылки, подписчиков и согласования в MVP нет'
const NOW = new Date().toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' })

/** Жителям — шапка с главным блоком «конструктор уведомления + превью на телефоне», ниже аудитория и FAQ */
// пока ЖК не загружены (прямой переход на /residents), выбирать объект не из чего — макет ждёт данных
export default function Residents() {
  const { complexes } = useStore()
  return complexes.length ? <ResidentsMock /> : <div className="empty">Загружаем объекты…</div>
}

function ResidentsMock() {
  const { complexes, riskOf } = useStore()
  const scored = useMemo(() => complexes.map((c) => ({ c, r: riskOf(c) })).sort((a, b) => b.r.score - a.r.score), [complexes, riskOf])
  const [cid, setCid] = useState(scored[0].c.id)
  const [pick, setPick] = useState(false)
  const [ch, setCh] = useState<'push' | 'tg'>('push')
  const [tpl, setTpl] = useState<Tpl>(() => firstFit(scored[0].c))
  const [msg, setMsg] = useState('')
  const cur = scored.find((x) => x.c.id === cid)!
  const c = cur.c
  const title = TPL[tpl].title
  const text = msg || TPL[tpl].text(c)
  const fits = TPL[tpl].fits(c)
  const count = (z: Zone) => scored.filter((x) => isAssessed(x.r) && x.r.zone === z).length
  const fitN = (k: Tpl) => scored.filter((x) => TPL[k].fits(x.c)).length
  /** Плитка шаблона: выбрать его; если к текущему объекту он не подходит — самый рискованный объект, к которому подходит */
  const pickTpl = (k: Tpl) => {
    const to = TPL[k].fits(c) ? c : scored.find((x) => TPL[k].fits(x.c))?.c
    if (to) setCid(to.id)
    setTpl(k); setMsg('')
  }

  return (
    <>
      <Hero crumb="Жителям" title="Предупредить жителей"
        info={<span><b className="bold">Идея для развития · макет, сообщения не отправляются.</b> Не входит в MVP: подписчиков, рассылок и согласования нет. В рабочей версии отправка — только после решения уполномоченного органа</span>}
        board={<Board kind="residents">
          <Kpi big em="flag" tone="red" value={count('red') + count('yellow')} label="ЖК можно предупредить" hint="красная и жёлтая зоны"
            onClick={() => { setCid(scored[0].c.id); setTpl(firstFit(scored[0].c, tpl)); setMsg('') }} />
          <div className="sb-col">
            <Cap aside="только оценённые ЖК">Кого касается</Cap>
            <Shares parts={[
              { key: 'red', label: 'Красная зона', value: count('red'), tone: 'red', to: '/complexes?zone=red' },
              { key: 'yellow', label: 'Жёлтая зона', value: count('yellow'), tone: 'yellow', to: '/complexes?zone=yellow' },
            ]} />
          </div>
          <div className="sb-sep" />
          <div className="sb-col">
            <Cap>Шаблоны · подходят к ЖК</Cap>
            <Row em="docs" label="«Проверьте основание»" value={fitN('basis')} onClick={() => pickTpl('basis')} active={tpl === 'basis'} />
            <Row em="hourglass" label="«Стройка стоит»" value={fitN('idle')} onClick={() => pickTpl('idle')} active={tpl === 'idle'} />
            <Row em="city" label="«Есть в перечне»" value={fitN('ok')} onClick={() => pickTpl('ok')} active={tpl === 'ok'} />
          </div>
        </Board>}>
        <div className="panel" style={{ gridTemplateColumns: 'minmax(0, 1fr) 380px' }}>
          <div className="pl">
            <h2 className="t-h2" style={{ marginBottom: 12 }}>Уведомление</h2>
            <div className="frow"><label>Объект</label>
              <button className="input row between" onClick={() => setPick(true)} style={{ textAlign: 'left' }}><span className="row" style={{ gap: 8 }}>ЖК «{c.name}»<RiskScore r={cur.r} /></span><IcChevronDown size={16} /></button>
            </div>
            <div className="frow"><label>Шаблон</label><Seg value={tpl} onChange={(v) => { setTpl(v); setMsg('') }} items={TPLS.map((k) => [k, TPL[k].label])} /></div>
            {!fits && <div className="frow"><span /><span className="t-sm" style={{ color: 'var(--amber-ink)' }}>Шаблон не подходит к объекту: {TPL[tpl].why}. Выберите другой.</span></div>}
            <div className="frow top"><label>Текст</label>
              <div><textarea className="input" maxLength={240} value={text} onChange={(e) => setMsg(e.target.value)} /><div className="t-xs c2" style={{ marginTop: 4, textAlign: 'right' }}>{text.length} / 240</div></div>
            </div>
            <div className="frow"><label>Канал</label><Seg value={ch} onChange={setCh} items={[['push', 'Push в приложении'], ['tg', 'Telegram-бот']]} /></div>
            <div className="frow"><label>Кому</label><span>жителям района <b className="bold">{c.district || 'не указан'}</b><span className="c2"> · подписчиков нет: канала рассылки в MVP нет</span></span></div>
            <div className="frow"><span /><div className="row wrap-f" style={{ gap: 12 }}>
              <button className="btn y" disabled title={IDEA}><IcSend />Отправить на согласование · макет</button>
              <span className="t-sm c2">{IDEA}</span>
            </div></div>
          </div>
          <div className="pr" style={{ alignItems: 'center', justifyContent: 'center', padding: 20, gap: 10 }}>
            <span className="t-sm c2">Как увидит житель — макет</span>
            <div className="phone" style={{ width: 260, height: 530, boxShadow: 'none' }}>
              <div className="phone-scr">
                <div style={{ textAlign: 'center', color: '#fff', paddingTop: 44, textShadow: '0 1px 10px rgba(0,0,0,.4)' }}>
                  <div style={{ fontSize: 14, fontWeight: 500 }}>{NOW}</div>
                  <div style={{ fontSize: 64, fontWeight: 600, lineHeight: 1 }}>09:41</div>
                </div>
                <div className="notif fade" key={text + ch} style={{ marginTop: 24 }}>
                  <div className="row" style={{ gap: 8, marginBottom: 4 }}>
                    <span style={{ width: 20, height: 20, borderRadius: 6, background: ch === 'push' ? 'var(--yellow)' : '#26A5E4', color: ch === 'push' ? '#000' : '#fff', display: 'grid', placeItems: 'center', fontSize: 11, fontWeight: 700 }}>{ch === 'push' ? 'S' : 'T'}</span>
                    <span style={{ fontSize: 12, fontWeight: 600 }}>{ch === 'push' ? 'SAQ QURYLYS · ГАСК ВКО' : '@saq_vko_bot'}</span>
                    <span className="c2" style={{ marginLeft: 'auto', fontSize: 12 }}>сейчас</span>
                  </div>
                  <div style={{ fontWeight: 600 }}>{title}</div>
                  <div style={{ marginTop: 2 }}>{text}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </Hero>

      <Faq title="Вопросы об уведомлениях"
        items={[
          ['Это работает?', 'Нет, это идея для развития. Сейчас можно только собрать текст по данным объекта и посмотреть макет: подписчиков, рассылок и согласования в MVP нет, ничего не отправляется.'],
          ['Кто решал бы, что отправлять?', 'Аналитик готовит уведомление, руководитель согласует. Автоматически система ничего жителям не отправляет.'],
          ['Какие данные о жителях нужны?', 'Никаких персональных данных: уведомление уходит подписчикам района в приложении или Telegram-боте.'],
          ['Почему не только Instagram?', 'Пост в ленте легко пропустить, а реклама ЖК приходит адресно. Push по району доходит до тех, кто живёт рядом со стройкой.'],
        ]} />

      {pick && (
        <Modal title="Выберите объект" onClose={() => setPick(false)}>
          {scored.map(({ c: x, r }) => <Cell key={x.id} inset icon={<img className="th" src={coverSrc(x)} alt="" style={{ width: 44, height: 44, borderRadius: 12 }} />} title={x.name} sub={x.address}
            end={<><RiskScore r={r} />{x.id === cid && <IcCheck size={18} style={{ color: 'var(--text)' }} />}</>} onClick={() => { setCid(x.id); setTpl(firstFit(x, tpl)); setMsg(''); setPick(false) }} />)}
        </Modal>
      )}
    </>
  )
}
