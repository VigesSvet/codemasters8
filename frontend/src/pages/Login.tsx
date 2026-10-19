import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import LangSwitch from '../components/LangSwitch'
import { roleIn, useT } from '../lib/i18n'
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
  const i = useT()
  const { t } = i
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
        <img src="/img/city-panorama.jpg" alt={t('common.city')} />
        <div className="cap">
          <span className="tag white" style={{ marginBottom: 16 }}>{t('login.agencies')}</span>
          <h2>{t('login.h1')}<br />{t('login.h2')}</h2>
          <p style={{ marginTop: 14, maxWidth: 520, fontSize: 17, lineHeight: '23px', opacity: 0.9 }}>{t('login.lead')}</p>
        </div>
      </div>
      <div className="side">
        <div className="row" style={{ gap: 16 }}><div className="logo"><img src="/logo.svg" alt="" className="mark" />SAQ Qurylys</div><LangSwitch /><Link to="/about" className="link t-sm">{t('login.about')}</Link></div>
        <form onSubmit={(e) => { e.preventDefault(); enter() }}>
          <h1 className="t-h1">{t('login.title')}</h1>
          <p className="c2" style={{ margin: '4px 0 12px', lineHeight: '21px' }}>{t('login.intro')}</p>
          <div className="who-list" role="radiogroup" aria-label={t('login.role')}>
            {analysts.map((a) => { const r = roleIn(i, a); return (
              <button key={a.id} type="button" role="radio" aria-checked={pick === a.id} className={`cell who ${pick === a.id ? 'on' : ''}`}
                onClick={() => setPick(a.id)} onDoubleClick={() => { setPick(a.id); enter(a) }}>
                <span className="ava">{r.short}</span>
                <div className="main"><div className="ttl">{r.title}</div><div className="sub">{r.dept}{user?.id === a.id && t('login.youAreIn')}</div></div>
                {pick === a.id && <div className="end"><IcCheck size={22} strokeWidth={2.5} style={{ color: 'var(--text)' }} /></div>}
              </button>
            ) })}
            {analysts.length === 0 && <div className="t-sm c2" style={{ padding: 12 }}>{mode === 'loading' ? t('login.loadingRoles') : t('login.noRoles')}</div>}
          </div>
          <button type="submit" className="btn y full" style={{ marginTop: 6 }} disabled={!chosen}>{chosen ? t('login.enterAs', { role: roleIn(i, chosen).title.toLowerCase() }) : t('login.pickRole')}</button>
          <button type="button" className="btn g full" disabled title={t('login.afterPilot')}><IcKey />{t('login.eds')}</button>
          {cases.length > 0 && (
            <div className="row wrap-f t-sm" style={{ gap: 6, marginTop: 2 }}>
              <span className="c2">{t('login.cases')}</span>
              {cases.map(({ k, c }) => (
                <button key={k.id} type="button" className="chip" style={{ height: 30, padding: '0 12px' }} disabled={!chosen}
                  title={chosen ? `${k.title}${isDemo(c) ? t('login.fictional') : ''}` : t('login.pickFirst')} onClick={() => enter(chosen, `/complexes/${c.id}?tab=risk`)}>{k.id}</button>
              ))}
            </div>
          )}
          <p className="t-xs c2" style={{ lineHeight: '17px', marginTop: 4 }}>{t('login.pilot')}</p>
          {mode === 'snapshot' && <p className="t-xs c2" style={{ lineHeight: '17px' }}>{t('login.snapshot')}</p>}
        </form>
        <div className="t-xs c3">{t('login.copy')}</div>
      </div>
    </div>
  )
}
