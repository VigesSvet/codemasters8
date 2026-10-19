import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { isDemo } from '../lib/provenance'
import { model } from '../lib/risk'
import { safeNext } from '../lib/session'
import { useStore } from '../lib/store'
import { IcCheck, IcKey } from '../ui/icons'

/** Вход — как страницы входа сервисов Яндекса: фото слева, справа короткая форма с жёлтой кнопкой.
 *  Пилот: выбор сотрудника без пароля; его имя становится автором всех действий в журнале */
export default function Login() {
  const { analysts, user, login, mode, complexes } = useStore()
  const nav = useNavigate()
  const [sp] = useSearchParams()
  const next = safeNext(sp.get('next'))
  const [pick, setPick] = useState<string | null>(user?.id ?? null)
  const chosen = analysts.find((a) => a.id === pick)
  // контрольные сценарии ТЗ §5 — только если их ЖК есть в данных (тестовый набор, старые БД); в стартовом наборе их нет
  const cases = model.cases.flatMap((k) => { const c = complexes.find((x) => x.id === k.complexId); return c ? [{ k, c }] : [] })

  const enter = (who = chosen, to = next) => {
    if (!who) return
    login(who)
    nav(to, { replace: true })
  }

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
        <div className="row between"><div className="logo"><img src="/logo.svg" alt="" className="mark" />SAQ Qurylys</div><Link to="/about" className="link t-sm">О системе</Link></div>
        <form onSubmit={(e) => { e.preventDefault(); enter() }}>
          <h1 className="t-h1">Войдите в рабочее место</h1>
          <p className="c2" style={{ margin: '4px 0 12px', lineHeight: '21px' }}>Для сотрудников уполномоченного органа. ИИ находит рекламу и считает риск — решение принимаете вы.</p>
          <div className="who-list" role="radiogroup" aria-label="Сотрудник">
            {analysts.map((a) => (
              <button key={a.id} type="button" role="radio" aria-checked={pick === a.id} className={`cell who ${pick === a.id ? 'on' : ''}`}
                onClick={() => setPick(a.id)} onDoubleClick={() => { setPick(a.id); enter(a) }}>
                <span className="ava">{a.short}</span>
                <div className="main"><div className="ttl">{a.fullName || a.name}</div><div className="sub">{a.role}{user?.id === a.id && ' · вы вошли'}</div></div>
                {pick === a.id && <div className="end"><IcCheck size={22} strokeWidth={2.5} style={{ color: 'var(--text)' }} /></div>}
              </button>
            ))}
            {analysts.length === 0 && <div className="t-sm c2" style={{ padding: 12 }}>{mode === 'loading' ? 'Загружаем список сотрудников…' : 'Список сотрудников пуст'}</div>}
          </div>
          <button type="submit" className="btn y full" style={{ marginTop: 6 }} disabled={!chosen}>{chosen ? `Войти как ${chosen.name}` : 'Выберите сотрудника'}</button>
          <button type="button" className="btn g full" disabled title="Подключим после пилота"><IcKey />Войти по ЭЦП (NCALayer) · после пилота</button>
          {cases.length > 0 && (
            <div className="row wrap-f t-sm" style={{ gap: 6, marginTop: 2 }}>
              <span className="c2">Войти и открыть сценарий ТЗ §5:</span>
              {cases.map(({ k, c }) => (
                <button key={k.id} type="button" className="chip" style={{ height: 30, padding: '0 12px' }} disabled={!chosen}
                  title={chosen ? `${k.title}${isDemo(c) ? ' — вымышленный ЖК' : ''}` : 'Сначала выберите сотрудника'} onClick={() => enter(chosen, `/complexes/${c.id}?tab=risk`)}>{k.id}</button>
              ))}
            </div>
          )}
          <p className="t-xs c2" style={{ lineHeight: '17px', marginTop: 4 }}>Вход пилота без пароля. В рабочей версии — ЭЦП (NCALayer) или SSO; автор каждого действия попадает в журнал.</p>
          {mode === 'snapshot' && <p className="t-xs c2" style={{ lineHeight: '17px' }}>Сервер недоступен: откроется офлайн-копия стартового набора — только для просмотра.</p>}
        </form>
        <div className="t-xs c3">© 2026 SAQ Qurylys · реальные открытые данные и публичная реклама, оценка — предварительная</div>
      </div>
    </div>
  )
}
