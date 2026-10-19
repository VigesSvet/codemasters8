// Переключатель языка «РУС | ҚАЗ»: в шапке рабочего места, на входе и на лендинге. Выбор — в localStorage (saq.lang),
// применяется сразу, без перезагрузки (lib/i18n.ts). Стили — styles/lang.css
import { LANGS, LANG_LABEL, LANG_TITLE, setLang, useT } from '../lib/i18n'

export default function LangSwitch() {
  const { lang, t } = useT()
  return (
    <div className="lang-sw" role="group" aria-label={t('common.lang')}>
      {LANGS.map((l) => (
        <button key={l} type="button" lang={l} className={l === lang ? 'on' : ''} aria-pressed={l === lang} title={LANG_TITLE[l]} onClick={() => setLang(l)}>
          {LANG_LABEL[l]}
        </button>
      ))}
    </div>
  )
}
