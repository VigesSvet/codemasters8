// Лендинг SAQ Qurylys (/about, без входа) в стиле «манифест»: жёлтый постер, крупный кегль, шаги — нумерованные строки.
// Что делает система, как работает, принципы; цифры — из тех же данных, что видит аналитик (стартовый набор или сервер)
import { Link } from 'react-router-dom'
import { Emblem, type EmblemName } from '../components/emblems'
import { statusOf } from '../lib/screening'
import { useStore } from '../lib/store'

const STEPS: { t: string; d: string }[] = [
  { t: 'Собираем рекламу', d: 'Instagram, TikTok, Krisha.kz, Telegram и заявки жителей через бота — с файлами, датой и ссылкой на оригинал.' },
  { t: 'Находим признаки продажи', d: 'Правила и ИИ читают текст, OCR кадров и речь в роликах: цена, бронь, рассрочка, «старт продаж» — с дословной цитатой.' },
  { t: 'Сверяем с реестром', d: 'Открытые данные Homeportal и Казреестра: есть ли у ЖК разрешение МИО, гарантия ЕО или банк. Адрес — по 2ГИС.' },
  { t: 'Решает аналитик', d: 'Очередь проверки, сигнальная карточка с доказательствами и журнал: кто, когда и на чём основал решение.' },
]
const FEATURES: { em: EmblemName; t: string; d: string }[] = [
  { em: 'traffic', t: 'Карта риска', d: 'Все ЖК города со статусом «Проверки по рекламе» и формальным баллом 0–100. Схема, спутник и 3D.' },
  { em: 'photos', t: 'Спутник: было и стало', d: 'Архив снимков с 2014 года по точке ЖК: шторка «до/после» и период стройки по документам.' },
  { em: 'flag', t: 'Сигнальная карточка', d: 'Снимок риска на момент создания: основания, публикации, цитаты — печатная версия A4 для руководства.' },
  { em: 'inbox', t: 'Очередь проверки', d: 'Самые рискованные публикации сверху, перетаскивание по статусам, исполнитель и комментарий к решению.' },
  { em: 'city', t: 'Объекты из рекламы', d: 'ЖК, которых нет в реестре, но их продают в рекламе: создать карточку, привязать или отклонить.' },
  { em: 'database', t: 'Провенанс каждой строки', d: 'Источник, время сбора и ссылка у каждого факта; гипотеза ИИ всегда помечена как гипотеза.' },
]
const PRINCIPLES = [
  ['ИИ рекомендует — решает человек', 'Ни одна публикация не становится нарушением без решения аналитика с основанием.'],
  ['Оценка — не приговор', '«Проверка по рекламе» предварительная: по ЖК без проверенных сведений риск не оценивается.'],
  ['Только открытые данные', 'Публичная реклама и открытые реестры. Секреты подключений — на сервере, не в браузере.'],
] as const

function useNumbers() {
  const { complexes, ads, developers } = useStore()
  const suspect = complexes.filter((c) => statusOf(c) === 'suspect').length
  const check = complexes.filter((c) => statusOf(c) === 'check').length
  return { complexes: complexes.length, ads: ads.length, developers: developers.length, suspect, check }
}
const n = (x: number) => x.toLocaleString('ru-RU')

const Logo = () => <Link to="/about" className="ld-logo"><img src="/logo.svg" alt="" className="mark" />SAQ Qurylys</Link>

export default function Landing() {
  const k = useNumbers()
  return (
    <div className="ld ld-c">
      <header className="ld-top"><div className="ld-wrap"><Logo /><Link to="/login" className="ld-btn-black">Войти</Link></div></header>
      <section className="ld-hero"><div className="ld-wrap">
        <h1>Деньги дольщиков —<br />только по&nbsp;разрешению</h1>
        <div className="ld-hero-row">
          <p>SAQ Qurylys ловит рекламу, где квартиры продают раньше, чем государство разрешило привлекать деньги. Для ДЭР и ГАСК Восточно-Казахстанской области.</p>
          <Link to="/login" className="ld-btn-black big">Открыть систему →</Link>
        </div>
      </div></section>
      <section className="ld-giant"><div className="ld-wrap">
        <div><b>{n(k.complexes)}</b><span>ЖК<br />на контроле</span></div>
        <div><b>{n(k.ads)}</b><span>публикаций<br />разобрано</span></div>
        <div><b>{n(k.suspect + k.check)}</b><span>ЖК ждут<br />проверки</span></div>
      </div></section>
      <section className="ld-sec" id="how"><div className="ld-wrap">
        <h2>Четыре шага</h2>
        {STEPS.map((s, i) => <div key={s.t} className="ld-row"><i>{i + 1}</i><h3>{s.t}</h3><p>{s.d}</p></div>)}
      </div></section>
      <section className="ld-photo"><img src="/img/city-panorama.jpg" alt="Усть-Каменогорск" /><blockquote>«ИИ формирует рекомендацию — решение о нарушении принимает аналитик»<span>принцип системы</span></blockquote></section>
      <section className="ld-sec" id="features"><div className="ld-wrap">
        <h2>Что внутри</h2>
        <div className="ld-feat">{FEATURES.map((f) => <div key={f.t}><Emblem name={f.em} size={40} /><h3>{f.t}</h3><p>{f.d}</p></div>)}</div>
      </div></section>
      <section className="ld-sec black" id="rules"><div className="ld-wrap">
        <div className="ld-rules">{PRINCIPLES.map(([t, d]) => <div key={t}><h3>{t}</h3><p>{d}</p></div>)}</div>
      </div></section>
      <footer className="ld-foot"><div className="ld-wrap"><span>SAQ Qurylys · Codemasters 8.0 · 2026</span><span>Усть-Каменогорск</span></div></footer>
    </div>
  )
}
