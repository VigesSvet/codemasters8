import { describe, expect, it } from 'vitest'
import { ENV_LABELS, envLabel, plain } from './plain'

// Строки — дословно из сервера (backend/app/…): то, что приходит в пояснениях подключений, ошибках и журнале
describe('тексты сервера без технических подробностей — и для администратора', () => {
  it('вход в Telegram: без команд консоли и Docker', () => {
    expect(plain('Нет авторизованной сессии Telegram: выполните вход — python -m app.integrations.telegram_login --send-code, '
      + 'затем --code <код> (в Docker: docker compose run --rm worker python -m app.integrations.telegram_login …)'))
      .toBe('Нет авторизованной сессии Telegram: выполните вход на сервере')
  })
  it('модель речи и пакет Anthropic: действие — «на сервере», без команды и имени переменной', () => {
    expect(plain('Модель faster-whisper «base» не найдена в SAQ_ASR_MODEL_DIR (FileNotFoundError) — скачайте её: python -m app.speech download'))
      .toBe('Модель faster-whisper «base» не найдена в каталог моделей распознавания речи (FileNotFoundError) — скачайте её на сервере')
    expect(plain('Пакет anthropic не установлен на сервере — pip install -r backend/requirements.txt')).toBe('Пакет anthropic не установлен на сервере')
  })
  it('имена переменных → названия; скобки из одних имён — убираются', () => {
    expect(plain('Нет на сервере: OLX_CLIENT_ID, OLX_CLIENT_SECRET')).toBe('Нет на сервере: ID приложения OLX, секрет приложения OLX')
    expect(plain('2ГИС не настроен на сервере (DGIS_API_KEY)')).toBe('2ГИС не настроен на сервере')
    expect(plain('Анализ ИИ не настроен: задайте ANTHROPIC_API_KEY на сервере')).toBe('Анализ ИИ не настроен: задайте ключ Anthropic на сервере')
    expect(plain('Администрирование подключений не настроено: задайте SAQ_ADMIN_TOKEN на сервере'))
      .toBe('Администрирование подключений не настроено: задайте токен администратора на сервере')
    expect(plain('env:META_USER_ACCESS_TOKEN')).toBe('токен Meta')
  })
  it('«.env» — «настройки сервера»; фраза с имени переменной — с заглавной', () => {
    expect(plain('APIFY_TOKEN содержит недопустимые символы — проверьте значение в .env'))
      .toBe('Токен Apify содержит недопустимые символы — проверьте значение в настройках сервера')
    expect(plain('Telegram не принял номер TG_PHONE — проверьте его в .env на сервере'))
      .toBe('Telegram не принял номер телефон аккаунта Telegram — проверьте его в настройках сервера')
    expect(plain('Неизвестная SAQ_NEW_FLAG')).toBe('Неизвестная настройка сервера')
  })
  it('адреса API и пути к файлам — убираются, ссылки на чужие сайты — нет', () => {
    expect(plain('отдельный проект saq-telegram-bot → POST /api/intake/telegram')).toBe('отдельный проект saq-telegram-bot')
    expect(plain('POST /api/intake/publications')).toBe('')
    expect(plain('Открыта копия стартового набора (shared/snapshot): реальные ЖК')).toBe('Открыта копия стартового набора: реальные ЖК')
    expect(plain('Файл не загружен: https://site.kz/api/photo/1.jpg (HTTP 404)')).toBe('Файл не загружен: https://site.kz/api/photo/1.jpg (HTTP 404)')
    expect(plain('Офлайн-копия — действие доступно с сервером (запустите ./dev.sh)')).toBe('Офлайн-копия — действие доступно с сервером')
  })
  it('обычный текст не меняется', () => {
    for (const s of ['Статус: «Новая» → «На проверке». Основание: звонок застройщику', 'Карточки закрыты проверкой доступа (HTTP 468, SafeLine)',
      'Ограничение частоты: повтор после 14:05', 'ЖК «Ертіс Парк», бронь от 100 000 ₸']) expect(plain(s)).toBe(s)
    expect(plain('')).toBe('')
    expect(plain(null)).toBeNull()
    expect(plain(undefined)).toBeUndefined()
  })
  it('словарь: у каждого названия нет имени переменной; неизвестная — «настройка сервера»', () => {
    expect(Object.keys(ENV_LABELS).length).toBeGreaterThan(60)
    for (const [k, v] of Object.entries(ENV_LABELS)) expect(v.includes(k) || v.includes('_')).toBe(false)
    expect(envLabel('BOT_TOKEN')).toBe('токен бота заявок')
    expect(envLabel('NOPE')).toBe('настройка сервера')
  })
})
