// ДЕМО-ДАННЫЕ. Все названия ЖК, застройщиков, БИН, аккаунтов и текстов объявлений вымышлены.
// Совпадения с реальными организациями случайны. Используются только для демонстрации интерфейса.
import type { Ad, AuditEvent, Complex, Developer, SitePhoto } from './types'

export const developers: Developer[] = [
  { id: 'd1', name: 'ТОО «Ертіс Строй Инвест»', bin: '180840012345', director: 'Ахметов Е. С.', registered: '2018-08-14', objects: 6, unfinished: 0, site: 'ertis-invest.kz', instagram: '@ertis.invest' },
  { id: 'd2', name: 'ТОО «Altai Build Group»', bin: '200540098761', director: 'Ким В. А.', registered: '2020-05-21', objects: 3, unfinished: 1, instagram: '@altai.build' },
  { id: 'd3', name: 'ТОО «Ульба Девелопмент»', bin: '151140055512', director: 'Серикбаева А. М.', registered: '2015-11-02', objects: 9, unfinished: 0, site: 'ulba-dev.kz', instagram: '@ulba.dev' },
  { id: 'd4', name: 'ТОО «Шығыс Құрылыс 2030»', bin: '230240077003', director: 'Нуртазин Б. К.', registered: '2023-02-09', objects: 1, unfinished: 1, instagram: '@shygys.kurylys' },
  { id: 'd5', name: 'ТОО «Квартал-Восток»', bin: '120340011198', director: 'Петров И. Л.', registered: '2012-03-30', objects: 11, unfinished: 0, site: 'kvartal-vostok.kz', instagram: '@kvartal.vostok' },
  { id: 'd6', name: 'ТОО «Nord Capital Construction»', bin: '210940066420', director: 'Жумабеков Д. Р.', registered: '2021-09-17', objects: 2, unfinished: 2, instagram: '@nord.capital.uka' },
  { id: 'd7', name: 'ТОО «Береке Девелопмент»', bin: '190640033871', director: 'Тлеубаева Г. Н.', registered: '2019-06-11', objects: 4, unfinished: 0, site: 'bereke-dev.kz', instagram: '@bereke.dev' },
]

const seriesA: SitePhoto[] = [
  { date: '2025-05-12', src: '/img/site-a-2025-05.jpg', source: 'дрон', activity: 'есть', note: 'Каркас 1–9 эт., 2 крана в работе', fence: 'yes', bins: 'yes', wheelWash: 'yes' },
  { date: '2026-02-10', src: '/img/site-a-2026-02.jpg', source: 'дрон', activity: 'есть', note: 'Каркас до 24 эт.', fence: 'yes', bins: 'yes', wheelWash: 'unknown' },
  { date: '2026-08-15', src: '/img/site-a-2026-08.jpg', source: 'камера', activity: 'есть', note: 'Фасадные работы', fence: 'yes', bins: 'yes', wheelWash: 'yes' },
]

const idleSeries: SitePhoto[] = [
  { date: '2026-05-04', src: '/img/site-c.jpg', source: 'дрон', activity: 'есть', note: 'Монолит 4 эт.', fence: 'yes', bins: 'unknown', wheelWash: 'no' },
  { date: '2026-06-06', src: '/img/site-c.jpg', source: 'дрон', activity: 'низкая', note: '1 кран, техника не движется', fence: 'yes', bins: 'no', wheelWash: 'no' },
  { date: '2026-07-08', src: '/img/site-c.jpg', source: 'дрон', activity: 'нет', note: 'Изменений нет', fence: 'no', bins: 'no', wheelWash: 'no' },
  { date: '2026-09-09', src: '/img/site-c.jpg', source: 'дрон', activity: 'нет', note: 'Изменений нет 95 дней', fence: 'no', bins: 'no', wheelWash: 'no' },
]

const ph = (src: string, date = '2026-09-20', activity: SitePhoto['activity'] = 'есть'): SitePhoto[] => [
  { date, src, source: 'дрон', activity, fence: 'yes', bins: 'yes', wheelWash: 'unknown' },
]

