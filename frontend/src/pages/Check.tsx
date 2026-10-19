// «Проверь ЖК перед покупкой» (/check) — публичная страница для жителей, без входа, RU/KZ.
// Только факты открытого реестра об основании принимать деньги дольщиков (гарантия ЕО, разрешение ЖАО, участие банка, записи
// Казреестра) с источником, датой записи и ссылкой; пояснения со ссылками на ст. 7, 8, 18, 23 Закона (shared/law.json),
// чек-лист и оговорка. Ни балла риска, ни «Проверки по рекламе», ни решений аналитика здесь нет. Стили — styles/check.css
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import LangSwitch from '../components/LangSwitch'
import type { RegistryCard, SourceInfo } from '../data/types'
import { errorText } from '../lib/api'
import { CHECK_DOCS, adiletUrl, anyBasis, checkEntries, checkFacts, searchEntries, type CheckDoc, type CheckEntry, type FactRecord } from '../lib/check'
import { useT, type Key } from '../lib/i18n'
import { excerptsFor, law, lawArticle, type LawRef } from '../lib/law'
import { useRegistry } from '../lib/provenance'
import { useStore } from '../lib/store'
import { IcCheck, IcChevronLeft, IcClose, IcExternal, IcSearch } from '../ui/icons'
import '../styles/check.css'

/** Пояснения: документ, нормы (из shared/law.json) и статья, на которую ведёт ссылка */
const WHAT: { k: CheckDoc | 'ads'; refs: LawRef[]; main: string }[] = [
  { k: 'guarantee', refs: [{ article: '7', parts: ['1'] }, { article: '8', parts: ['6'] }], main: '8' },
  { k: 'mio', refs: [{ article: '7', parts: ['1'] }, { article: '18', parts: ['1', '8'] }], main: '18' },
  { k: 'bvu', refs: [{ article: '7', parts: ['1'] }, { article: '18', parts: ['1'] }], main: '7' },
  { k: 'ads', refs: [{ article: '23', parts: [] }], main: '23' },
]
const LIST: Key[] = ['chk.list1', 'chk.list2', 'chk.list3', 'chk.list4', 'chk.list5', 'chk.list6']
const latest = (xs: (string | null | undefined)[]) => xs.filter((x): x is string => !!x).sort().at(-1) ?? null

