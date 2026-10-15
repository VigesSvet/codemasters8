import { useNavigate } from 'react-router-dom'
import { IcBuilding, IcKey } from '../ui/icons'

/** Вход — как страницы входа сервисов Яндекса: фото слева, справа короткая форма с жёлтой кнопкой */
export default function Login() {
  const nav = useNavigate()
  return (
    <div className="login">
      <div className="hero">
        <img src="/img/city-panorama.jpg" alt="Усть-Каменогорск" />
        <div className="cap">
          <span className="tag white" style={{ marginBottom: 16 }}>ДЭР · ГАСК · ВКО</span>
          <h2>Строится<br />под контролем</h2>
          <p style={{ marginTop: 14, maxWidth: 520, fontSize: 17, lineHeight: '23px', opacity: 0.9 }}>Мониторинг рекламы долевого строительства и риск-скоринг жилых комплексов Өскемена</p>
        </div>
      </div>
      <div className="side">
        <div className="row between">
          <div className="logo"><span className="mark"><IcBuilding /></span>SAQ Qurylys</div>
          <span className="t-sm c2">RU · KZ</span>
        </div>
        <form onSubmit={(e) => { e.preventDefault(); nav('/') }}>
          <h1 className="t-h1">Войдите в рабочее место</h1>
          <p className="c2" style={{ margin: '4px 0 20px', lineHeight: '21px' }}>Для сотрудников уполномоченного органа. ИИ находит рекламу и считает риск — решение принимаете вы.</p>
          <input className="input" defaultValue="v.nurlanova" aria-label="Логин" />
          <input className="input" type="password" defaultValue="demo-password" aria-label="Пароль" />
          <button type="submit" className="btn y full" style={{ marginTop: 6 }}>Войти</button>
          <button type="button" className="btn g full" onClick={() => nav('/')}><IcKey />Войти по ЭЦП (NCALayer)</button>
        </form>
        <div className="t-xs c3">© 2026 SAQ Qurylys · данные демонстрационные</div>
      </div>
    </div>
  )
}