export const complexes: Complex[] = [
  { id: 'c1', name: 'Ертіс Парк', developerId: 'd1', address: 'пр. Абая, 172', district: 'Ульбинский', lng: 82.6372, lat: 49.9866, stage: 'каркас', progress: 58, floors: 16, flats: 312, start: '2025-03-01', plannedEnd: '2027-06-30',
    basis: { guarantee: 'yes', guaranteeRef: 'Гарантия ЕО № 24-0193 от 12.02.2025', mio: 'yes', mioRef: 'Разрешение МИО № 112 от 20.02.2025', bvu: 'yes', bank: 'АО «Банк Восток» (демо)' },
    inPublishedList: 'yes', cover: '/img/site-a-2026-08.jpg', photos: seriesA, idleDays: 0 },
  { id: 'c2', name: 'Алтай Резиденс', developerId: 'd2', address: 'ул. Казахстан, 66', district: 'Центр', lng: 82.6308, lat: 49.9513, stage: 'фундамент', progress: 14, floors: 12, flats: 180, start: '2026-04-15', plannedEnd: '2028-03-31',
    basis: { guarantee: 'no', mio: 'no', bvu: 'no' },
    inPublishedList: 'no', cover: '/img/site-i.jpg', photos: ph('/img/site-i.jpg', '2026-09-28'), idleDays: 0 },
  { id: 'c3', name: 'Ульба Ривер', developerId: 'd3', address: 'ул. Протозанова, 81', district: 'Центр', lng: 82.6045, lat: 49.9505, stage: 'отделка', progress: 88, floors: 9, flats: 144, start: '2024-06-01', plannedEnd: '2026-12-25',
    basis: { guarantee: 'yes', guaranteeRef: 'Гарантия ЕО № 24-0071', mio: 'unknown', bvu: 'yes', bank: 'АО «Шығыс Банк» (демо)' },
    inPublishedList: 'yes', cover: '/img/facade-c.jpg', photos: ph('/img/site-g.jpg'), idleDays: 0 },
  { id: 'c4', name: 'Шығыс Тауэр', developerId: 'd4', address: 'пр. Сатпаева, 14/2', district: 'Левобережье', lng: 82.6213, lat: 49.8948, stage: 'каркас', progress: 31, floors: 18, flats: 256, start: '2025-09-01', plannedEnd: '2027-12-31',
    basis: { guarantee: 'no', mio: 'no', bvu: 'unknown' },
    inPublishedList: 'no', cover: '/img/site-c.jpg', photos: idleSeries, idleDays: 97 },
  { id: 'c5', name: 'Квартал 21', developerId: 'd5', address: 'ул. Бажова, 333', district: 'Согра', lng: 82.5347, lat: 50.0136, stage: 'введён', progress: 100, floors: 10, flats: 220, start: '2023-04-01', plannedEnd: '2025-10-30',
    basis: { guarantee: 'yes', guaranteeRef: 'Гарантия ЕО № 22-0410', mio: 'yes', bvu: 'yes', bank: 'АО «Банк Восток» (демо)' },
    inPublishedList: 'yes', cover: '/img/facade-b.jpg', photos: ph('/img/facade-b.jpg', '2026-08-01', 'нет'), idleDays: 0 },
  { id: 'c6', name: 'Nord Hills', developerId: 'd6', address: 'ул. Жибек Жолы, 5', district: 'Левобережье', lng: 82.6143, lat: 49.9282, stage: 'котлован', progress: 6, floors: 14, flats: 198, start: '2026-02-01', plannedEnd: '2028-06-30',
    basis: { guarantee: 'unknown', mio: 'no', bvu: 'unknown' },
    inPublishedList: 'unknown', cover: '/img/site-f.jpg', photos: ph('/img/site-f.jpg', '2026-06-01', 'низкая'), idleDays: 104 },
  { id: 'c7', name: 'Береке Сити', developerId: 'd7', address: 'ул. Кабанбай батыра, 120', district: 'Центр', lng: 82.6345, lat: 49.9545, stage: 'кровля', progress: 74, floors: 12, flats: 168, start: '2024-11-01', plannedEnd: '2026-11-30',
    basis: { guarantee: 'yes', guaranteeRef: 'Гарантия ЕО № 24-0288', mio: 'yes', bvu: 'yes', bank: 'АО «Шығыс Банк» (демо)' },
    inPublishedList: 'yes', cover: '/img/site-d.jpg', photos: ph('/img/site-d.jpg'), idleDays: 0 },
  { id: 'c8', name: 'Тихий берег', developerId: 'd2', address: 'пр. Сатпаева, 52', district: 'Левобережье', lng: 82.615, lat: 49.899, stage: 'каркас', progress: 42, floors: 9, flats: 96, start: '2025-07-01', plannedEnd: '2027-04-30',
    basis: { guarantee: 'no', mio: 'yes', mioRef: 'Разрешение МИО № 87', bvu: 'no' },
    inPublishedList: 'no', cover: '/img/site-h.jpg', photos: ph('/img/site-h.jpg', '2026-09-02', 'низкая'), idleDays: 41 },
  { id: 'c9', name: 'Самал Сити', developerId: 'd5', address: 'пр. Независимости, 31', district: 'Центр', lng: 82.6146, lat: 49.9562, stage: 'отделка', progress: 92, floors: 12, flats: 240, start: '2024-03-01', plannedEnd: '2026-10-31',
    basis: { guarantee: 'yes', mio: 'yes', bvu: 'yes', bank: 'АО «Банк Восток» (демо)' },
    inPublishedList: 'yes', cover: '/img/facade-a.jpg', photos: ph('/img/facade-a.jpg'), idleDays: 0 },
  { id: 'c10', name: 'Белуха Парк', developerId: 'd3', address: 'ул. Гоголя, 210', district: 'Северо-запад', lng: 82.5888, lat: 49.9739, stage: 'каркас', progress: 47, floors: 14, flats: 210, start: '2025-05-15', plannedEnd: '2027-08-31',
    basis: { guarantee: 'yes', guaranteeRef: 'Гарантия ЕО № 25-0034', mio: 'no', bvu: 'yes', bank: 'АО «Шығыс Банк» (демо)' },
    inPublishedList: 'yes', cover: '/img/site-b.jpg', photos: ph('/img/site-b.jpg'), idleDays: 0 },
  { id: 'c11', name: 'Достык Плаза', developerId: 'd6', address: 'ул. Утепова, 18', district: 'Левобережье', lng: 82.6138, lat: 49.9018, stage: 'фундамент', progress: 11, floors: 16, flats: 288, start: '2026-01-10', plannedEnd: '2028-09-30',
    basis: { guarantee: 'no', mio: 'no', bvu: 'no' },
    inPublishedList: 'no', cover: '/img/site-g.jpg', photos: ph('/img/site-g.jpg', '2026-07-01', 'низкая'), idleDays: 73 },
  { id: 'c12', name: 'Green Line', developerId: 'd7', address: 'ул. Кирова, 59', district: 'Центр', lng: 82.6226, lat: 49.9509, stage: 'каркас', progress: 36, floors: 9, flats: 126, start: '2025-10-01', plannedEnd: '2027-09-30',
    basis: { guarantee: 'unknown', mio: 'unknown', bvu: 'yes', bank: 'АО «Банк Восток» (демо)' },
    inPublishedList: 'unknown', cover: '/img/site-d.jpg', photos: ph('/img/site-d.jpg'), idleDays: 0 },
  { id: 'c13', name: 'Нұр Әлем', developerId: 'd1', address: 'ул. Шакарима, 88', district: 'Центр', lng: 82.608, lat: 49.9689, stage: 'введён', progress: 100, floors: 12, flats: 190, start: '2022-05-01', plannedEnd: '2024-12-20',
    basis: { guarantee: 'yes', mio: 'yes', bvu: 'yes', bank: 'АО «Банк Восток» (демо)' },
    inPublishedList: 'yes', cover: '/img/facade-c.jpg', photos: ph('/img/facade-c.jpg', '2026-05-01', 'нет'), idleDays: 0 },
  { id: 'c14', name: 'Солнечный квартал', developerId: 'd4', address: 'пр. Абая, 9', district: 'Ульбинский', lng: 82.642, lat: 49.983, stage: 'котлован', progress: 3, floors: 10, flats: 140, start: '2026-08-01', plannedEnd: '2028-08-31',
    basis: { guarantee: 'unknown', mio: 'unknown', bvu: 'unknown' },
    inPublishedList: 'unknown', cover: '/img/site-i.jpg', photos: [], idleDays: 0 },
]

