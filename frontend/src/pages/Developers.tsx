import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Cell, Faq, Hero, HeroTile, LinkCols, Modal, Score, SectionHead, Seg, ZoneTag, fmtDate } from '../components/ui'
import { DEMO_ORG, isDemo } from '../lib/provenance'
import { model } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcBuilding, IcCrane, IcMegaphone, IcSearch, IcUsers, IcWarning } from '../ui/icons'

const COLORS = ['#e4402d', '#0065d9', '#00b341', '#f59d00', '#1d1f26', '#7a5af8', '#00a1a1']
type F = 'all' | 'risk' | 'unfinished' | 'ads'

/** Застройщики — шапка с поиском по БИН, карточки-профили, FAQ «Как проверить застройщика» */
export default function Developers() {
  const { complexes, ads, developers, riskOf } = useStore()
  const [open, setOpen] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [f, setF] = useState<F>('all')
  const data = useMemo(() => developers.map((d, i) => {
    const objs = complexes.filter((c) => c.developerId === d.id).map((c) => ({ c, r: riskOf(c) }))
    const max = objs.reduce<(typeof objs)[number] | undefined>((m, x) => (!m || x.r.score > m.r.score ? x : m), undefined)
    return { d, objs, max, color: COLORS[i % COLORS.length], adsCount: ads.filter((a) => objs.some((o) => o.c.id === a.complexId)).length }
  }).sort((a, b) => (b.max?.r.score ?? 0) - (a.max?.r.score ?? 0)), [complexes, ads, developers, riskOf])
  type D = (typeof data)[number]
  const test: Record<F, (x: D) => boolean> = { all: () => true, risk: (x) => x.max?.r.zone === 'red', unfinished: (x) => x.d.unfinished > 0, ads: (x) => x.adsCount > 0 }
  const rows = data.filter((x) => test[f](x) && (!q || `${x.d.name} ${x.d.bin} ${x.d.director}`.toLowerCase().includes(q.toLowerCase())))
  const cur = data.find((x) => x.d.id === open)
  const initials = (n: string) => n.replace(/ТОО «|»/g, '').split(/[\s-]+/).slice(0, 2).map((w) => w[0]).join('').toUpperCase()

  return (
    <>
      <Hero crumb="Застройщики" title="Застройщики Усть-Каменогорска" info="Данные по БИН — для сверки с реестром юрлиц. В демо-наборе компании вымышлены"
        tiles={<>
          <HeroTile icon={<IcUsers size={32} />} label="Застройщиков" value={data.length} onClick={() => setF('all')} active={f === 'all'} />
          <HeroTile icon={<IcWarning size={32} />} label="С объектами в красной зоне" value={data.filter(test.risk).length} tone="red" onClick={() => setF('risk')} active={f === 'risk'} />
          <HeroTile icon={<IcCrane size={32} />} label="Есть незавершённые" value={data.filter(test.unfinished).length} tone="yellow" onClick={() => setF('unfinished')} active={f === 'unfinished'} />
          <HeroTile icon={<IcMegaphone size={32} />} label="С рекламой в ленте" value={data.filter(test.ads).length} onClick={() => setF('ads')} active={f === 'ads'} />
          <HeroTile icon={<IcBuilding size={32} />} label="Объектов на контроле" value={complexes.length} to="/complexes" />
          <HeroTile icon={<IcBuilding size={32} />} label="Сдано всего" value={data.reduce((s, x) => s + x.d.objects - x.d.unfinished, 0)} tone="green" onClick={() => setF('all')} />
        </>}>
        <div className="panel" style={{ gridTemplateColumns: '1fr' }}>
          <div className="pl row" style={{ gap: 12, paddingBottom: 16 }}>
            <label className="fsearch" style={{ height: 48 }}><IcSearch /><input placeholder="Название, БИН или руководитель" value={q} onChange={(e) => setQ(e.target.value)} /></label>
            <Seg value={f} onChange={setF} items={[['all', 'Все'], ['risk', 'С риском'], ['unfinished', 'Не сдают'], ['ads', 'С рекламой']]} />
            <button className="btn y" style={{ minWidth: 200 }} onClick={() => document.getElementById('devs')?.scrollIntoView({ behavior: 'smooth' })}>Показать {rows.length}</button>
          </div>
        </div>
      </Hero>

      <div id="devs" className="row between" style={{ marginBottom: 16 }}>
        <div className="sorter" style={{ padding: 0 }}><b>{rows.length} застройщиков:</b><span>по максимальному риску</span></div>
      </div>
      {rows.length === 0 && <div className="empty">Никого не нашли — попробуйте другой БИН или название</div>}
      <div className="gcards" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: 16 }}>
        {rows.map(({ d, objs, max, adsCount, color }) => (
          <button key={d.id} className="devcard" onClick={() => setOpen(d.id)}>
            <div className="row" style={{ gap: 14, alignItems: 'flex-start' }}>
              <span className="devlogo" style={{ background: color }}>{initials(d.name)}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="t-h3" style={{ fontSize: 18 }}>{d.name}</div>
                <div className="t-sm c2" style={{ marginTop: 2 }}>БИН {d.bin} · с {new Date(d.registered).getFullYear()} года{isDemo(d) && ` · ${DEMO_ORG}`}</div>
              </div>
              {max && <Score value={max.r.score} zone={max.r.zone} />}
            </div>
            <div className="t-sm"><b className="bold">{d.objects - d.unfinished} сдано</b><span className="c2"> · </span><b className="bold" style={{ color: d.unfinished ? 'var(--red)' : undefined }}>{d.unfinished} строится</b><span className="c2"> · {adsCount} публ. в ленте</span></div>
            <div className="objs">{objs.slice(0, 4).map(({ c }) => <img key={c.id} src={c.cover} alt={c.name} title={c.name} />)}</div>
            <div className="row wrap-f" style={{ gap: 6 }}>{objs.map(({ c, r }) => <span key={c.id} className={`tag ${r.zone}`}>{c.name}</span>)}</div>
          </button>
        ))}
      </div>

      <Faq title="Как проверить застройщика" sub="Что смотреть, прежде чем отправлять объект на проверку."
        items={[
          ['Где взять сведения о компании по БИН?', 'В открытом реестре юрлиц: дата регистрации, руководитель, учредители. Молодая компания с одним незавершённым объектом — повод присмотреться внимательнее, но не факт нарушения: это наблюдение вне балла O2.'],
          ['Как понять, может ли застройщик привлекать деньги дольщиков?', 'Нужна гарантия Казахстанской жилищной компании (ЕО) либо разрешение МИО вместе с проектным финансированием банка. Перечень таких ЖК ГАСК публикует в Instagram.'],
          ['Что делать, если у застройщика несколько объектов?', 'Откройте профиль: риск считается по каждому ЖК отдельно, в карточке видно, какой объект тянет балл вверх.'],
          ['Почему у застройщика с хорошей историей может быть красная зона?', 'Зона считается по объекту, а не по компании. Красная — когда по новому ЖК установлено отсутствие основания (R1) и есть прямое предложение внести деньги (R2), независимо от прошлых сданных домов.'],
        ]} />

      <LinkCols cols={[
        [['С объектами в красной зоне', '/complexes?zone=red', data.filter(test.risk).length], ['С незавершёнными объектами', '/developers', data.filter(test.unfinished).length]],
        [['Новостройки на контроле', '/complexes', complexes.length], ['Реклама застройщиков', '/ads', ads.length]],
        [['Очередь проверки', '/queue', ads.filter((a) => a.status === 'new' || a.status === 'needs_check').length], ['Методика скоринга', '/method', model.factors.length]],
      ]} />

      {cur && (
        <Modal title={cur.d.name} onClose={() => setOpen(null)}
          footer={<Link to={`/complexes?dev=${cur.d.id}`} className="btn y" onClick={() => setOpen(null)}>Все объекты застройщика</Link>}>
          <div className="row" style={{ gap: 14, padding: '4px 24px 12px' }}>
            <span className="devlogo" style={{ background: cur.color }}>{initials(cur.d.name)}</span>
            <div><div className="bold">{cur.d.objects - cur.d.unfinished} сдано · {cur.d.unfinished} строится</div>{isDemo(cur.d) && <div className="t-xs c2">{DEMO_ORG[0].toUpperCase() + DEMO_ORG.slice(1)}</div>}<div className="t-sm c2">{cur.max && <ZoneTag zone={cur.max.r.zone} />}</div></div>
          </div>
          {[['БИН', cur.d.bin], ['Руководитель', cur.d.director], ['Регистрация', fmtDate(cur.d.registered)], ['Сайт', cur.d.site ?? '—'], ['Instagram', cur.d.instagram ?? '—']].map(([k, v]) => (
            <Cell key={k} title={<span className="c2 t-sm">{k}</span>} end={<span style={{ color: 'var(--text)' }}>{v}</span>} />
          ))}
          <SectionHead title="Объекты на контроле" />
          {cur.objs.map(({ c, r }) => (
            <Cell key={c.id} inset to={`/complexes/${c.id}`} icon={<img className="th" src={c.cover} alt="" style={{ width: 48, height: 48, borderRadius: 12 }} />} title={`ЖК «${c.name}»`} sub={`${c.stage} · ${c.progress}%`} end={<Score value={r.score} zone={r.zone} />} chevron />
          ))}
        </Modal>
      )}
    </>
  )
}
