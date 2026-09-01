import os
import json
import logging
import asyncio
import threading
from pathlib import Path
from dotenv import load_dotenv
from handlers.calendar_conflicts import handle_conflict_choice

# ---------- Загрузка .env ----------
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

print("TG_TOKEN:", os.environ.get("TG_TOKEN"))
print("BASE_URL:", os.environ.get("BASE_URL"))

# ---------- Дата/время ----------
from datetime import datetime, timedelta
import pytz
import re
import dateparser

# ---------- Flask ----------
from flask import Flask, request, redirect

# ---------- Telegram ----------
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
)

# ---------- Google Calendar ----------
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ---------- Финансы ----------
from handlers.finance import (
    parse_finance,
    add_transaction,
    get_balance,
    get_summary_by_category,
)

# ---------- Summary/Parser ----------
from handlers.summary import build_summary

# ---------- Календарь ----------


# ---------- Конфликты ----------
from handlers.calendar_conflicts import handle_conflict_choice

# ---------- Утилиты ----------
telegram_app.add_handler(
    CallbackQueryHandler(handle_conflict_choice, pattern="^conflict_")
)
from utils.free_slots import find_next_free_slot
from handlers.calendar_service import get_calendar_service
from handlers.calendar_conflicts import handle_conflict_choice



# ================= CONFIG =================
TG_TOKEN = os.environ["TG_TOKEN"]
BASE_URL = os.getenv("BASE_URL", "https://bot-project-bdub.onrender.com")
SCOPES = ["https://www.googleapis.com/auth/calendar"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ================= CATEGORIES =================
CATEGORY_COLORS = {
    "работа": "5",
    "деньги": "11",
    "семья": "6",
    "здоровье": "2",
    "развитие": "9",
    "отдых": "10",
    "друзья": "3",
    "хобби": "7",
    "трансфер": "4",
    "прочее": "8",
}

CATEGORY_KEYWORDS = {
    "работа": [
        "встреча","звонок","проект","клиент","клиентка","работа","дело","собрание","совещание","презентация",
        "отчет","задача","deadline","срок","команда","менеджер","начальник","брифинг","координация",
        "план","бриф","контракт","договора","поставщик","партнер","подготовка","анализ","стратегия",
        "подписание","отправка","отчетность","планирование","рабочий звонок","рабочее совещание",
        "тимлид","бриф-сессия","координация проекта","обновление","проверка","согласование",
        "тестирование","бриф-лист","оперативка","статистика","рабочее задание","проектный","таск",
        "брифинг-план","клиентский","производство","согласование договора","отчет по проекту",
        "бизнес","рабочая встреча","документы","письмо","электронная почта","деловая переписка",
        "брифинг-сессия","доклад","контроль","управление","рабочий процесс","план задач","тимлидинг",
        "проверка задач","выполнение","отчетность по проекту","статус","согласование проекта",
        "брифинг-отчет","совещание с командой","рабочее совещание онлайн","планирование задач",
        "проектная документация","управление проектом","разработка","координация команды","брифинг звонок",
        "деловая встреча","обсуждение","согласование документов","ведение проекта","тим-координация",
        "делопроизводство","документирование","совещание по проекту","рабочий звонок клиенту",
        "встреча команды","брифинг по задачам","обновление статуса","планирование проекта",
        "отчет по задачам","контроль выполнения","управление командой","отчетность по задачам",
        "проверка прогресса","совещание с клиентом","координация действий","брифинг планирования",
        "встреча с партнером","подготовка документов","анализ результатов","проверка отчета",
        "согласование плана","контроль сроков","выполнение задач","обновление проекта",
        "проверка документа","согласование задач","брифинг по проекту","встреча с менеджером",
        "контроль задач","отчет о проделанной работе","совещание отдела","тим-брифинг","деловое общение",
        "рабочие звонки","обсуждение проекта","планирование работы","обновление задач","отчетность команды",
        "брифинг встреч","координация действий команды","обсуждение с руководителем","контроль выполнения задач",
        "совещание по задачам","планирование звонков","брифинг с клиентом","проверка сроков","ведение отчета",
        "деловая переписка с клиентом","координация проекта","обновление статуса задач","брифинг по отчету",
        "контроль исполнения","проверка выполнения проекта","согласование документов проекта",
        "управление задачами","планирование встреч","брифинг по задачам команды","встреча с руководителем",
        "отчет о задачах","совещание по проекту","координация действий проекта","брифинг-план проекта",
        "контроль выполнения задач команды","обновление информации","проверка документации","согласование сроков",
        "ведение документации","деловое совещание","брифинг онлайн","обсуждение задач","планирование отчета",
        "брифинг с командой","контроль проекта","проверка прогресса задач","обновление отчетности","координация звонков",
        "брифинг с руководителем","встреча по проекту","проверка выполнения задач","согласование действий",
        "отчет по проекту","управление проектной командой","брифинг по статусу","контроль выполнения плана",
        "проверка задач команды","совещание по проектной документации","брифинг обновления","встреча для согласования",
        "проверка результатов","контроль прогресса","брифинг по новым задачам","координация работы команды",
        "совещание по выполнению","брифинг по задачам проекта","обсуждение отчета","проверка плана","контроль выполнения проекта",
        "брифинг действий команды","встреча с сотрудниками","обновление плана","проверка статуса","согласование действий команды",
        "ведение отчета по проекту","брифинг для команды","координация выполнения задач","совещание по проектной работе",
        "контроль статуса","брифинг по выполнению","проверка выполнения","обновление документа","брифинг по плану",
        "встреча для обсуждения","контроль задач проекта","брифинг по отчетности","совещание с менеджером","проверка выполнения задач проекта",
        "управление задачами команды","брифинг по проектной документации","обновление статуса проекта","контроль прогресса",
        "брифинг по планированию","совещание с клиентом по проекту","проверка отчета по проекту","координация задач","брифинг онлайн встречи",
        "планирование звонков с клиентом","контроль выполнения проекта команды","брифинг по задачам отдела","встреча с руководителем проекта",
        "брифинг по проектным задачам","проверка выполнения задач отдела","обновление информации по проекту","контроль выполнения задач проекта"
    ],

    "деньги": [
        "оплата","деньги","счет","доход","расход","зарплата","налоги","налог","бюджет","прибыль",
        "убыток","финансы","платеж","инвестиции","выручка","расходы","прибыльность","баланс",
        "транзакция","оплата счета","кредит","депозит","сбережения","платежи","финансовые цели",
        "бухгалтерия","касса","счет-фактура","оплата услуг","расчет","поступления","инкассация",
        "фонд","платежный","финансовая отчетность","наличные","безналичный","оплата аренды","счета",
        "финансовый поток","поступление денег","оплата поставщикам","деньги на счет","оплата товара",
        "финансовые операции","баланс счета","доходы и расходы","финансовый контроль","уплата налогов",
        "кредитование","платежи поставщикам","финансовый отчет","финансовая проверка","денежные средства",
        "инвестиционный план","финансовая транзакция","уплата счета","дебет","кредитная операция",
        "расходование средств","денежные переводы","финансовый учет","финансовая дисциплина",
        "оплата коммунальных","расходные операции","счет на оплату","финансовая цель","управление деньгами",
        "поступление дохода","уплата долгов","денежный поток","финансовая аналитика","контроль платежей",
        "финансовая стратегия","расчет прибыли","учет расходов","финансовый менеджмент","финансовое планирование",
        "денежный остаток","баланс доходов","платежи онлайн","уплата кредита","инкассация средств","финансовый мониторинг",
        "оплата счетов","уплата поставщикам","финансовый контроль расходов","денежные операции","уплата налогов компании",
        "бухгалтерский учет","контроль финансов","финансовая отчетность компании","финансовый анализ","уплата зарплаты",
        "поступление платежа","баланс компании","финансовая отчетность отдела","финансовая проверка счета",
        "расходная статья","учет финансов","финансовая отчетность персонала","финансовая проверка расходов","управление счетом",
        "денежные средства компании","поступление денег на счет","финансовый поток компании","уплата поставщикам услуг",
        "баланс доходов и расходов","финансовый учет компании","расходы компании","денежные переводы компании",
        "финансовые показатели","уплата налогов предприятия","учет платежей","финансовая отчетность бизнеса",
        "баланс по счету","контроль платежей компании","уплата кредитов","финансовый менеджмент компании",
        "финансовая аналитика бизнеса","контроль финансов компании","управление финансами","уплата счетов компании",
        "денежные операции бизнеса","расходование средств компании","оплата поставок","баланс бюджета",
        "финансовая отчетность организации","управление денежными потоками","оплата услуг компании",
        "финансовая проверка организации","денежные средства бизнеса","финансовые транзакции компании","учет расходов компании",
        "баланс финансов","финансовый контроль организации","уплата налогов организации","финансовая отчетность предприятия",
        "денежные переводы бизнеса","финансовые операции организации","управление счетами","баланс денежных средств",
        "оплата услуг бизнеса","финансовая аналитика организации","уплата счетов организации","финансовый мониторинг бизнеса",
        "денежные средства организации","баланс по операциям","финансовые показатели компании","управление расходами",
        "учет доходов","контроль финансовых потоков","уплата кредитов компании","баланс по счетам","управление финансовыми потоками",
        "финансовая отчетность бизнеса","контроль расходов","финансовое планирование компании","управление бюджетом",
        "уплата налогов бизнес","баланс доходов компании","финансовая дисциплина компании","денежный поток компании",
        "финансовый аудит","финансовые показатели организации","расчет бюджета","управление доходами","контроль поступлений",
        "оплата поставок компании","финансовая отчетность отдела компании","управление счетом компании","контроль расходов компании"
    ],

    "семья": [
        "дети","семья","жена","сын","дочь","мама","папа","родители","сестра","брат","бабушка","дедушка",
        "родня","отдых с семьей","встреча с родными","семейный ужин","праздник","день рождения","свадьба",
        "детский сад","школа","родительское собрание","каникулы","отпуск с семьей","забота о детях",
        "воспитание","покупки детям","развлечения","игры с детьми","семейное мероприятие","сюрприз",
        "подарки","родительский контроль","семейная прогулка","встреча родственников","совместные выходные",
        "поход с детьми","развивающие игры","семейная поездка","вечер с семьей","детский праздник","семейные встречи",
        "отдых с детьми","развлечение детей","семейная активность","родительская встреча","совместное время",
        "встреча с бабушкой","встреча с дедушкой","семейный обед","родительские обязанности","разговор с детьми",
        "забота о родителях","семейный досуг","игры в семье","выезд на природу с семьей","совместный отдых",
        "подарки детям","детские кружки","школьные мероприятия","родительское собрание школы","семейный праздник",
        "встреча с семьей","поездка с детьми","семейные праздники","совместные занятия","праздничный ужин",
        "детские занятия","занятия с детьми","воспитательные мероприятия","родительские встречи","семейный поход",
        "детская игра","вместе с детьми","поход на природу","семейный вечер","родительский контроль за детьми",
        "совместные мероприятия","детские игры на улице","занятия для детей","встреча с родственниками",
        "выходной с семьей","детские развлечения","совместный отдых с семьей","планирование семейного времени",
        "семейная прогулка на улице","родительская забота","вместе с семьей","детские праздники","семейные занятия",
        "поход в парк","родительская поддержка","развлечение всей семьи","занятия спортом с детьми","поход на выходные",
        "семейный отдых на природе","совместные выезды","родительская ответственность","разговор с семьей","семейный досуг на улице",
        "детский отдых","встреча с родственниками","семейные праздники и мероприятия","выезд всей семьей",
        "организация семейного досуга","детский сад и школа","занятия творчеством","совместное времяпровождение","воспитание детей",
        "родительский контроль учебы","планирование совместных мероприятий","детские каникулы","организация праздников",
        "семейное время","занятия для всей семьи","встречи с родными","покупки для семьи","семейное планирование",
        "праздничные мероприятия","отдых с детьми на выходные","занятия спортом всей семьи","совместные походы","детские походы",
        "выходные с детьми","организация семейных встреч","родительское сопровождение","семейные пикники","совместные экскурсии",
        "организация семейного досуга","семейные мероприятия на природе","поход на природу всей семьей","занятия с детьми на улице"
    ],
    "здоровье": ["врач", "спорт", "тренировка", "бег", "зал"],
    "развитие": ["обучение", "курс", "книга", "учеба"],
    "отдых": ["отдых", "прогулка", "отпуск"],
    "трансфер": ["поездка", "трансфер", "в аэропорт", "к детям", "в школу", "ехать", "в дороге"],
    "друзья": ["друг", "встреча с другом"],
}

def detect_category(text: str) -> str:
    text = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(word in text for word in keywords):
            return category
    return "прочее"

# ================= AUTH HELPERS =================
def is_authorized(user_id: int) -> bool:
    return os.path.exists(f"tokens/{user_id}.json")

def auth_keyboard(user_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            text="🔐 Авторизоваться в Google",
            url=f"{BASE_URL}/auth/{user_id}"
        )]
    ])

