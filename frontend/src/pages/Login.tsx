import { useNavigate } from 'react-router-dom'
import { ArrowRight, KeyRound } from 'lucide-react'

export default function Login() {
  const nav = useNavigate()
  return (
    <div className="login">
      <div className="login-img">
        <img src="/img/city-panorama.jpg" alt="Усть-Каменогорск" />
        <div className="veil" />
        <div style={{ position: 'absolute', left: 48, right: 48, bottom: 48, color: '#f5f1e8' }}>
          <div className="label" style={{ color: 'rgba(255,255,255,.7)', marginBottom: 20 }}>Өскемен · Восточно-Казахстанская область</div>
          <h1 className="display d-xxl" style={{ color: '#fff' }}>SAQ<br /><span className="serif-i">Qurylys</span></h1>
          <p style={{ maxWidth: 440, marginTop: 24, fontSize: 16, color: 'rgba(255,255,255,.85)' }}>Цифровой помощник уполномоченного органа: мониторинг рекламы долевого строительства и риск-скоринг жилых комплексов.</p>
        </div>
      </div>
      <div className="login-form">
        <div className="row" style={{ justifyContent: 'space-between' }}><span className="label">ДЭР · ГАСК · Управление цифровизации ВКО</span><span className="label">RU · KZ</span></div>
        <div style={{ margin: 'auto 0', maxWidth: 400, width: '100%' }} className="stack">
          <h2 className="display d-l" style={{ marginBottom: 20 }}>Вход для <span className="serif-i">сотрудников</span></h2>
          <label className="field"><span className="label">Логин</span><input className="input" defaultValue="v.nurlanova" /></label>
          <label className="field"><span className="label">Пароль</span><input className="input" type="password" defaultValue="demo-password" /></label>
          <button className="btn primary" style={{ height: 46, justifyContent: 'space-between', marginTop: 8 }} onClick={() => nav('/')}>Войти <ArrowRight size={16} /></button>
          <button className="btn ghost" style={{ height: 46, justifyContent: 'center' }} onClick={() => nav('/')}><KeyRound size={15} />Войти по ЭЦП (NCALayer)</button>
          <p className="muted" style={{ fontSize: 12, marginTop: 12 }}>Внутренняя система. Решения о нарушениях принимает сотрудник; ИИ формирует рекомендацию.</p>
        </div>
        <div className="label">Codemasters 8.0 · Astana Hub × Oskemen · 2026</div>
      </div>
    </div>
  )
}
