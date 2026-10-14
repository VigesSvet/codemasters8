import { useState } from 'react'
import { Button, InlineNotification, Select, SelectItem, Stack, TextArea } from '@carbon/react'
import { SendAlt } from '@carbon/icons-react'
import { PageHeader } from '../components/ui'
import { useStore } from '../lib/store'

export default function Residents() {
  const { complexes, notify } = useStore()
  const [cid, setCid] = useState('c2')
  const c = complexes.find((x) => x.id === cid)!
  const [msg, setMsg] = useState('')
  const text = msg || `ЖК «${c.name}» отсутствует в перечне объектов, которым разрешено привлекать деньги дольщиков. Перед сделкой проверьте основание.`
  return (
    <>
      <PageHeader crumbs={[['SAQ', '/'], ['Жителям']]} title="Уведомления жителям" sub="Идея для развития, не входит в MVP: ГАСК предупреждает жителей push-уведомлением или в Telegram-боте, а не только постом в Instagram." />
      <div className="page" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 560px) 1fr', gap: 64 }}>
        <Stack gap={6}>
          <InlineNotification kind="info" lowContrast hideCloseButton title="Только после решения уполномоченного органа" subtitle="Без персональных данных и без автоматической отправки." />
          <Select id="obj" labelText="Объект" value={cid} onChange={(e) => setCid(e.target.value)}>{complexes.map((x) => <SelectItem key={x.id} value={x.id} text={`ЖК «${x.name}»`} />)}</Select>
          <TextArea id="msg" labelText="Текст уведомления" rows={5} value={text} onChange={(e) => setMsg(e.target.value)} maxCount={240} enableCounter />
          <Select id="ch" labelText="Канал" defaultValue="push"><SelectItem value="push" text="Push · мобильное приложение" /><SelectItem value="tg" text="Telegram-бот" /></Select>
          <Button renderIcon={SendAlt} onClick={() => notify('Отправлено на согласование руководителю', 'info')}>На согласование</Button>
        </Stack>
        <div className="phone">
          <div className="phone-scr">
            <div style={{ textAlign: 'center', color: '#fff', paddingTop: 54, textShadow: '0 1px 10px rgba(0,0,0,.4)' }}>
              <div style={{ fontSize: 14 }}>среда, 15 октября</div>
              <div style={{ fontSize: 68, fontWeight: 300, lineHeight: 1 }}>09:41</div>
            </div>
            <div className="notif" style={{ marginTop: 28 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
                <div style={{ width: 20, height: 20, borderRadius: 5, background: '#0f62fe', color: '#fff', display: 'grid', placeItems: 'center', fontSize: 9, fontWeight: 700 }}>SQ</div>
                <span style={{ fontSize: 11, fontWeight: 600 }}>SAQ QURYLYS · ГАСК ВКО</span>
                <span style={{ marginLeft: 'auto', fontSize: 11, color: '#6f6f6f' }}>сейчас</span>
              </div>
              <div style={{ fontWeight: 600 }}>Проверьте ЖК перед сделкой</div>
              <div style={{ marginTop: 2 }}>{text}</div>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