export default function Check() {
  const { complexes, developers, registryObject, sources, mode } = useStore()
  const i = useT()
  const { t, tn, lang } = i
  const reg = useRegistry()
  const [sp, setSp] = useSearchParams()
  const q = sp.get('q') ?? ''
  const k = sp.get('k')
  const setQ = (v: string) => setSp(v ? { q: v } : {}, { replace: true })
  const pick = (e: CheckEntry) => setSp({ q: q || e.name, k: e.key })
  const unpick = () => setSp(q ? { q } : {}, { replace: true })

  const objects = useMemo(() => (reg.data ? [...reg.data.objects, ...reg.data.withoutCoords] : []), [reg.data])
  const entries = useMemo(() => checkEntries(objects, complexes, developers), [objects, complexes, developers])
  const found = useMemo(() => searchEntries(q, entries), [q, entries])
  const sel = k ? entries.find((e) => e.key === k) : undefined
  const examples = entries.filter((e) => e.registry).slice(0, 4)

  // записи источников выбранного объекта реестра
  const [card, setCard] = useState<{ key: string; data?: RegistryCard; err?: string } | null>(null)
  useEffect(() => {
    if (!sel?.registry) return
    let alive = true
    registryObject(sel.key).then((data) => { if (alive) setCard({ key: sel.key, data }) }, (e: unknown) => { if (alive) setCard({ key: sel.key, err: errorText(e) }) })
    return () => { alive = false }
  }, [sel?.key, sel?.registry, mode]) // eslint-disable-line react-hooks/exhaustive-deps
  const mine = card && sel && card.key === sel.key ? card : null
  const facts = mine?.data ? checkFacts(mine.data.records) : null

  // дата сведений и дата загрузки набора у поставщика — для оговорки
  const [src, setSrc] = useState<SourceInfo[] | null>(null)
  useEffect(() => {
    if (mode === 'loading') return
    let alive = true
    sources().then((x) => { if (alive) setSrc(x) }, () => { /* без списка источников оговорка берёт даты объектов */ })
    return () => { alive = false }
  }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps
  const asOfAll = latest(objects.map((o) => o.collectedAt)) ?? latest((src ?? []).map((s) => s.collectedAt))
  const provider = latest((src ?? []).filter((s) => s.code === 'ashyq').map((s) => s.providerLoadedAt)) ?? facts?.providerLoadedAt ?? null
  const asOf = facts?.asOf ?? asOfAll
  const d = (iso: string | null | undefined) => (iso ? i.date(iso) : '—')

  return (
    <div className="lp lp-apple ck">
      <header className="lp-nav"><div className="lp-w">
        <Link to="/about" className="lp-logo"><img src="/logo.svg" alt="" /><span>SAQ Qurylys</span></Link>
        <span className="ck-navt">{t('chk.eyebrow')}</span>
        <LangSwitch />
        <Link to="/login" className="lp-pill">{t('chk.navStaff')}</Link>
      </div></header>

      <section className="ck-hero">
        <h1>{t('chk.h1a')}<br /><span>{t('chk.h1b')}</span></h1>
        <p className="ck-lead">{t('chk.lead')}</p>
        <div className="ck-search" role="search">
          <IcSearch size={22} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('chk.placeholder')} aria-label={t('chk.searchAria')} autoComplete="off" enterKeyHint="search"
            onKeyDown={(e) => { if (e.key === 'Enter' && found[0]) pick(found[0]) }} />
          {q && <button type="button" onClick={() => setQ('')} aria-label={t('common.clear')}><IcClose size={18} /></button>}
        </div>
        {!sel && q.trim() && (
          <div className="ck-found" role="listbox" aria-label={t('chk.searchAria')}>
            {found.map((e) => (
              <button key={e.key} type="button" role="option" aria-selected={false} onClick={() => pick(e)}>
                <b>{e.name}</b>
                <span>{[e.address, e.developer, e.bin && t('chk.bin', { bin: e.bin })].filter(Boolean).join(' · ')}</span>
                <em className={e.registry ? 'in' : ''}>{t(e.registry ? 'chk.inRegistry' : 'chk.notInRegistry')}</em>
              </button>
            ))}
            {reg.loading && !found.length && <div className="ck-empty">{t('chk.loading')}</div>}
            {!reg.loading && !found.length && <div className="ck-empty"><b>{t('chk.nothing', { q: q.trim() })}</b><span>{t('chk.nothingHint')}</span></div>}
          </div>
        )}
        {!q.trim() && (
          <div className="ck-ex">
            {examples.length > 0 && <span>{t('chk.examples')}</span>}
            {examples.map((e) => <button key={e.key} type="button" onClick={() => pick(e)}>{e.name.replace(/^(М?ЖК)\s*/u, '').replace(/"/g, '')}</button>)}
          </div>
        )}
        {reg.err && !reg.data && <p className="ck-err">{t('chk.loadErr', { e: reg.err })}</p>}
        {reg.data && !sel && <p className="ck-count">{tn('chk.count', objects.length)}</p>}
      </section>

      {sel && (
        <section className="ck-res"><div className="ck-w">
          <button type="button" className="ck-back" onClick={unpick}><IcChevronLeft size={16} />{t('chk.back')}</button>
          <article className="ck-card">
            <header>
              <h2>{sel.name}</h2>
              <span className={`ck-tag ${sel.registry ? 'in' : ''}`}>{t(sel.registry ? 'chk.inRegistry' : 'chk.notInRegistry')}</span>
            </header>
            <dl className="ck-dl">
              <div><dt>{t('chk.address')}</dt><dd>{sel.address || t('chk.noValue')}</dd></div>
              <div><dt>{t('chk.developer')}</dt><dd>{sel.developer || t('chk.noValue')}<small>{sel.bin ? t('chk.bin', { bin: sel.bin }) : t('chk.noBin')}</small></dd></div>
              {sel.agent && <div><dt>{t('chk.agent')}</dt><dd>{sel.agent}</dd></div>}
            </dl>

            {sel.registry ? (
              <>
                <h3 className="ck-h3">{t('chk.basisH')}{facts?.asOf && <small>{t('chk.basisSub', { d: d(facts.asOf) })}</small>}</h3>
                {mine?.err && <p className="ck-err">{t('chk.loadErr', { e: mine.err })}</p>}
                {!mine && <p className="ck-muted">{t('chk.loading')}</p>}
                {facts && (
                  <>
                    <div className="ck-docs">
                      {CHECK_DOCS.map((doc) => <DocTile key={doc} doc={doc} recs={facts.docs[doc]} />)}
                    </div>
                    {facts.kazreestr.length > 0 && (
                      <div className="ck-kaz">
                        <h4>{t('chk.kazH')}</h4>
                        <p>{t('chk.kazP', { n: facts.kazreestr.length })}{facts.providerLoadedAt && <> · {t('chk.kazLoaded', { d: d(facts.providerLoadedAt) })}</>}</p>
                        <ul>
                          {facts.kazreestr.slice(0, 4).map((r, n) => (
                            <li key={n}>
                              <b>{r.number ? t(r.number.includes('№') ? 'chk.docRaw' : 'chk.docNo', { n: r.number }) : t('chk.noValue')}</b>
                              {(r.start || r.end) && <span>{t('chk.kazTerm', { a: d(r.start), b: d(r.end) })}</span>}
                              <span>{t('chk.recordAt', { s: r.sourceName, d: d(r.collectedAt) })}</span>
                              {r.url && <a href={r.url} target="_blank" rel="noopener noreferrer">{t('chk.open')}<IcExternal size={13} /></a>}
                            </li>
                          ))}
                        </ul>
                        {facts.kazreestr.length > 4 && <p className="ck-muted">{t('chk.kazMore', { n: facts.kazreestr.length - 4 })}</p>}
                      </div>
                    )}
                    {facts.section && (
                      <div className="ck-sec">
                        <p>{t('chk.sectionP', { s: facts.section.section ?? '', d: d(facts.section.collectedAt) })}</p>
                        {facts.section.url && <a href={facts.section.url} target="_blank" rel="noopener noreferrer">{t('chk.open')}<IcExternal size={13} /></a>}
                      </div>
                    )}
                    {!anyBasis(facts) && !facts.section && <p className="ck-muted">{t('chk.noneHint')}</p>}
                  </>
                )}
              </>
            ) : (
              <div className="ck-absent">
                <h3>{t('chk.absentH')}</h3>
                <p>{t('chk.absentP')}</p>
                <div className="ck-docs">{CHECK_DOCS.map((doc) => <DocTile key={doc} doc={doc} recs={[]} />)}</div>
              </div>
            )}
            <p className="ck-disc-s">{t('chk.disc', { d: d(asOf) })}</p>
          </article>
        </div></section>
      )}

      <section className="ck-what"><div className="ck-w">
        <h2>{t('chk.whatH')}</h2>
        <div className="ck-grid">
          {WHAT.map(({ k: w, refs, main }) => {
            const a = lawArticle(main)
            const ex = refs.flatMap((r) => excerptsFor(r).map((e) => ({ num: r.article, ...e })))
            return (
              <div key={w} className={`ck-tile ${w}`}>
                <span className="ck-ref">{t(`chk.ref.${w}`)}</span>
                <h3>{w === 'ads' ? t('chk.what.adsH') : t(`chk.doc.${w}`)}</h3>
                <p>{t(`chk.what.${w}`)}</p>
                {a && <a href={adiletUrl(a.url, lang)} target="_blank" rel="noopener noreferrer">{t('chk.lawLink')}<IcExternal size={13} /></a>}
                {ex.length > 0 && (
                  <details>
                    <summary>{t('chk.excerpt')}</summary>
                    {ex.map((e, n) => <blockquote key={n} lang="ru"><b>ст. {e.num}{e.part ? `, ${e.part.startsWith('ч') ? e.part : `п. ${e.part}`}` : ''}.</b> {e.text}</blockquote>)}
                  </details>
                )}
              </div>
            )
          })}
        </div>
        <p className="ck-act">{t('chk.lawAct')} · <a href={adiletUrl(law.act.url, lang)} target="_blank" rel="noopener noreferrer">adilet.zan.kz</a></p>
      </div></section>

      <section className="ck-list"><div className="ck-w">
        <h2>{t('chk.listH')}</h2>
        <ol>{LIST.map((key) => <li key={key}><span className="ck-tick"><IcCheck size={16} strokeWidth={2.6} /></span>{t(key)}</li>)}</ol>
      </div></section>

      <section className="ck-disc"><div className="ck-w">
        <h2>{t('chk.discH')}</h2>
        <p className="big">{t('chk.disc', { d: d(asOf) })}</p>
        <p>{t('chk.srcs', { d: d(asOfAll), p: d(provider) })}</p>
      </div></section>

      <footer className="lp-foot"><div className="lp-w"><span>{t('chk.foot')}</span><span>{t('common.city')}</span></div></footer>
    </div>
  )
}

/** Плитка документа основания: есть ли запись реестра, номер и дата, источник, дата записи, ссылка */
function DocTile({ doc, recs }: { doc: CheckDoc; recs: FactRecord[] }) {
  const i = useT()
  const { t } = i
  const d = (iso: string | null | undefined) => (iso ? i.date(iso) : '—')
  const has = recs.length > 0
  return (
    <div className={`ck-doc ${has ? 'has' : 'none'}`}>
      <div className="ck-doc-h"><span className="ck-mark" aria-hidden>{has ? <IcCheck size={16} strokeWidth={2.8} /> : '—'}</span><b>{t(`chk.doc.${doc}`)}</b></div>
      <div className="ck-doc-s">{t(has ? 'chk.has' : 'chk.none')}</div>
      {has ? recs.slice(0, 2).map((r, n) => (
        <div key={n} className="ck-rec">
          <span>{[r.number && t(r.number.includes('№') ? 'chk.docRaw' : 'chk.docNo', { n: r.number }), r.date && t('chk.docDate', { d: d(r.date) })].filter(Boolean).join(' ')}</span>
          <small>{t('chk.recordAt', { s: r.sourceName, d: d(r.collectedAt) })}</small>
          {r.url && <a href={r.url} target="_blank" rel="noopener noreferrer">{t('chk.open')}<IcExternal size={13} /></a>}
        </div>
      )) : <small className="ck-muted">{t('chk.noneHint')}</small>}
    </div>
  )
}
