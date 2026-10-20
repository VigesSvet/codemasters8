// Тексты сервера для человека: в интерфейсе — никому, и администратору тоже, — не показываются имена переменных окружения,
// консольные команды, пути к файлам и адреса API. Сервер пишет их в пояснения подключений и ошибки для того, кто его
// обслуживает (журналы, консольная проверка); здесь они заменяются понятными словами: имя переменной — её названием
// (shared/env_labels.json), команда и путь — убираются («выполните вход на сервере»), «.env» — «настройки сервера».
import envJson from '../../../shared/env_labels.json'

export const ENV_LABELS: Readonly<Record<string, string>> = envJson.labels

/** Название настройки сервера по имени переменной; неизвестная — «настройка сервера» */
export const envLabel = (name: string) => ENV_LABELS[name] ?? 'настройка сервера'

// Команда сервера и её аргументы: ./dev.sh, python -m …, docker compose …, pip install …; аргументы — до конца фразы
// (точка с пробелом или в конце, точка с запятой, скобка): точка внутри имени файла («requirements.txt») фразу не кончает
const CMD = String.raw`(?:\.\/dev\.sh|(?:\.venv\/bin\/)?python3? -m [\w.]+|docker compose\b|pip install\b|npm (?:ci|install|run)\b)`
// В скобках — пояснение с командой: «(запустите ./dev.sh)», «(в Docker: docker compose run …)»
const CMD_PAREN = new RegExp(String.raw`\s*\([^()]*${CMD}[^()]*\)`, 'g')
const ARGS = String.raw`(?:[^;.)\n\x60]|\.(?=[^\s.]))*`
// После тире или двоеточия — «скачайте её: python -m app.speech download», «— python -m … --send-code, затем …»
const CMD_CLAUSE = new RegExp(String.raw`\s*[—:]\s*(?:([а-яё][а-яё ]{0,40}?):\s*)?${CMD}${ARGS}`, 'giu')
const CMD_ANY = new RegExp(String.raw`\s*\x60?${CMD}${ARGS}\x60?`, 'g')
// Имя переменной окружения: известное из словаря или похожее (префиксы настроек сервера)
const ENV_NAME = /\b(?:env:)?((?:DATABASE|MEDIA|SAQ|INTEGRATION|SERVICE|META|IG|TG|BOT|APIFY|DGIS|KRISHA|OLX|ANTHROPIC)_[A-Z0-9_]*[A-Z0-9])\b/g
// Скобки, в которых только имена переменных: «(DGIS_API_KEY)», «(TG_API_ID, TG_API_HASH)» — фраза и так говорит, чего нет
const ENV_PAREN = new RegExp(String.raw`\s*\((?:\s*${ENV_NAME.source}\s*[,/и]?\s*)+\)`, 'g')
// Адрес API сервера: «→ POST /api/intake/telegram», «GET /api/…»; «https://site.kz/api/…» — чужая ссылка, не трогается
const API = /\s*(?:→\s*)?(?:\b(?:GET|POST|PUT|PATCH|DELETE)\s+)?(?<![\w.:/-])\/api\/[^\s,;)»]*/g
// Путь к файлу или каталогу репозитория и сервера: shared/…, backend/…, /data/…
const PATH = /\s*\(?(?<![\w/.-])(?:(?:shared|backend|frontend|docs)\/|\/data\/)[^\s,;)»]*\)?/g

/** Текст сервера (пояснение, ошибка, заметка) — без имён переменных, команд, путей и адресов API */
export function plain(text: string): string
export function plain(text: string | null | undefined): string | null | undefined
export function plain(text: string | null | undefined): string | null | undefined {
  if (!text) return text
  let s = text
    .replace(CMD_PAREN, '')
    .replace(CMD_CLAUSE, (_m, verb?: string) => (verb ? ` — ${verb} на сервере` : ' на сервере'))
    .replace(CMD_ANY, '')
    .replace(/\s+в \.env на сервере/g, ' в настройках сервера')
    .replace(/\s+в \.env\b/g, ' в настройках сервера')
    .replace(/\.env\b/g, 'настройки сервера')
    .replace(ENV_PAREN, '')
    .replace(ENV_NAME, (_m, name: string) => envLabel(name))
    .replace(API, '')
    .replace(PATH, '')
  s = s
    .replace(/\(\s*\)/g, '')
    .replace(/(на сервере)(?:\s+на сервере)+/g, '$1')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\s+([,.;:)])/g, '$1')
    .replace(/[\s—:→-]+$/u, '')
    .trim()
  // фраза начиналась с имени переменной («APIFY_TOKEN содержит…») — первая буква снова заглавная
  if (s && /^\p{Lu}/u.test(text.trim()) && /^\p{Ll}/u.test(s)) s = s[0].toUpperCase() + s.slice(1)
  return s
}
