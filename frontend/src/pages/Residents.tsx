import { useMemo, useState } from 'react'
import { Cell, Faq, Hero, HeroTile, Modal, Score, Seg } from '../components/ui'
import type { Zone } from '../data/types'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcBell, IcBuilding, IcCheck, IcChevronDown, IcPhone, IcSend, IcShield, IcUsers } from '../ui/icons'

type Tpl = 'warn' | 'idle' | 'ok'
const TPL: Record<Tpl, [string, (n: string) => string]> = {
  warn: ['Проверьте ЖК перед сделкой', (n) => `ЖК «${n}» нет в перечне объектов, которым разрешено привлекать деньги дольщиков. Перед сделкой проверьте основание.`],
  idle: ['Стройка приостановлена', (n) => `На площадке ЖК «${n}» нет работ больше трёх месяцев. Уточните сроки у застройщика, прежде чем вносить деньги.`],
  ok: ['Объект в перечне ГАСК', (n) => `ЖК «${n}» есть в перечне объектов с гарантией. Договор долевого участия заключайте только с номером гарантии.`],
}

/** Жителям — шапка с главным блоком «конструктор уведомления + превью на телефоне», ниже аудитория и FAQ */
export default function Residents() {
  const { complexes, ads, notify } = useStore()
  const scored = useMemo(() => complexes.map((c) => ({ c, r: scoreComplex(c, ads) })).sort((a, b) => b.r.score - a.r.score), [complexes, ads])
  const [cid, setCid] = useState(scored[0].c.id)
  const [pick, setPick] = useState(false)
  const [ch, setCh] = useState<'push' | 'tg'>('push')
  const [tpl, setTpl] = useState<Tpl>('warn')
  const [msg, setMsg] = useState('')
  const cur = scored.find((x) => x.c.id === cid)!
  const c = cur.c
  const title = TPL[tpl][0]
  const text = msg || TPL[tpl][1](c.name)
  const reach = { push: 4200, tg: 1350 }[ch]
  const count = (z: Zone) => scored.filter((x) => x.r.zone === z).length

  return (
    <>
      <Hero crumb="Жителям" title="Предупредить жителей"
        info="Идея для развития, не входит в MVP. Отправка — только после решения уполномоченного органа"
        tiles={<>
          <HeroTile icon={<IcUsers size={32} />} label="Подписчиков push" value="4 200" />
          <HeroTile icon={<IcSend size={32} />} label="Подписчиков в Telegram" value="1 350" tone="blue" />
          <HeroTile icon={<IcBuilding size={32} />} label="Можно предупредить" value={count('red') + count('yellow')} tone="red" />
          <HeroTile icon={<IcBell size={32} />} label="Отправлено за месяц" value={3} />
          <HeroTile icon={<IcShield size={32} />} label="Ждут согласования" value={1} tone="yellow" />
          <HeroTile icon={<IcPhone size={32} />} label="Звонков в ГАСК" value="−38%" tone="green" />
        </>}>
        <div className="panel" style={{ gridTemplateColumns: 'minmax(0, 1fr) 380px' }}>
          <div className="pl">
            <h2 className="t-h2" style={{ marginBottom: 12 }}>Уведомление</h2>
            <div className="frow"><label>Объект</label>
              <button className="input row between" onClick={() => setPick(true)} style={{ textAlign: 'left' }}><span className="row" style={{ gap: 8 }}>ЖК «{c.name}»<Score value={cur.r.score} zone={cur.r.zone} /></span><IcChevronDown size={16} /></button>
            </div>
            <div className="frow"><label>Шаблон</label><Seg value={tpl} onChange={(v) => { setTpl(v); setMsg('') }} items={[['warn', 'Нет в перечне'], ['idle', 'Стройка стоит'], ['ok', 'Всё в порядке']]} /></div>
            <div className="frow top"><label>Текст</label>
              <div><textarea className="input" maxLength={240} value={text} onChange={(e) => setMsg(e.target.value)} /><div className="t-xs c2" style={{ marginTop: 4, textAlign: 'right' }}>{text.length} / 240</div></div>
            </div>
            <div className="frow"><label>Канал</label><Seg value={ch} onChange={setCh} items={[['push', 'Push в приложении'], ['tg', 'Telegram-бот']]} /></div>
            <div className="frow"><label>Кому</label><span>жителям района <b className="bold">{c.district}</b> · ~{reach.toLocaleString('ru')} человек</span></div>
            <div className="frow"><span /><button className="btn y" style={{ justifySelf: 'start' }} onClick={() => notify('Отправлено на согласование руководителю')}><IcSend />Отправить на согласование</button></div>
          </div>
          <div className="pr" style={{ alignItems: 'center', justifyContent: 'center', padding: 20 }}>
            <div className="phone" style={{ width: 260, height: 530, boxShadow: 'none' }}>
              <div className="phone-scr">
                <div style={{ textAlign: 'center', color: '#fff', paddingTop: 44, textShadow: '0 1px 10px rgba(0,0,0,.4)' }}>
                  <div style={{ fontSize: 14, fontWeight: 500 }}>среда, 15 октября</div>
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
          ['Кто решает, что отправлять?', 'Аналитик готовит уведомление, руководитель согласует. Автоматически система ничего жителям не отправляет.'],
          ['Какие данные о жителях нужны?', 'Никаких персональных данных: уведомление уходит подписчикам района в приложении или Telegram-боте.'],
          ['Почему не только Instagram?', 'Пост в ленте легко пропустить, а реклама ЖК приходит адресно. Push по району доходит до тех, кто живёт рядом со стройкой.'],
        ]} />

      {pick && (
        <Modal title="Выберите объект" onClose={() => setPick(false)}>
          {scored.map(({ c: x, r }) => <Cell key={x.id} inset icon={<img className="th" src={x.cover} alt="" style={{ width: 44, height: 44, borderRadius: 12 }} />} title={x.name} sub={x.address}
            end={<><Score value={r.score} zone={r.zone} />{x.id === cid && <IcCheck size={18} style={{ color: 'var(--text)' }} />}</>} onClick={() => { setCid(x.id); setMsg(''); setPick(false) }} />)}
        </Modal>
      )}
    </>
  )
}
