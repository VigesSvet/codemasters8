import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { DevAffiliations } from '../components/DevAffiliations'
import { DevScreeningBlock, Disclaimer, NoScreening, ScreeningPic, ScreeningTag, StatusCounts, WorstList } from '../components/Screening'
import { Cell, Faq, Hero, LinkCols, Modal, RiskScore, RiskZone, SectionHead, Seg, fmtDate } from '../components/ui'
import type { ScreeningStatus } from '../data/types'
import { from } from '../lib/nav'
import { DEMO_ORG, NO_DATA, REGISTRY_NOTE, UNKNOWN_DEV, isAdvertised, isDemo, isRegistry, portfolioText, progressText } from '../lib/provenance'
import { isAssessed, model } from '../lib/risk'
import { DEV_SCREENING_LABEL, SCREENING_FILTER_LABEL, SCREENING_ORDER, anyScreening, compareDevScreening, inScreening, parseScreeningParam, statusOf } from '../lib/screening'
import { matches } from '../lib/search'
import { useStore } from '../lib/store'
import { IcHelp, IcSearch } from '../ui/icons'
import { Board, Cap, Kpi, Ring, Row, Shares } from '../components/StatBoard'

const COLORS = ['#e4402d', '#0065d9', '#00b341', '#f59d00', '#1d1f26', '#7a5af8', '#00a1a1']
type F = 'all' | 'risk' | 'unfinished' | 'ads'

