import { useState } from 'react'
import { Send, ShieldCheck } from 'lucide-react'
import { PageHead } from '../components/ui'
import { useStore } from '../lib/store'

export default function Residents() {
  const { complexes, notify } = useStore()
  const [cid, setCid] = useState('c2')
  const c = complexes.find((x) => x.id === cid)!
  const [msg, setMsg] = useState('')
  const text = msg || `ЖК «${c.name}» отсутствует в перечне объектов, которым разрешено привлекать деньги дольщиков. Перед сделкой проверьте основание на saq.gov.kz.`

  return (
    <div className="page">
      <PageHead idx="M4" label="Идея для развития · не входит в MVP" title={<>Жителям — <span className="serif-i">вовремя</span></>}>
        <p className="ink2" style={{ maxWidth: 680, marginTop: 18 }}>Вместо одних публикаций в Instagram ГАСК может предупреждать жителей push-уведомлением или в Telegram-боте, когда человек смотрит рекламу или ищет конкретный ЖК. Отправка — только после решения уполномоченного органа.</p>
      </PageHead>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: 64, alignItems: 'start' }}>
        <div className="stack" style={{ gap: 20 }}>
          <label className="field"><span className="label">Объект</span>
            <select className="select" value={cid} onChange={(e) => setCid(e.target.value)}>{complexes.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select>
          </label>
          <label className="field"><span className="label">Текст уведомления</span><textarea className="textarea" value={text} onChange={(e) => setMsg(e.target.value)} /></label>
          <div className="grid g-3" style={{ gap: 0, border: '1px solid var(--line)' }}>
            {[['Канал', 'Push · Telegram'], ['Аудитория', 'Подписчики района, ~4 200'], ['Согласование', 'Требуется']].map(([k, v], i) => (
              <div key={k} style={{ padding: 16, borderRight: i < 2 ? '1px solid var(--line)' : 0 }}><div className="label">{k}</div><div style={{ marginTop: 6, fontWeight: 500 }}>{v}</div></div>
            ))}
          </div>
          <div className="row"><button className="btn primary" onClick={() => notify('Отправлено на согласование руководителю')}><Send size={14} />На согласование</button><span className="row muted" style={{ gap: 6 }}><ShieldCheck size={14} />Без ПДн, без автоматической отправки</span></div>
        </div>
        <div className="phone">
          <div className="phone-scr">
            <div style={{ textAlign: 'center', color: '#fff', paddingTop: 54, textShadow: '0 1px 10px rgba(0,0,0,.4)' }}>
              <div style={{ fontSize: 14 }}>среда, 15 октября</div>
              <div style={{ fontSize: 68, fontWeight: 300, lineHeight: 1 }}>09:41</div>
            </div>
            <div className="notif fade-in" key={text} style={{ marginTop: 28 }}>
              <div className="row" style={{ gap: 8, marginBottom: 4 }}>
                <div style={{ width: 20, height: 20, borderRadius: 5, background: '#111', color: '#fff', display: 'grid', placeItems: 'center', fontSize: 9, fontWeight: 700 }}>SQ</div>
                <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: '.02em' }}>SAQ QURYLYS · ГАСК ВКО</span>
                <span style={{ marginLeft: 'auto', fontSize: 11, color: '#666' }}>сейчас</span>
              </div>
              <div style={{ fontWeight: 600 }}>Осторожно: проверьте ЖК перед сделкой</div>
              <div style={{ color: '#333', marginTop: 2 }}>{text}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