export const ads: Ad[] = [
  { id: 'a1', platform: 'instagram', account: '@altai.rezidens.sale', url: 'https://instagram.com/p/demo1', date: '2026-10-13T18:22', source: 'parser', status: 'new', reach: 18400,
    text: 'Старт продаж! ЖК «Алтай Резиденс» в самом центре. Квартиры от 290 000 ₸/м². Бронь от 500 000 ₸, рассрочка от застройщика 0% на 24 мес. Сдача 2028. Звоните!',
    ocr: 'АЛТАЙ РЕЗИДЕНС · СТАРТ ПРОДАЖ · РАССРОЧКА 0% · СДАЧА IV кв. 2028', image: '/img/site-i.jpg', complexId: 'c2', matchConfidence: 0.97, price: 'от 290 000 ₸/м²' },
  { id: 'a2', platform: 'tiktok', account: '@shygys_tower', url: 'https://tiktok.com/@demo/video/2', date: '2026-10-13T12:05', source: 'parser', status: 'needs_check', reach: 52100,
    text: 'Шығыс Тауэр — 18 этажей у набережной 🔥 Продаётся 1-2-3 комн. на этапе каркаса. Первый взнос 20%, договор долевого участия. Успей по старой цене!',
    ocr: 'ДОГОВОР ДОЛЕВОГО УЧАСТИЯ · ПЕРВЫЙ ВЗНОС 20%', image: '/img/site-c.jpg', complexId: 'c4', matchConfidence: 0.92, price: 'от 310 000 ₸/м²' },
  { id: 'a3', platform: 'instagram', account: '@ertis.invest', url: 'https://instagram.com/p/demo3', date: '2026-10-12T10:40', source: 'parser', status: 'dismissed', reach: 9200,
    text: 'ЖК «Ертіс Парк». Объект участвует в программе гарантирования ЕО. Номер гарантии в профиле. Квартиры с отделкой, сдача 2 кв. 2027.',
    image: '/img/site-a-2026-08.jpg', complexId: 'c1', matchConfidence: 0.99, price: 'от 360 000 ₸/м²' },
  { id: 'a4', platform: 'krisha', account: 'krisha.kz / собственник', url: 'https://krisha.kz/a/show/demo4', date: '2026-10-12T09:15', source: 'manual', status: 'in_review', assignee: 'Нестерова А.',
    text: 'Продаётся 2-комн. квартира в строящемся ЖК на Левобережье, 64 м², черновая. Переуступка по ДДУ, сдача 2027. Торг.',
    complexId: 'c8', matchConfidence: 0.71, price: '19 800 000 ₸' },
  { id: 'a5', platform: 'telegram', account: 't.me/nord_hills_uka', url: 'https://t.me/demo/5', date: '2026-10-11T21:30', source: 'parser', status: 'new', reach: 3100,
    text: 'Nord Hills — бизнес-класс в Аблакетке. Предварительное бронирование квартир, фиксируем цену котлована. Количество ограничено.',
    image: '/img/site-f.jpg', complexId: 'c6', matchConfidence: 0.88, price: 'цена котлована' },
  { id: 'a6', platform: 'instagram', account: '@dostyk.plaza.uka', url: 'https://instagram.com/p/demo6', date: '2026-10-11T16:02', source: 'parser', status: 'confirmed', assignee: 'Нестерова А.', reach: 24700,
    text: 'Достык Плаза: продаём квартиры от фундамента! Цена 270 000 ₸ за квадрат, взнос от 30%. Ипотека не нужна — платите напрямую застройщику.',
    ocr: 'ПРОДАЖА ОТ ФУНДАМЕНТА · 270 000 ₸/м²', image: '/img/site-g.jpg', complexId: 'c11', matchConfidence: 0.95, price: '270 000 ₸/м²' },
  { id: 'a7', platform: 'olx', account: 'olx.kz / Агентство «Дом»', url: 'https://olx.kz/d/demo7', date: '2026-10-10T13:44', source: 'manual', status: 'new',
    text: '3-комн. квартира в сданном доме ЖК «Квартал 21», 86 м², ключи на руках, документы готовы.',
    complexId: 'c5', matchConfidence: 0.9, price: '34 500 000 ₸' },
  { id: 'a8', platform: 'facebook', account: 'Тихий берег УКА', url: 'https://facebook.com/demo8', date: '2026-10-10T08:12', source: 'parser', status: 'needs_check', reach: 6100,
    text: 'Квартиры у реки. Строительство идёт, дом будет сдан в 2027. Продаётся по договору о долевом участии, цены от застройщика.',
    image: '/img/site-h.jpg', complexId: 'c8', matchConfidence: 0.83, price: 'от 285 000 ₸/м²' },
  { id: 'a9', platform: 'instagram', account: '@novostroyki_oskemen', url: 'https://instagram.com/p/demo9', date: '2026-10-09T19:55', source: 'parser', status: 'new', reach: 41200,
    text: 'Подборка новостроек Өскемен 🏙 Green Line, Белуха Парк, Ульба Ривер — продажа квартир, рассрочка, бронь. Пишите в директ.',
    complexId: 'c12', matchConfidence: 0.54 },
  { id: 'a10', platform: '2gis', account: '2ГИС · отдел продаж', url: 'https://2gis.kz/demo10', date: '2026-10-09T11:00', source: 'demo', status: 'new',
    text: 'Отдел продаж ЖК «Солнечный квартал». Приём заявок на бронирование. Продаётся. Цена по запросу.',
    complexId: 'c14', matchConfidence: 0.86 },
  { id: 'a11', platform: 'tiktok', account: '@belukha.park', url: 'https://tiktok.com/@demo/video/11', date: '2026-10-08T17:20', source: 'parser', status: 'dismissed', reach: 12800,
    text: 'Белуха Парк — строим по гарантии ЕО. Каркас 9 этаж. Продажа через договор с гарантией, номер договора гарантии в шапке профиля.',
    image: '/img/site-b.jpg', complexId: 'c10', matchConfidence: 0.96 },
  { id: 'a12', platform: 'instagram', account: '@kvartiry.uka.invest', url: 'https://instagram.com/p/demo12', date: '2026-10-08T09:41', source: 'parser', status: 'new', reach: 7400,
    text: 'Инвестиция в квадратные метры! Новый проект на КШТ, старт строительства. Вход от 5 млн ₸, доходность до 30% к сдаче. Количество мест ограничено.',
    complexId: undefined, matchConfidence: 0.31, price: 'от 5 000 000 ₸' },
]