/** Застройщики — шапка с поиском по БИН, карточки-профили, FAQ «Как проверить застройщика» */
export default function Developers() {
  const { complexes, ads, developers, riskOf } = useStore()
  // открытый профиль — в адресе (?dev=d1): на него ведут ссылки «Застройщик» с карты и из карточек
  const [sp, setSp] = useSearchParams()
  const open = sp.get('dev')
  const setOpen = (id: string | null) => setSp((p) => { const n = new URLSearchParams(p); if (id) n.set('dev', id); else n.delete('dev'); return n }, { replace: true })
  const [q, setQ] = useState('')
  const [f, setF] = useState<F>('all')
  // «Проверка по рекламе» — в адресе (?screening=suspect), как у списка новостроек: на неё ведут ссылки с карты и сводки
  const screening = parseScreeningParam(sp.get('screening'))
  const setScreening = (v: ScreeningStatus[]) => setSp((p) => { const n = new URLSearchParams(p); if (v.length) n.set('screening', v.join(',')); else n.delete('screening'); return n }, { replace: true })
  const screenOn = anyScreening(developers)
  const back = from('Застройщики', `/developers${sp.toString() ? `?${sp}` : ''}`)
  const data = useMemo(() => developers.map((d, i) => {
    const objs = complexes.filter((c) => c.developerId === d.id).map((c) => ({ c, r: riskOf(c) }))
    // наибольший риск — среди оценённых ЖК; не оценённый (реестр без проверенных сведений) — только если других нет
    const rank = (x: (typeof objs)[number]) => (isAssessed(x.r) ? x.r.score : -1)
    const max = objs.reduce<(typeof objs)[number] | undefined>((m, x) => (!m || rank(x) > rank(m) ? x : m), undefined)
    return { d, objs, max, color: COLORS[i % COLORS.length], adsCount: ads.filter((a) => objs.some((o) => o.c.id === a.complexId)).length }
  // подозрение первыми (худший ЖК застройщика по «Проверке по рекламе»), затем по максимальному формальному риску
  }).sort((a, b) => compareDevScreening(a.d.screening, b.d.screening)
    || (b.max ? (isAssessed(b.max.r) ? b.max.r.score : -1) : -2) - (a.max ? (isAssessed(a.max.r) ? a.max.r.score : -1) : -2)),
  [complexes, ads, developers, riskOf])
  type D = (typeof data)[number]
  const test: Record<F, (x: D) => boolean> = { all: () => true, risk: (x) => x.max?.r.zone === 'red', unfinished: (x) => x.d.unfinished > 0, ads: (x) => x.adsCount > 0 }
  const rows = data.filter((x) => test[f](x) && inScreening(statusOf(x.d), screening) && (!q || matches(q, x.d.name, x.d.bin, x.d.director)))
  const scrN = (k: ScreeningStatus) => data.filter((x) => statusOf(x.d) === k).length
  const only = (k: ScreeningStatus) => screening.length === 1 && screening[0] === k
  const pickScr = (k: ScreeningStatus | null) => { setScreening(k ? [k] : []); if (k) setF('all') }
  const cur = data.find((x) => x.d.id === open)
  const initials = (n: string) => n.replace(/ТОО «|»/g, '').split(/[\s-]+/).slice(0, 2).map((w) => w[0]).join('').toUpperCase()

  return (
    <>
      <Hero crumb="Застройщики" title="Застройщики Усть-Каменогорска" info={`Данные по БИН — для сверки с реестром юрлиц.${developers.some(isDemo) ? ' В демо-наборе компании вымышлены.' : ''}${developers.some(isRegistry) ? ' Застройщики из открытого реестра — название и БИН источника, портфель не проверен.' : ''}`}
        board={screenOn ? <Board kind="devs">
          <Ring value={data.length} label="застройщиков" parts={[
            { key: 'suspect', label: 'Подозрение', value: scrN('suspect'), tone: 'red', active: only('suspect') },
            { key: 'check', label: 'Требуют проверки', value: scrN('check'), tone: 'yellow', active: only('check') },
            { key: 'ok', label: 'Без замечаний', value: scrN('ok'), tone: 'green', active: only('ok') },
            { key: 'unknown', label: 'Нет данных', value: scrN('unknown'), tone: 'gray', active: only('unknown') },
          ]} />
          <div className="sb-col">
            <Cap aside="предварительная оценка — не вывод о нарушении">Проверка по рекламе</Cap>
            <Shares bar={false} parts={[
              { key: 'suspect', label: 'Подозрение: продажа без разрешения', value: scrN('suspect'), tone: 'red', onClick: () => pickScr(only('suspect') ? null : 'suspect'), active: only('suspect') },
              { key: 'check', label: 'Требуют проверки', value: scrN('check'), tone: 'yellow', onClick: () => pickScr(only('check') ? null : 'check'), active: only('check') },
              { key: 'ok', label: 'Без замечаний: в реестре, сданы или проверены', value: scrN('ok'), tone: 'green', onClick: () => pickScr(only('ok') ? null : 'ok'), active: only('ok') },
              { key: 'unknown', label: 'Нет данных для оценки', value: scrN('unknown'), tone: 'gray', onClick: () => pickScr(only('unknown') ? null : 'unknown'), active: only('unknown') },
            ]} />
          </div>
          <div className="sb-sep" />
          <div className="sb-col">
            <Cap>Портфель</Cap>
            <Row em="devs" label="Все застройщики" value={data.length} onClick={() => { setF('all'); pickScr(null) }} active={f === 'all' && !screening.length} />
            <Row em="feed" tone="blue" label="С рекламой в ленте" value={data.filter(test.ads).length} onClick={() => setF(f === 'ads' ? 'all' : 'ads')} active={f === 'ads'} />
            <Row em="crane" tone="yellow" label="Есть незавершённые" value={data.filter(test.unfinished).length} onClick={() => setF(f === 'unfinished' ? 'all' : 'unfinished')} active={f === 'unfinished'} />
          </div>
        </Board> : <Board kind="devs">
          <Kpi big em="devs" value={data.length} label="застройщиков" onClick={() => setF('all')} active={f === 'all'} />
          <div className="sb-col">
            <Cap>Портфель</Cap>
            <Row em="traffic" tone="red" label="С объектами в красной зоне" value={data.filter(test.risk).length} onClick={() => setF(f === 'risk' ? 'all' : 'risk')} active={f === 'risk'} />
            <Row em="crane" tone="yellow" label="Есть незавершённые" value={data.filter(test.unfinished).length} onClick={() => setF(f === 'unfinished' ? 'all' : 'unfinished')} active={f === 'unfinished'} />
          </div>
          <div className="sb-sep" />
          <div className="sb-col">
            <Cap>Объекты</Cap>
            <Row em="feed" tone="blue" label="С рекламой в ленте" value={data.filter(test.ads).length} onClick={() => setF(f === 'ads' ? 'all' : 'ads')} active={f === 'ads'} />
            <Row em="city" label="Объектов на контроле" value={complexes.length} to="/complexes" />
          </div>
        </Board>}>
        <div className="panel" style={{ gridTemplateColumns: '1fr' }}>
          <div className="pl row" style={{ gap: 12, paddingBottom: 16 }}>
            <label className="fsearch" style={{ height: 48 }}><IcSearch /><input placeholder="Название, БИН или руководитель" value={q} onChange={(e) => setQ(e.target.value)} /></label>
            <Seg value={f} onChange={setF} items={[['all', 'Все'], ['risk', 'С риском'], ['unfinished', 'Не сдают'], ['ads', 'С рекламой']]} />
            <button className="btn y" style={{ minWidth: 200 }} onClick={() => document.getElementById('devs')?.scrollIntoView({ behavior: 'smooth' })}>Показать {rows.length}</button>
          </div>
          {screenOn ? (
            <div className="scr-chips" style={{ padding: '0 16px 16px', alignItems: 'center' }} role="group" aria-label="Проверка по рекламе">
              <span className="t-sm bold" style={{ marginRight: 4 }}>Проверка по рекламе:</span>
              <button className={`chip all ${!screening.length ? 'on' : ''}`} aria-pressed={!screening.length} onClick={() => pickScr(null)}>Все<span className="n num">{data.length}</span></button>
              {SCREENING_ORDER.map((k) => (
                <button key={k} className={`chip ${only(k) ? 'on' : ''}`} aria-pressed={only(k)} title={DEV_SCREENING_LABEL[k]} onClick={() => pickScr(only(k) ? null : k)}>
                  <ScreeningPic status={k} />{SCREENING_FILTER_LABEL[k]}<span className="n num">{scrN(k)}</span>
                </button>
              ))}
              <Disclaimer />
            </div>
          ) : <div style={{ padding: '0 16px 16px' }}><NoScreening /></div>}
        </div>
      </Hero>

      <div id="devs" className="row between" style={{ marginBottom: 16 }}>
        <div className="sorter" style={{ padding: 0 }}><b>{rows.length} застройщиков:</b><span>{screenOn ? 'сначала подозрение, затем по максимальному риску' : 'по максимальному риску'}</span></div>
      </div>
      {rows.length === 0 && <div className="empty">Никого не нашли — попробуйте другой БИН или название</div>}
      <div className="gcards" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: 16 }}>
        {rows.map(({ d, objs, max, adsCount, color }) => (
          // карточка — не <button>: внутри ссылки на худшие ЖК
          <div key={d.id} className="devcard" role="button" tabIndex={0} onClick={() => setOpen(d.id)}
            onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); setOpen(d.id) } }}>
            <div className="row" style={{ gap: 14, alignItems: 'flex-start' }}>
              <span className="devlogo" style={{ background: color }}>{initials(d.name)}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="t-h3" style={{ fontSize: 18 }}>{d.name}</div>
                <div className="t-sm c2" style={{ marginTop: 2 }}>БИН {d.bin || NO_DATA}{d.registered && ` · с ${new Date(d.registered).getFullYear()} года`}{isDemo(d) && ` · ${DEMO_ORG}`}{isRegistry(d) && ` · ${REGISTRY_NOTE}`}{isAdvertised(d) && ' · по рекламе, не проверено'}</div>
              </div>
              {max && isAssessed(max.r) && <RiskScore r={max.r} />}
            </div>
            {d.screening && (
              <div className="stack" style={{ gap: 8 }}>
                <span className="row wrap-f" style={{ gap: 8 }}><ScreeningTag s={d.screening} dev /></span>
                {d.screening.counts.complexes > 1 && <StatusCounts c={d.screening.counts} />}
                <WorstList worst={d.screening.worst} state={back} />
              </div>
            )}
            {d.id === UNKNOWN_DEV && <div className="t-sm c2 row" style={{ gap: 6 }}><IcHelp size={14} />ЖК по рекламе, застройщик которых не установлен</div>}
            {isRegistry(d) || isAdvertised(d)
              ? <div className="t-sm c2">{portfolioText(d, objs.length)} · {adsCount} публ. в ленте</div>
              : <div className="t-sm"><b className="bold">{d.objects - d.unfinished} сдано</b><span className="c2"> · </span><b className="bold" style={{ color: d.unfinished ? 'var(--red)' : undefined }}>{d.unfinished} строится</b><span className="c2"> · {adsCount} публ. в ленте</span></div>}
            <div className="objs">{objs.filter(({ c }) => c.cover).slice(0, 4).map(({ c }) => <img key={c.id} src={c.cover} alt={c.name} title={c.name} />)}</div>
            {!d.screening && <div className="row wrap-f" style={{ gap: 6 }}>{objs.map(({ c, r }) => <span key={c.id} className={`tag ${isAssessed(r) ? r.zone : 'gray'}`}>{c.name}</span>)}</div>}
          </div>
        ))}
      </div>

      <Faq title="Как проверить застройщика" sub="Что смотреть, прежде чем отправлять объект на проверку."
        items={[
          ['Где взять сведения о компании по БИН?', 'В открытом реестре юрлиц: дата регистрации, руководитель, учредители. Молодая компания с одним незавершённым объектом — повод присмотреться внимательнее, но не факт нарушения: это наблюдение вне балла O2.'],
          ['Как понять, может ли застройщик привлекать деньги дольщиков?', 'Нужна гарантия Казахстанской жилищной компании (ЕО) либо разрешение МИО вместе с проектным финансированием банка. Перечень таких ЖК ГАСК публикует в Instagram.'],
          ['Что делать, если у застройщика несколько объектов?', 'Откройте профиль: риск считается по каждому ЖК отдельно, в карточке видно, какой объект тянет балл вверх.'],
          ['Что значит «Подозрение» и «Требует проверки» у застройщика?', 'Это «Проверка по рекламе» — предварительная автоматическая оценка по рекламе и открытым данным, не вывод о нарушении. «Подозрение» — у застройщика есть ЖК, который рекламируют с приёмом денег (бронь, взнос, ДДУ, рассрочка), а основания привлекать деньги дольщиков в открытых данных нет. «Требует проверки» — сведений не хватает или они расходятся. Решение принимает аналитик после проверки.'],
          ['Почему у застройщика с хорошей историей может быть красная зона?', 'Зона считается по объекту, а не по компании. Красная — когда по новому ЖК установлено отсутствие основания (R1) и есть прямое предложение внести деньги (R2), независимо от прошлых сданных домов.'],
        ]} />

      <LinkCols cols={[
        [['С объектами в красной зоне', '/complexes?zone=red', data.filter(test.risk).length], ['С незавершёнными объектами', '/developers', data.filter(test.unfinished).length]],
        [['Новостройки на контроле', '/complexes', complexes.length], ['Реклама застройщиков', '/ads', ads.length]],
        [['Очередь проверки', '/queue', ads.filter((a) => a.status === 'new' || a.status === 'needs_check').length], ['Методика скоринга', '/method', model.factors.length]],
      ]} />

      {cur && (
        <Modal title={cur.d.name} onClose={() => setOpen(null)}
          footer={<Link to={`/complexes?dev=${cur.d.id}`} className="btn y">Все объекты застройщика</Link>}>
          <div className="row" style={{ gap: 14, padding: '4px 24px 12px' }}>
            <span className="devlogo" style={{ background: cur.color }}>{initials(cur.d.name)}</span>
            <div><div className="bold">{portfolioText(cur.d, cur.objs.length)}</div>{isDemo(cur.d) && <div className="t-xs c2">{DEMO_ORG[0].toUpperCase() + DEMO_ORG.slice(1)}</div>}{isRegistry(cur.d) && <div className="t-xs c2">{REGISTRY_NOTE[0].toUpperCase() + REGISTRY_NOTE.slice(1)}</div>}<div className="t-sm c2">{cur.max && <RiskZone r={cur.max.r} />}</div></div>
          </div>
          {cur.d.screening && <div style={{ padding: '0 24px 12px' }}><DevScreeningBlock s={cur.d.screening} state={from('Застройщики', `/developers?dev=${cur.d.id}`)} /></div>}
          {[['БИН', cur.d.bin || NO_DATA], ['Руководитель', cur.d.director || NO_DATA], ['Регистрация', cur.d.registered ? fmtDate(cur.d.registered) : NO_DATA], ['Сайт', cur.d.site ?? '—'], ['Instagram', cur.d.instagram ?? '—']].map(([k, v]) => (
            <Cell key={k} title={<span className="c2 t-sm">{k}</span>} end={<span style={{ color: 'var(--text)' }}>{v}</span>} />
          ))}
          <DevAffiliations id={cur.d.id} />
          <SectionHead title="Объекты на контроле" />
          {cur.objs.map(({ c, r }) => (
            <Cell key={c.id} inset to={`/complexes/${c.id}`} icon={c.screening ? <ScreeningPic status={c.screening.status} size={40} /> : <img className="th" src={c.cover || undefined} alt="" style={{ width: 48, height: 48, borderRadius: 12 }} />}
              title={`ЖК «${c.name}»`} sub={c.screening ? `${c.screening.label} · ${progressText(c)}` : progressText(c)} end={(!c.screening || isAssessed(r)) && <RiskScore r={r} />} chevron />
          ))}
        </Modal>
      )}
    </>
  )
}
