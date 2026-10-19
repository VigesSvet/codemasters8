// Блок разбора публикации «Кто продаёт»: где ещё встречается номер из публикации (GET /api/sellers/by-ad/{id}), связанные
// аккаунты и цена за м² против медианы города. Гипотеза для проверки, в балл не входит; без сервера блока нет.
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { plural } from './ui'
import { useT } from '../lib/i18n'
import { HYPOTHESIS, METHOD_LABEL, belowText, fmtPerM2, phoneHint, sellerOfAd, type AdSellerHint } from '../lib/sellers'
import { useStore } from '../lib/store'
import '../styles/sellers.css'

export function SellerHints({ adId }: { adId: string }) {
  const { mode, revision } = useStore()
  const { t } = useT()
  const [h, setH] = useState<AdSellerHint | null>(null)
  useEffect(() => {
    if (mode !== 'api') return
    let on = true
    // ошибка подсказки не мешает разбору публикации — блок просто не показывается
    sellerOfAd(adId).then((x) => on && setH(x), () => on && setH(null))
    return () => { on = false }
  }, [adId, mode, revision])
  const s = h?.seller
  const p = h?.price
  if (!h || ((!s || (!s.phones.length && !s.linked)) && !p)) return null
  const to = s ? `/sellers?key=${encodeURIComponent(s.groupKey)}` : '/sellers'
  return (
    <div className="card">
      <div className="card-head"><h3>{t('sellers.adTitle')}</h3><span className="tag yellow" title={h.note}>гипотеза для проверки</span></div>
      <div className="sl-hint">
        {s?.phones.map((ph) => (
          <div key={ph.digits} className="ln"><b>{ph.phone}</b>{ph.whatsapp && <span className="tag gray">WhatsApp</span>}
            <Link to={to}>{phoneHint(ph)}</Link></div>
        ))}
        {s?.linked && (
          <div className="ln t-sm">Продавец «{s.title}»: {s.accounts} {plural(s.accounts, ['аккаунт', 'аккаунта', 'аккаунтов'])}, {s.ads} публ. — связи: {[...new Set(s.links.map((l) => l.label))].join(', ')}.
            <Link to={to}>Открыть схему →</Link></div>
        )}
        {p?.perM2 != null && (
          <div className="ln t-sm">Цена за м² <b>{p.from ? 'от ' : ''}{fmtPerM2(p.perM2)}</b>
            {p.anomaly ? <span className="tag red">{belowText(p.belowPct)}</span> : <span className="c2">— {belowText(p.belowPct)}</span>}
            <span className="c2">медиана {p.medianBasis === 'rooms' ? 'по числу комнат' : 'города'} {fmtPerM2(p.median)}, {p.method ? METHOD_LABEL[p.method] : ''}</span>
            <Link to="/sellers?tab=price">Все цены →</Link></div>
        )}
        {p?.fullPayment && <div className="ln t-sm"><span className="tag yellow">100% оплата</span><span className="c2">«{p.fullPayment.quote}»</span></div>}
        <p className="t-xs c2" style={{ lineHeight: '17px' }}>{HYPOTHESIS[0].toUpperCase() + HYPOTHESIS.slice(1)}: общий номер бывает у агентства, которое продаёт несколько ЖК. Цена ниже рынка и «100% оплата» — контекст, не нарушение; в балл риска не входят.</p>
      </div>
    </div>
  )
}
