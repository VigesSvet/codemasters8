// Лендинг SAQ Qurylys (/about, а для гостя — и главная /) в духе apple.com: крупная типографика, полноэкранные кадры
// продукта, бенто с живыми цифрами. Кадры — реальные экраны системы (public/landing/*.jpg), цифры — из данных системы
import { Link } from 'react-router-dom'
import LangSwitch from '../components/LangSwitch'
import { useT } from '../lib/i18n'
import { statusOf } from '../lib/screening'
import { useStore } from '../lib/store'

const IMG = (n: string) => `/landing/${n}.jpg`

function useNums() {
  const { complexes, ads } = useStore()
  const flagged = complexes.filter((c) => { const s = statusOf(c); return s === 'suspect' || s === 'check' }).length
  return { complexes: complexes.length, ads: ads.length, flagged }
}
const Logo = () => <Link to="/about" className="lp-logo"><img src="/logo.svg" alt="" />SAQ Qurylys</Link>

export default function Landing() {
  const k = useNums()
  const { t, tn, num: n } = useT()
  return (
    <div className="lp lp-apple">
      <header className="lp-nav"><div className="lp-w"><Logo /><nav><a href="#see">{t('lp.navOverview')}</a><a href="#sat">{t('lp.navSat')}</a><a href="#bento">{t('lp.navFeatures')}</a><Link to="/method">{t('lp.navMethod')}</Link></nav><LangSwitch /><Link to="/login" className="lp-pill">{t('lp.signIn')}</Link></div></header>
      <section className="lp-hero" id="see">
        <h1>{t('lp.heroL1')}<br />{t('lp.heroL2')}</h1>
        <p>{t('lp.heroText')}</p>
        <div className="lp-links"><Link to="/login">{t('lp.openSystem')} ›</Link><Link to="/method">{t('lp.howRisk')} ›</Link><Link to="/check">{t('chk.cta')} ›</Link></div>
        <img className="lp-hero-img" src={IMG('map-3d')} alt={t('lp.heroAlt')} />
      </section>
      <section className="lp-black" id="sat">
        <h2>{t('lp.before')}<span>{t('lp.after')}</span></h2>
        <p>{t('lp.satText')}</p>
        <img src={IMG('satellite')} alt={t('lp.satAlt')} />
      </section>
      <section className="lp-bento-wrap" id="bento"><div className="lp-w">
        <h2>{t('lp.bentoTitle')}</h2>
        <div className="lp-bento">
          <div className="t n"><span>{t('lp.onControl')}</span><b>{n(k.complexes)}</b><em>{tn('lp.complexesN', k.complexes)}</em></div>
          <div className="t img2"><span>{t('lp.ops')}</span><h3>{t('lp.opsH')}</h3><img src={IMG('ops-board')} alt="" /></div>
          <div className="t img3"><span>{t('lp.disc')}</span><h3>{t('lp.discH')}</h3><img src={IMG('discovered')} alt="" /></div>
          <div className="t n y"><span>{t('lp.parsed')}</span><b>{n(k.ads)}</b><em>{tn('lp.adsN', k.ads)}</em></div>
          <div className="t dark"><span>{t('lp.video')}</span><h3>{t('lp.videoH')}</h3><p>{t('lp.videoP')}</p></div>
          <div className="t n r"><span>{t('lp.waiting')}</span><b>{n(k.flagged)}</b><em>{t('lp.flaggedN')}</em></div>
          <div className="t sat"><span>{t('lp.sat')}</span><h3>{t('lp.satH')}</h3><img src={IMG('satellite')} alt="" /></div>
        </div>
      </div></section>
      <section className="lp-row"><div className="lp-w">
        <img src={IMG('queue')} alt={t('lp.queue')} />
        <div><span>{t('lp.queue')}</span><h2>{t('lp.queueH')}</h2><p>{t('lp.queueP')}</p><Link to="/login">{t('lp.openQueue')} ›</Link></div>
      </div></section>
      <section className="lp-close"><h2>{t('lp.closeL1')}<br />{t('lp.closeL2')}</h2><p>{t('lp.closeP')}</p><Link to="/login" className="lp-btn blue">{t('lp.openSystem')}</Link></section>
      <footer className="lp-foot"><div className="lp-w"><span>SAQ Qurylys · Codemasters 8.0 · 2026</span><span>{t('common.city')}</span></div></footer>
    </div>
  )
}
