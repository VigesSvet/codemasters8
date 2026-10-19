import { useEffect } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { SignalStatusTag } from '../components/SignalPanel'
import { Faq, Hero, Score, ZoneTag, fmtDT } from '../components/ui'
import type { SignalStatus } from '../data/types'
import { from } from '../lib/nav'
import { useStore } from '../lib/store'
import { IcClose, IcHelp } from '../ui/icons'
import { Board, Cap, Kpi, Row, Shares } from '../components/StatBoard'
import { RouteDue } from '../components/SignalRoute'
import { useT } from '../lib/i18n'
import { routeCounts } from '../lib/route'
import { useRoutes } from '../lib/useRoutes'

const STATUSES: SignalStatus[] = ['new', 'confirmed', 'returned']
const TITLE: Record<SignalStatus, string> = { new: 'Ждут решения', confirmed: 'Подтверждённые', returned: 'Возвращённые на доработку' }

/** Реестр сигнальных карточек (модуль 05): фильтр по статусу плитками шапки, строка открывает сигнал */
export default function Signals() {
  const { signals, complexes, mode, refreshSignals } = useStore()
  const nav = useNavigate()
  const [sp] = useSearchParams()
  const status = STATUSES.find((s) => s === sp.get('status'))
  const cid = sp.get('complex')
  const cx = cid ? complexes.find((c) => c.id === cid) : undefined
  const base = cid ? signals.filter((s) => s.complexId === cid) : signals
  const shown = status ? base.filter((s) => s.status === status) : base
  const n = (st: SignalStatus) => base.filter((s) => s.status === st).length
  const href = (st?: SignalStatus, keepComplex = true) => {
    const p = new URLSearchParams()
    if (st) p.set('status', st)
    if (cid && keepComplex) p.set('complex', cid)
    const q = p.toString()
    return q ? `/signals?${q}` : '/signals'
  }
  // сигнал и ЖК открываются с «← Сигналы» на эту же выборку: фильтр статуса и объекта сохраняется при возврате
  const back = from('Сигналы', `/signals${sp.toString() ? `?${sp}` : ''}`)
  // маршрут рассмотрения (T-79): этап, срок и просрочка по каждому сигналу — с сервера
  const { t } = useT()
  const routes = useRoutes()
  const rows = routes ? base.map((s) => routes.rows.get(s.id)).filter((r) => !!r) : []
  const rc = routeCounts(rows)
  const sla = (st: 'analysis' | 'approval') => rows.find((r) => r.stage === st && r.slaDays)?.slaDays

  // список мог поменяться у других аналитиков — перечитываем при открытии реестра; ошибка — баннером под шапкой
  useEffect(() => {
    if (mode === 'api') void refreshSignals()
  }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <Hero crumb="Сигналы" title="Сигнальные карточки"
        info="Сигнал фиксирует риск и доказательства по ЖК на момент создания. Подтверждает или возвращает его аналитик"
        actions={<Link to="/complexes?zone=red" className="btn o m">Объекты красной зоны</Link>}
        board={<Board kind="signals">
          <Kpi big em="flag" tone="red" value={base.length} label={cx ? `сигналов по ЖК «${cx.name}»` : 'сигналов всего'} to={href()} active={!status} />
          <div className="sb-sep" />
          <div className="sb-col">
            <Cap aside="подтверждает или возвращает аналитик">Решения по сигналам</Cap>
            <Shares parts={[
              { key: 'new', label: 'Ждут решения', value: n('new'), tone: 'yellow', to: href('new'), active: status === 'new' },
              { key: 'confirmed', label: 'Подтверждены', value: n('confirmed'), tone: 'red', to: href('confirmed'), active: status === 'confirmed' },
              { key: 'returned', label: 'Возвращены', value: n('returned'), tone: 'gray', to: href('returned'), active: status === 'returned' },
            ]} />
          </div>
          {routes && <>
            <div className="sb-sep" />
            <div className="sb-col">
              <Cap aside={sla('analysis') && sla('approval') ? t('route.capHint', { a: sla('analysis')!, b: sla('approval')! }) : undefined}>{t('route.cap')}</Cap>
              <Shares parts={[
                { key: 'analysis', label: t('route.analysis'), value: rc.analysis, tone: 'yellow' },
                { key: 'approval', label: t('route.approval'), value: rc.approval, tone: 'blue' },
              ]} />
              <Row em="timer" tone="red" label={t('route.overdue')} value={rc.overdue} />
            </div>
          </>}
        </Board>} />

      <div className="row between" style={{ marginBottom: 4, flexWrap: 'wrap' }}>
        <div className="row" style={{ gap: 12 }}>
          <h2 className="t-h2">{status ? TITLE[status] : 'Все сигналы'}</h2>
          {cid && <Link to={href(status, false)} className="tag outline" title="Показать по всем объектам">{cx ? `ЖК «${cx.name}»` : cid}<IcClose size={12} /></Link>}
        </div>
        {mode !== 'loading' && <span className="t-sm c2">{shown.length} из {signals.length}</span>}
      </div>
      {mode === 'snapshot' && <div className="ai-note" style={{ margin: '8px 0 16px' }}>Офлайн-копия: реестр сигналов на момент выгрузки, только просмотр. Карточка сигнала, решение по нему и PDF — с сервером (./dev.sh).</div>}

      {mode === 'loading' ? (
        <div className="empty">Загружаем сигналы…</div>
      ) : base.length === 0 ? (
        <div className="empty">
          {cid ? 'По этому объекту сигналов ещё не было.' : 'Сигналов пока нет.'} Сформируйте сигнал из карточки ЖК — кнопка «Сформировать сигнал».
          <div style={{ marginTop: 16 }}><Link to="/complexes?zone=red" className="btn y m">Объекты красной зоны</Link></div>
        </div>
      ) : shown.length === 0 ? (
        <div className="empty">Сигналов с таким статусом нет. <Link to={href()} className="link">Показать все</Link></div>
      ) : (
        <table className="ytable">
          <thead><tr><th>Код</th><th>Жилой комплекс</th><th>Риск</th><th>Подтверждённые факторы</th><th>Создан</th><th>Статус</th><th>{t('route.col')}</th><th>Решение</th></tr></thead>
          <tbody>
            {shown.map((s) => {
              const c = complexes.find((x) => x.id === s.complexId)
              return (
                <tr key={s.id} onClick={() => nav(`/signals/${s.id}`, { state: back })}>
                  <td className="b">{s.code}</td>
                  <td>
                    <div className="row" style={{ gap: 12 }}>
                      {c?.cover && <img src={c.cover} alt="" style={{ width: 56, height: 42, borderRadius: 8, objectFit: 'cover' }} />}
                      <div><Link to={`/complexes/${s.complexId}`} state={back} className="link" onClick={(e) => e.stopPropagation()}>ЖК «{s.complexName}»</Link>{c && <div className="t-sm c2">{[c.district, c.stage].filter(Boolean).join(' · ')}</div>}</div>
                    </div>
                  </td>
                  <td><div className="row" style={{ gap: 8 }}><Score value={s.score} zone={s.zone} /><ZoneTag zone={s.zone} /></div></td>
                  <td>
                    <div className="row wrap-f" style={{ gap: 4 }}>
                      {s.hits.map((h) => <span key={h} className="tag gray">{h}</span>)}
                      {s.hits.length === 0 && <span className="c3">—</span>}
                      {s.needsCheck && <span className="tag yellow">нужна проверка</span>}
                    </div>
                  </td>
                  <td className="t-sm">{fmtDT(s.createdAt)}<div className="c2">{s.createdBy}</div></td>
                  <td><SignalStatusTag s={s.status} /></td>
                  <td>{routes ? <RouteDue r={routes.rows.get(s.id)} now={routes.at} /> : <span className="c3">—</span>}</td>
                  <td className="t-sm">{s.decidedAt ? <>{s.decidedBy}<div className="c2">{fmtDT(s.decidedAt)}</div></> : <span className="c3">—</span>}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      <Faq title="Вопросы о сигналах"
        items={[
          ['Что такое сигнал?', 'Краткий отчёт по ЖК для проверки: данные объекта, факторы риска R1–R6 с доказательствами, ссылки на публикации, обращения и снимки. Сигнал формирует аналитик из карточки ЖК — по одному объекту одновременно может ждать решения только один сигнал.'],
          ['Кто решает и что фиксируется?', 'Решение принимает аналитик: «Подтвердить сигнал» или «Вернуть на доработку». Без комментария решение не сохранится. В журнал сигнала записываются автор, дата, время и комментарий, туда же — каждая выгрузка PDF.'],
          ['Можно ли отправить сигнал в госорган?', 'Нет, автоматической отправки нет. Сигнал — рекомендация для проверки, не юридическое заключение. Для внутреннего маршрута есть PDF и печатная версия с местом для отметки аналитика.'],
          ['Почему балл в сигнале отличается от текущего?', <>Сигнал — снимок на момент создания: дальше он не пересчитывается, чтобы было видно, на каком основании его сформировали. Если по объекту появились новые данные, карточка сигнала покажет текущий балл рядом. Как считается балл — в <Link className="link" to="/method">методике</Link>.</>],
        ]} />
      <div className="row t-sm c2" style={{ gap: 6, marginTop: 24 }}><IcHelp size={16} />Сигнал — рекомендация для проверки, не вывод о нарушении. Решение по сигналу принимает сотрудник.</div>
    </>
  )
}
