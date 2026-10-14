import { MenuButton, MenuItem } from '@carbon/react'
import { Close, Filter } from '@carbon/icons-react'

export interface FilterDef { key: string; label: string; options: { value: string; label: string }[] }
export interface Applied { key: string; values: string[] }

/** Фильтры по образцу Linear: «+ Фильтр» → свойство → значение; применённые — составные чипы «Свойство · является · Значение ×» */
export default function FilterBar({ defs, applied, onChange, right }: { defs: FilterDef[]; applied: Applied[]; onChange: (a: Applied[]) => void; right?: React.ReactNode }) {
  const toggle = (key: string, value: string) => {
    const cur = applied.find((a) => a.key === key)
    if (!cur) return onChange([...applied, { key, values: [value] }])
    const values = cur.values.includes(value) ? cur.values.filter((v) => v !== value) : [...cur.values, value]
    onChange(values.length ? applied.map((a) => (a.key === key ? { key, values } : a)) : applied.filter((a) => a.key !== key))
  }
  return (
    <div className="filters-row">
      <MenuButton label="Фильтр" kind="ghost" size="sm" menuAlignment="bottom-start">
        {defs.map((d) => (
          <MenuItem key={d.key} label={d.label}>
            {d.options.map((o) => (
              <MenuItem key={o.value} label={`${applied.find((a) => a.key === d.key)?.values.includes(o.value) ? '✓ ' : ''}${o.label}`} onClick={() => toggle(d.key, o.value)} />
            ))}
          </MenuItem>
        ))}
      </MenuButton>
      {applied.map((a) => {
        const d = defs.find((x) => x.key === a.key)!
        const labels = a.values.map((v) => d.options.find((o) => o.value === v)?.label ?? v)
        return (
          <span className="fchip" key={a.key}>
            <span className="k"><Filter size={12} style={{ marginRight: 6 }} />{d.label}</span>
            <span className="op">{a.values.length > 1 ? 'одно из' : 'является'}</span>
            <span>{labels.join(', ')}</span>
            <button aria-label="Убрать фильтр" onClick={() => onChange(applied.filter((x) => x.key !== a.key))}><Close size={14} /></button>
          </span>
        )
      })}
      {applied.length > 0 && <button className="cds--link" style={{ background: 'none', border: 0, cursor: 'pointer', fontSize: 14 }} onClick={() => onChange([])}>Сбросить</button>}
      <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>{right}</div>
    </div>
  )
}