# ================= ASYNC LOOP =================
event_loop = asyncio.new_event_loop()

def start_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

threading.Thread(target=start_loop, args=(event_loop,), daemon=True).start()


# ================= TELEGRAM =================
telegram_app = Application.builder().token(TG_TOKEN).build()
telegram_app.add_handler(CallbackQueryHandler(handle_conflict_choice, pattern="^conflict_"))
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
telegram_app.add_handler(MessageHandler(filters.VOICE, handle_voice))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["💰 Баланс", "📊 Расходы по категориям"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    text = (
        "👋 Я календарь-бот.\n"
        "Напиши, например:\n"
        "• завтра в 15 работа\n"
        "• сегодня 19:00 спорт"
    )

    if not is_authorized(update.effective_user.id):
        await update.message.reply_text(
            text + "\n\n🔐 Для календаря нужна авторизация:",
            reply_markup=auth_keyboard(update.effective_user.id)
        )
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔵 HANDLE_TEXT CALLED")

    user_id = update.effective_user.id
    text = update.message.text.strip()

    logger.info(f"🔵 TEXT = {text}")

    # ================= ФИНАНСЫ =================
    try:
        if text == "💰 Баланс":
            balance = get_balance(user_id)
            await update.message.reply_text(f"💰 Текущий баланс: {balance} ₽")
            return

        if text == "📊 Расходы по категориям":
            summary = get_summary_by_category(user_id)
            if not summary:
                await update.message.reply_text("📊 Расходов пока нет")
                return
            lines = ["📊 Расходы по категориям:"]
            for cat, total in summary.items():
                lines.append(f"• {cat}: {total} ₽")
            await update.message.reply_text("\n".join(lines))
            return

        parsed = parse_finance(text)
        if parsed:
            action, amount, category, comment = parsed
            add_transaction(user_id, action, amount, category, comment)
            sign = "➕" if action == "income" else "➖"
            await update.message.reply_text(
                f"{sign} {amount} ₽\n"
                f"📂 {category}\n"
                f"💰 Баланс: {get_balance(user_id)} ₽"
            )
            return

    except Exception:
        logger.exception("Ошибка в блоке финансов")
        await update.message.reply_text("❌ Ошибка обработки финансов")
        return

    # ================= КАЛЕНДАРЬ =================
    if not is_authorized(user_id):
        await update.message.reply_text(
            "🔐 Для работы с календарём нужна авторизация",
            reply_markup=auth_keyboard(user_id)
        )
        return

    try:
         if isinstance(result, dict) and result.get("status") == "CONFLICT":
             context.user_data["conflict"] = result

             keyboard = InlineKeyboardMarkup([[
                 InlineKeyboardButton("🔗 Объединить", callback_data="conflict_merge"),
                 InlineKeyboardButton("♻️ Заменить", callback_data="conflict_replace"),
                 InlineKeyboardButton("🕒 Другое время", callback_data="conflict_new_time"),
             ]])

             await update.message.reply_text(
                 "⚠️ Этот слот уже занят. Выберите действие:",
                 reply_markup=keyboard
             )
             return

    # ✅ ЕСЛИ КОНФЛИКТА НЕТ — ТОГДА РАСПАКОВКА
         dt, category = result
         await update.message.reply_text(
            f"✅ Событие создано\n"
            f"📂 {category}\n"
            f"🕒 {dt.strftime('%d.%m %H:%M')}"
         )

    except ValueError:
        await update.message.reply_text(
            "❌ Не удалось распознать дату.\n"
            "Примеры:\n"
            "• завтра в 15 работа\n"
            "• 30 января в 10 поездка"
        )

    except Exception:
        logger.exception("Ошибка при создании события")
        await update.message.reply_text("❌ Ошибка при создании события")

telegram_app.add_handler(
    CallbackQueryHandler(handle_conflict_choice, pattern="^conflict_")
)
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
)
from handlers.calendar_conflicts import handle_conflict_choice



