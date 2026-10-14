import { useNavigate } from 'react-router-dom'
import { Button, PasswordInput, TextInput, Theme } from '@carbon/react'
import { ArrowRight, Password } from '@carbon/icons-react'

/** Вход — раскладка главной SIERA: тонкая верхняя линейка с датой, центрированный знак, фото на всю ширину, курсивная строка */
export default function Login() {
  const nav = useNavigate()
  const now = new Date()
  return (
    <Theme theme="g100">
      <div className="siera">
        <div className="siera-bar">
          <div className="l"><span>{now.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }).toUpperCase()}</span><span>{now.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })} ALMT ◐</span></div>
          <div className="mark">SAQ QURYLYS</div>
          <div className="r"><span>ДЭР · ГАСК · ВКО</span><span>RU / KZ ■</span></div>
        </div>
        <div className="siera-hero">
          <img src="/img/city-panorama.jpg" alt="Усть-Каменогорск" />
          <div className="tag">Строится под контролем.</div>
          <form className="siera-form" onSubmit={(e) => { e.preventDefault(); nav('/') }}>
            <div className="cap"><span>Вход для сотрудников</span><span>■</span></div>
            <TextInput id="login" labelText="Логин" defaultValue="v.nurlanova" />
            <PasswordInput id="pwd" labelText="Пароль" defaultValue="demo-password" showPasswordLabel="Показать" hidePasswordLabel="Скрыть" />
            <Button type="submit" renderIcon={ArrowRight} style={{ maxWidth: 'none', width: '100%' }}>Войти</Button>
            <Button kind="tertiary" renderIcon={Password} style={{ maxWidth: 'none', width: '100%' }} onClick={() => nav('/')}>Войти по ЭЦП (NCALayer)</Button>
          </form>
        </div>
        <div className="siera-cols">
          <div className="sq">Өскемен</div>
          <div>Мониторинг рекламы<br />долевого строительства</div>
          <div>Цифровой помощник уполномоченного органа. ИИ находит рекламу и считает риск, решение о нарушении принимает сотрудник.</div>
        </div>
      </div>
    </Theme>
  )
}
