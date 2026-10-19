// Лендинг SAQ Qurylys (/about, а для гостя — и главная /) в духе apple.com: крупная типографика, полноэкранные кадры
// продукта, бенто с живыми цифрами. Кадры — реальные экраны системы (public/landing/*.jpg), цифры — из данных системы
import { Link } from 'react-router-dom'
import { statusOf } from '../lib/screening'
import { useStore } from '../lib/store'

const IMG = (n: string) => `/landing/${n}.jpg`

function useNums() {
  const { complexes, ads } = useStore()
  const flagged = complexes.filter((c) => { const s = statusOf(c); return s === 'suspect' || s === 'check' }).length
  return { complexes: complexes.length, ads: ads.length, flagged }
}
const n = (x: number) => x.toLocaleString('ru-RU')
const Logo = () => <Link to="/about" className="lp-logo"><img src="/logo.svg" alt="" />SAQ Qurylys</Link>

export default function Landing() {
  const k = useNums()
  return (
    <div className="lp lp-apple">
      <header className="lp-nav"><div className="lp-w"><Logo /><nav><a href="#see">Обзор</a><a href="#sat">Спутник</a><a href="#bento">Возможности</a><Link to="/method">Методика</Link></nav><Link to="/login" className="lp-pill">Войти</Link></div></header>
      <section className="lp-hero" id="see">
        <h1>Стройка.<br />Под контролем.</h1>
        <p>Вся реклама новостроек города, риск каждого ЖК и проверка разрешений — в одном окне.</p>
        <div className="lp-links"><Link to="/login">Открыть систему ›</Link><Link to="/method">Как считается риск ›</Link></div>
        <img className="lp-hero-img" src={IMG('map-3d')} alt="3D-карта ЖК" />
      </section>
      <section className="lp-black" id="sat">
        <h2>Было.<span> Стало.</span></h2>
        <p>Снимки места с 2014 года. Сдвиньте шторку — и видно, когда на пустыре вырос котлован.</p>
        <img src={IMG('satellite')} alt="Спутник: до и после" />
      </section>
      <section className="lp-bento-wrap" id="bento"><div className="lp-w">
        <h2>Каждая деталь — для проверки.</h2>
        <div className="lp-bento">
          <div className="t n"><span>На контроле</span><b>{n(k.complexes)}</b><em>жилых комплексов</em></div>
          <div className="t img2"><span>Сводка</span><h3>Светофор риска по городу.</h3><img src={IMG('ops-board')} alt="" /></div>
          <div className="t img3"><span>Объекты из рекламы</span><h3>Нет в реестре — есть в рекламе.</h3><img src={IMG('discovered')} alt="" /></div>
          <div className="t n y"><span>Разобрано</span><b>{n(k.ads)}</b><em>публикаций рекламы</em></div>
          <div className="t dark"><span>Видео</span><h3>Слышит, что говорят в роликах.</h3><p>Речь и надписи кадров — в анализ.</p></div>
          <div className="t n r"><span>Ждут проверки</span><b>{n(k.flagged)}</b><em>ЖК с признаками продажи</em></div>
          <div className="t sat"><span>Спутник</span><h3>С 2014 года.</h3><img src={IMG('satellite')} alt="" /></div>
        </div>
      </div></section>
      <section className="lp-row"><div className="lp-w">
        <img src={IMG('queue')} alt="Очередь проверки" />
        <div><span>Очередь проверки</span><h2>Сначала — самое рискованное.</h2><p>Публикации отсортированы по риску. Перетащите карточку — статус и автор решения попадут в журнал.</p><Link to="/login">Открыть очередь ›</Link></div>
      </div></section>
      <section className="lp-close"><h2>ИИ подсказывает.<br />Решает человек.</h2><p>Оценка системы — предварительная. Нарушение подтверждает только аналитик.</p><Link to="/login" className="lp-btn blue">Открыть систему</Link></section>
      <footer className="lp-foot"><div className="lp-w"><span>SAQ Qurylys · Codemasters 8.0 · 2026</span><span>Усть-Каменогорск</span></div></footer>
    </div>
  )
}