from handlers.voice import handle_voice
telegram_app.add_handler(MessageHandler(filters.VOICE, handle_voice))

# ================= PARSER + SUMMARY =================
from datetime import datetime, timedelta
import re

# -------- Константы --------
DAYS_OF_WEEK = {
    "понедельник": 0,
    "вторник": 1,
    "среда": 2,
    "четверг": 3,
    "пятница": 4,
    "суббота": 5,
    "воскресенье": 6,
}

TIME_WORDS = {
    "утром": 9,
    "днем": 14,
    "вечером": 18,
    "ночью": 23,
}

RELATIVE_DAYS = {
    "сегодня": 0,
    "завтра": 1,
    "послезавтра": 2,
}

EVENT_TYPES = [
    "встреча",
    "созвон",
    "звонок",
    "поездка",
    "вылет",
    "совещание",
]

PREPOSITIONS = ["в", "к", "с", "по", "на"]

TIME_PATTERN = r"(\d{1,2})[:\.;\-\s]?(\d{2})?"  # 18:00, 18-00, 18.00, 18;00, 18 00, 18

# -------- Парсер даты/времени --------
def parse_datetime(text: str) -> datetime:
    text = text.lower().strip()
    now = datetime.now()
    base_date = None
    hour, minute = 10, 0
    explicit_date = False

    # ---- 1. Относительные дни ----
    for word, offset in RELATIVE_DAYS.items():
        if word in text:
            base_date = now + timedelta(days=offset)
            explicit_date = True
            break

    # ---- 2. Дни недели ----
    for day_name, day_num in DAYS_OF_WEEK.items():
        match = re.search(rf"(следующ[ийая]?|в)?\s*{day_name}", text)
        if match:
            days_ahead = (day_num - now.weekday() + 7) % 7
            if "следующ" in (match.group(1) or ""):
                days_ahead += 7
            elif days_ahead == 0:
                days_ahead = 7
            base_date = now + timedelta(days=days_ahead)
            explicit_date = True
            break

    # ---- 3. Относительные выражения ----
    rel_match = re.search(r"через\s+(\d+)\s*дн(?:я|ей)?", text)
    if rel_match:
        base_date = now + timedelta(days=int(rel_match.group(1)))
        explicit_date = True
    elif "через неделю" in text:
        base_date = now + timedelta(days=7)
        explicit_date = True

    # ---- 4. Дата dd.mm или '30 января'/'30 янв' ----
    # Формат dd.mm
    dot_match = re.search(r"\b(\d{1,2})\.(\d{1,2})\b", text)
    if dot_match:
        day, month = int(dot_match.group(1)), int(dot_match.group(2))
        try:
            base_date = datetime(now.year, month, day)
            explicit_date = True
        except ValueError:
            pass  # неверная дата, игнорируем

    # Формат '30 января' или '30 янв'
    month_names = {
        "января": 1, "янв": 1,
        "февраля": 2, "фев": 2,
        "марта": 3, "мар": 3,
        "апреля": 4, "апр": 4,
        "мая": 5,
        "июня": 6, "июн": 6,
        "июля": 7, "июл": 7,
        "августа": 8, "авг": 8,
        "сентября": 9, "сен": 9,
        "октября": 10, "окт": 10,
        "ноября": 11, "ноя": 11,
        "декабря": 12, "дек": 12,
    }
    month_match = re.search(r"\b(\d{1,2})\s*(\w+)\b", text)
    if month_match:
        day_str, mon_str = month_match.groups()
        mon_str = mon_str.lower()
        if mon_str in month_names:
            day = int(day_str)
            month = month_names[mon_str]
            try:
                base_date = datetime(now.year, month, day)
                explicit_date = True
            except ValueError:
                pass

    # ---- 5. Диапазоны времени ----
    range_match = re.search(r"с\s*" + TIME_PATTERN + r"\s*до\s*" + TIME_PATTERN, text)
    if range_match:
        hour = int(range_match.group(1))
        minute = int(range_match.group(2) or 0)
        explicit_date = True

    # ---- 6. Время ----
    time_match = re.search(TIME_PATTERN, text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        explicit_date = True

    # ---- 7. Временные слова ----
    for word, h in TIME_WORDS.items():
        if word in text:
            hour = h
            explicit_date = True
            break

    if not base_date:
        base_date = now

    # ---- 8. Формируем datetime ----
    result = base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # ---- 9. Коррекция прошедшего времени ----
    if result < now and not explicit_date:
        result += timedelta(days=1)

    return result

# -------- Очистка текста для summary --------
def clean_summary_text(text: str) -> str:
    text = re.sub(r"\b\d{1,2}[:\.;\-\s]?\d{0,2}\b", "", text)
    for word in list(RELATIVE_DAYS.keys()) + list(TIME_WORDS.keys()) + ["сегодня", "завтра"]:
        text = re.sub(rf"\b{word}\b", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"\b(в|к|с|по|на)\s*$", "", text)
    return text

# -------- Определение события и сборка summary --------
def build_summary(text: str) -> str:
    original = text.strip()
    cleaned = original

    # 1. Убираем время (16:00, 18.00, в 18, в 9)
    cleaned = re.sub(r"\bв?\s*\d{1,2}([:.]\d{2})?\b", "", cleaned, flags=re.IGNORECASE)

    # 2. Убираем слова времени
    for word in TIME_WORDS + ["сегодня", "завтра", "послезавтра"]:
        cleaned = re.sub(rf"\b{word}\b", "", cleaned, flags=re.IGNORECASE)

    # 3. Убираем точки и двоеточия в конце
    cleaned = re.sub(r"[:\.]+$", "", cleaned)

    # 4. Определяем тип события
    event_type = None
    for t in EVENT_TYPES:
        if re.search(rf"\b{t}\b", cleaned, re.IGNORECASE):
            event_type = t.capitalize()
            cleaned = re.sub(rf"\b{t}\b", "", cleaned, flags=re.IGNORECASE)
            break

    # 5. Убираем висячие предлоги
    cleaned = re.sub(r"\b(в|к|с|по|на)\s*$", "", cleaned, flags=re.IGNORECASE)

    # 6. Финальная чистка пробелов
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    # 7. Сборка summary
    if event_type and cleaned:
        return f"{event_type} {cleaned}"
    elif event_type:
        return event_type
    else:
        return cleaned

# ================= GOOGLE CALENDAR =================
import pytz
TIMEZONE = "Europe/Saratov"
tz = pytz.timezone(TIMEZONE)

def get_flow():
    client_config = json.loads(os.environ["GOOGLE_CLIENT_CONFIG"])
    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=f"{BASE_URL}/auth/callback",
    )