export const keywordsDefault = [
  'продаём', 'продается', 'продаётся', 'квартира', 'строительство', 'старт продаж', 'бронь', 'бронирование', 'рассрочка',
  'первый взнос', 'долевое', 'ДДУ', 'договор долевого участия', 'от фундамента', 'цена котлована', 'переуступка', '₸/м²', 'сдача',
]

export const keywordsSafe = ['гарантия ЕО', 'гарантии ЕО', 'номер гарантии', 'ключи на руках', 'сданном доме', 'введён в эксплуатацию']

export const audit: Record<string, AuditEvent[]> = {
  a6: [
    { at: '2026-10-11 16:05', who: 'Система', what: 'Публикация собрана парсером Instagram, OCR выполнен' },
    { at: '2026-10-11 16:05', who: 'Система', what: 'Связано с ЖК «Достык Плаза» (уверенность 95%)' },
    { at: '2026-10-11 16:06', who: 'Система', what: 'Скоринг 85 → красная зона (R1+R2 подтверждены)' },
    { at: '2026-10-12 09:30', who: 'Нестерова А.', what: 'Взято в работу' },
    { at: '2026-10-12 11:10', who: 'Нестерова А.', what: 'Сверено с картотекой ГАСК: основания нет. Нарушение подтверждено' },
  ],
}

export const publishedListMeta = { source: 'Instagram ГАСК ВКО (перечень)', updated: '2026-10-01', count: 7 }
