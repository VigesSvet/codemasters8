import { useState } from 'react'
import { Cell, Modal, PageTitle, Seg } from '../components/ui'
import { useStore } from '../lib/store'
import { IcBuilding, IcCheck, IcInfo, IcSend } from '../ui/icons'

export default function Residents() {
  const { complexes, notify } = useStore()
  const [cid, setCid] = useState('c2')
  const [pick, setPick] = useState(false)
  const [ch, setCh] = useState<'push' | 'tg'>('push')
  const c = complexes.find((x) => x.id === cid)!
  const [msg, setMsg] = useState('')
  const text = msg || `ЖК «${c.name}» нет в перечне объектов, которым разрешено привлекать деньги дольщиков. Перед сделкой проверьте основание.`
  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Жителям']]} title="Предупредить жителей" sub="Идея для развития, не входит в MVP: ГАСК предупреждает жителей push-уведомлением или в Telegram-боте, а не только постом в Instagram." />
      <div style={{ display: 'grid', gridTemplateColumns: '520px 1fr', gap: 48, alignItems: 'start' }}>
        <div className="stack">
          <div className="card card-pad row" style={{ background: 'var(--blue-card)', alignItems: 'flex-start' }}>
            <IcInfo /><div><div className="bold">Только после решения органа</div><div className="t-sm" style={{ marginTop: 4, opacity: 0.8 }}>Без персональных данных и без автоматической отправки.</div></div>
          </div>
          <div className="card">
            <Cell icon={<span className="ico"><IcBuilding size={20} /></span>} title="Объект" end={<span style={{ color: 'var(--text)' }}>{c.name}</span>} chevron onClick={() => setPick(true)} />
          </div>
          <div className="card">
            <div className="field top"><div className="body"><label>Текст уведомления · {text.length}/240</label><textarea maxLength={240} value={text} onChange={(e) => setMsg(e.target.value)} /></div></div>
          </div>
          <div className="card" style={{ padding: 8 }}><Seg full value={ch} onChange={setCh} items={[['push', 'Push в приложении'], ['tg', 'Telegram-бот']]} /></div>
          <div className="card" style={{ padding: 16 }}><button className="btn y full" onClick={() => notify('Отправлено на согласование руководителю')}><IcSend size={20} />Отправить на согласование</button></div>
        </div>
        <div className="phone" style={{ justifySelf: 'center' }}>
          <div className="phone-scr">
            <div style={{ textAlign: 'center', color: '#fff', paddingTop: 54, textShadow: '0 1px 10px rgba(0,0,0,.4)' }}>
              <div style={{ fontSize: 15, fontWeight: 500 }}>среда, 15 октября</div>
              <div style={{ fontSize: 72, fontWeight: 600, lineHeight: 1, letterSpacing: '-0.03em' }}>09:41</div>
            </div>
            <div className="notif" style={{ marginTop: 28 }}>
              <div className="row" style={{ gap: 8, marginBottom: 4 }}>
                <span style={{ width: 20, height: 20, borderRadius: 6, background: 'var(--yellow)', display: 'grid', placeItems: 'center', fontSize: 11, fontWeight: 700 }}>S</span>
                <span style={{ fontSize: 12, fontWeight: 600 }}>{ch === 'push' ? 'SAQ QURYLYS · ГАСК ВКО' : 'Telegram · @saq_vko_bot'}</span>
                <span className="c2" style={{ marginLeft: 'auto', fontSize: 12 }}>сейчас</span>
              </div>
              <div style={{ fontWeight: 600 }}>Проверьте ЖК перед сделкой</div>
              <div style={{ marginTop: 2 }}>{text}</div>
            </div>
          </div>
        </div>
      </div>
      {pick && (
        <Modal title="Объект" onClose={() => setPick(false)}>
          {complexes.map((x) => <Cell key={x.id} title={x.name} sub={x.address} end={x.id === cid ? <IcCheck size={22} strokeWidth={2.5} style={{ color: 'var(--text)' }} /> : undefined} onClick={() => { setCid(x.id); setMsg(''); setPick(false) }} />)}
        </Modal>
      )}
    </>
  )
}
