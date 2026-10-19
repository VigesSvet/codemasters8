// Панель «Горячие точки рекламы» над главной картой (Dashboard, переключатель): районы по убыванию публикаций с предложением
// оплаты у ЖК без подтверждённого основания, затем отметок «проверить» / «подозрение». Контекст для планирования проверок,
// не вывод о нарушении. Расчёт — lib/hotspots.ts, стили — styles/hot.css
import { useMemo } from 'react'
import type { Complex } from '../data/types'
import { hotAreas } from '../lib/hotspots'
import { useT } from '../lib/i18n'
import { IcClose } from '../ui/icons'
import '../styles/hot.css'

export default function HotspotPanel({ complexes, onClose, left = 412 }: { complexes: readonly Complex[]; onClose: () => void; left?: number }) {
  const { t } = useT()
  const areas = useMemo(() => hotAreas(complexes), [complexes])
  const days = complexes.find((c) => c.screening)?.screening?.counts.windowDays ?? 120
  const max = Math.max(1, ...areas.map((a) => a.offers))
  return (
    <section className="hot" style={{ left }} aria-labelledby="hot-h">
      <div className="hot-h"><h4 id="hot-h">{t('hot.title')}</h4><button type="button" className="c3" onClick={onClose} aria-label={t('common.close')}><IcClose size={16} /></button></div>
      {areas.length === 0 ? <p className="hot-empty">{t('hot.empty')}</p> : (
        <ol className="hot-list">
          {areas.slice(0, 5).map((a, n) => (
            <li key={a.key}>
              <span className="hot-n">{n + 1}</span>
              <div className="hot-b">
                <b>{a.district ?? (a.street ? t('hot.near', { s: a.street }) : t('hot.noStreet'))}{a.part && <span className="hot-part"> · {t(`hot.part.${a.part}`)}</span>}</b>
                <div className="hot-bar" aria-hidden><i className="nb" style={{ width: `${(a.offersNoBasis / max) * 100}%` }} /><i className="ok" style={{ width: `${((a.offers - a.offersNoBasis) / max) * 100}%` }} /></div>
                <small className="hot-nums" title={t('hot.line', { a: a.offers, b: a.offersNoBasis, c: a.flagged })}>
                  <span>{t('hot.offers')} <b>{a.offers}</b></span><span className="nb">{t('hot.noBasis')} <b>{a.offersNoBasis}</b></span><span>{t('hot.flagged')} <b>{a.flagged}</b></span>
                </small>
                <small className="hot-names">{a.names.join(' · ')}</small>
              </div>
            </li>
          ))}
        </ol>
      )}
      <p className="hot-note">{t('hot.legend', { d: days })}</p>
      <p className="hot-note">{t('hot.note')}</p>
    </section>
  )
}