def get_calendar_service(user_id: int):
    path = f"tokens/{user_id}.json"
    if not os.path.exists(path):
        return None
    creds = Credentials.from_authorized_user_file(path, SCOPES)
    return build("calendar", "v3", credentials=creds)

def create_event(user_id: int, text: str):
    service = get_calendar_service(user_id)
    if not service:
        raise RuntimeError("AUTH_REQUIRED")

    category = detect_category(text)
    color_id = str(CATEGORY_COLORS.get(category, "8"))
    dt = parse_datetime(text)
    if dt is None:
        raise ValueError("Дата не распознана")

    start = tz.localize(dt)


    summary = build_summary(text)

    event = {
        "summary": summary,
        "colorId": color_id,
        "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": (start + timedelta(hours=1)).isoformat(), "timeZone": TIMEZONE},
    }

    # -------- Проверка занятости --------
    busy_events = service.events().list(
        calendarId="primary",
        timeMin=start.astimezone(pytz.UTC).isoformat(),
        timeMax=(start + timedelta(hours=1)).astimezone(pytz.UTC).isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute().get("items", [])

    if busy_events:
        return {
            "status": "CONFLICT",
            "start": start,
            "end": start + timedelta(hours=1),
            "category": category,
            "new_event": event,
            "existing_event_id": busy_events[0]["id"],
        }

    # -------- Создание события, если свободно --------
    service.events().insert(calendarId="primary", body=event).execute()
    return start, category



# ================= OAUTH =================
@app.route("/auth/<int:user_id>")
def auth(user_id):
    flow = get_flow()
    url, _ = flow.authorization_url(
        state=str(user_id),
        prompt="consent",
        access_type="offline",
    )
    return redirect(url)

@app.route("/auth/callback")
def callback():
    code = request.args["code"]
    user_id = request.args["state"]

    flow = get_flow()
    flow.fetch_token(code=code)

    os.makedirs("tokens", exist_ok=True)
    with open(f"tokens/{user_id}.json", "w") as f:
        f.write(flow.credentials.to_json())

    telegram_app.bot.send_message(
        chat_id=int(user_id),
        text="✅ Авторизация завершена. Теперь можно создавать события."
    )

    return "✅ Авторизация завершена. Вернись в Telegram."

# ================= WEBHOOK =================
from flask import request

@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    update = Update.de_json(request.get_json(force=True), Bot(TG_TOKEN))
    # Обработка update через приложение
    return "ok"



# ================= START =================
if __name__ == "__main__":

    async def startup():
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.bot.set_webhook(f"{BASE_URL}/telegram/webhook")

    event_loop.call_soon_threadsafe(asyncio.create_task, startup())
    app.run(host="0.0.0.0", port=8080)
