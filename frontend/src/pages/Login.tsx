import { useNavigate } from 'react-router-dom'
import { IcKey } from '../ui/icons'

/** Вход — композиция главной go.yandex: фото на весь экран, белая карточка со скруглением 48 */
export default function Login() {
  const nav = useNavigate()
  return (
    <div className="login">
      <div className="hero"><img src="/img/city-panorama.jpg" alt="Усть-Каменогорск" /></div>
      <div className="lhead">
        <div className="logo"><span className="mark" style={{ color: 'var(--text)' }}>S</span>SAQ <span className="sub">Qurylys</span></div>
        <span className="t-sm" style={{ opacity: 0.9 }}>RU · KZ</span>
      </div>
      <div className="ybar">
        <span className="tag dark">ДЭР ВКО</span>
        <span className="t-sm" style={{ maxWidth: 380, lineHeight: '19px' }}>Мониторинг рекламы долевого строительства и риск-скоринг жилых комплексов Өскемена</span>
      </div>
      <form className="lcard" onSubmit={(e) => { e.preventDefault(); nav('/') }}>
        <h1>Строится<br />под контролем</h1>
        <p className="c2" style={{ margin: '14px 0 24px', lineHeight: '22px' }}>Вход для сотрудников уполномоченного органа. ИИ находит рекламу и считает риск — решение принимаете вы.</p>
        <div className="stack">
          <input className="input" defaultValue="v.nurlanova" aria-label="Логин" />
          <input className="input" type="password" defaultValue="demo-password" aria-label="Пароль" />
          <button type="submit" className="btn y full" style={{ marginTop: 8 }}>Войти</button>
          <button type="button" className="btn o full" onClick={() => nav('/')}><IcKey size={20} />Войти по ЭЦП (NCALayer)</button>
        </div>
      </form>
    </div>
  )
}
