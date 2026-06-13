import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import threading
import time
import json
import os
from datetime import datetime, date, timedelta
import pytz
import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")

bot = telebot.TeleBot(TOKEN)

DATA_FILE = "user_data.json"

RUSSIAN_TIMEZONES = [
    ("🌍 Калининград",   "Europe/Kaliningrad",  "UTC+2"),
    ("🏛 Москва",         "Europe/Moscow",        "UTC+3"),
    ("🌊 Самара",         "Europe/Samara",        "UTC+4"),
    ("⛰ Екатеринбург",   "Asia/Yekaterinburg",   "UTC+5"),
    ("🌾 Омск",           "Asia/Omsk",            "UTC+6"),
    ("🌲 Красноярск",    "Asia/Krasnoyarsk",     "UTC+7"),
    ("🏔 Иркутск",        "Asia/Irkutsk",         "UTC+8"),
    ("🦌 Якутск",         "Asia/Yakutsk",         "UTC+9"),
    ("🌊 Владивосток",   "Asia/Vladivostok",     "UTC+10"),
    ("🌋 Магадан",        "Asia/Magadan",         "UTC+11"),
    ("🌏 Камчатка",      "Asia/Kamchatka",       "UTC+12"),
]

DEFAULT_TZ = "Europe/Moscow"

if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        data = json.load(f)
        user_tasks = {int(k): v for k, v in data.get("tasks", {}).items()}
        user_checklists = {int(k): v for k, v in data.get("checklists", {}).items()}
        user_remind_before = {int(k): v for k, v in data.get("remind_before", {}).items()}
        user_daily_time = {int(k): v for k, v in data.get("daily_time", {}).items()}
        user_timezone = {int(k): v for k, v in data.get("timezone", {}).items()}
        user_work_info = {int(k): v for k, v in data.get("work_info", {}).items()}
        user_city = {int(k): v for k, v in data.get("city", {}).items()}
else:
    user_tasks = {}
    user_checklists = {}
    user_remind_before = {}
    user_daily_time = {}
    user_timezone = {}
    user_work_info = {}
    user_city = {}

daily_sent_today = {}
pending_daily = {}
pending_plan = {}
pending_weather = {}
pending_work = {}
onboarding = {}

def save_all():
    with open(DATA_FILE, "w") as f:
        json.dump({
            "tasks": user_tasks,
            "checklists": user_checklists,
            "remind_before": user_remind_before,
            "daily_time": user_daily_time,
            "timezone": user_timezone,
            "work_info": user_work_info,
            "city": user_city
        }, f, indent=2)

def get_tz(user_id):
    tz_name = user_timezone.get(user_id, DEFAULT_TZ)
    try:
        return pytz.timezone(tz_name)
    except:
        return pytz.timezone(DEFAULT_TZ)

def now_for(user_id):
    return datetime.now(get_tz(user_id))

def parse_user_time(user_id, time_str, date_str=None):
    from datetime import timedelta
    tz = get_tz(user_id)
    now = datetime.now(tz)
    if date_str:
        # формат дд.мм или дд.мм.гггг
        parts = date_str.split(".")
        if len(parts) == 2:
            day, month = int(parts[0]), int(parts[1])
            year = now.year
            # если дата уже прошла — следующий год
            if (month, day) < (now.month, now.day):
                year += 1
        elif len(parts) == 3:
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            if year < 100:
                year += 2000
        else:
            raise ValueError("Неверный формат даты")
        naive = datetime.strptime(f"{year}-{month:02d}-{day:02d} {time_str}", "%Y-%m-%d %H:%M")
    else:
        naive = datetime.strptime(time_str, "%H:%M").replace(
            year=now.year, month=now.month, day=now.day
        )
    local_dt = tz.localize(naive)
    if not date_str and local_dt <= now:
        local_dt += timedelta(days=1)
    return local_dt

DEFAULT_ITEMS = [
    "🔌 Свет выключен",
    "🪟 Окна закрыты",
    "💳 Кошелёк/карты",
    "📱 Телефон",
    "🔑 Ключи",
    "🔑 Ключи для работы",
    "🍎 Еда на работу",
    "🚰 Вода/газ выключены",
    "🗑 Мусор вынесен"
]

active_checklists = {}

def get_checklist(user_id):
    return user_checklists.get(user_id, DEFAULT_ITEMS.copy())

def build_morning_header(user_id):
    lines = []
    rates = get_exchange_rates()
    if rates:
        lines.append(rates)
    city = user_city.get(user_id)
    if city:
        weather = get_weather(city)
        if weather:
            lines.append(f"\n🌍 Погода в {city}:\n{weather}")
    work = user_work_info.get(user_id)
    if work:
        try:
            arrival = work["arrival"]
            travel = int(work["travel"])
            arr_h, arr_m = map(int, arrival.split(":"))
            total_minutes = arr_h * 60 + arr_m - travel
            leave_h = total_minutes // 60 % 24
            leave_m = total_minutes % 60
            lines.append(f"\n🚌 Выходи в {leave_h:02d}:{leave_m:02d} (дорога ~{travel} мин, к {arrival} на работе)")
        except:
            pass
    return "\n".join(lines)

def send_checklist(user_id, items, is_reminder=False):
    if is_reminder:
        header = build_morning_header(user_id)
        if header:
            try:
                bot.send_message(user_id, header)
            except:
                pass
        try:
            meme_url = get_meme()
            if meme_url:
                bot.send_photo(user_id, meme_url, caption="😂 Утренний мем")
        except:
            pass
    keyboard = InlineKeyboardMarkup(row_width=1)
    for i, item in enumerate(items):
        keyboard.add(InlineKeyboardButton(f"⬜️ {item}", callback_data=f"chk_{user_id}_{i}"))
    keyboard.add(InlineKeyboardButton("🏁 Завершить", callback_data=f"chk_finish_{user_id}"))
    text = "⏰ Пора собираться!" if is_reminder else "📋 Твой чеклист:"
    sent = bot.send_message(user_id, text, reply_markup=keyboard)
    active_checklists[user_id] = {
        "checked_ids": set(),
        "items": items,
        "message_id": sent.message_id,
        "is_reminder": is_reminder
    }

def update_checklist_keyboard(user_id):
    if user_id not in active_checklists:
        return
    data = active_checklists[user_id]
    checked = data["checked_ids"]
    items = data["items"]
    keyboard = InlineKeyboardMarkup(row_width=1)
    for i, item in enumerate(items):
        status = "✅ " if i in checked else "⬜️ "
        keyboard.add(InlineKeyboardButton(f"{status}{item}", callback_data=f"chk_{user_id}_{i}"))
    keyboard.add(InlineKeyboardButton("🏁 Завершить", callback_data=f"chk_finish_{user_id}"))
    try:
        bot.edit_message_reply_markup(user_id, data["message_id"], reply_markup=keyboard)
    except:
        pass

def check_scheduler():
    while True:
        now_utc = time.time()

        # Ежедневный чеклист
        for user_id, time_str in list(user_daily_time.items()):
            tz = get_tz(user_id)
            local_now = datetime.now(tz)
            today_key = local_now.strftime("%Y-%m-%d")
            sent_key = f"{user_id}_{today_key}"
            if daily_sent_today.get(sent_key):
                continue
            try:
                naive_target = datetime.strptime(today_key + " " + time_str, "%Y-%m-%d %H:%M")
                target = tz.localize(naive_target)
                if now_utc >= target.timestamp():
                    send_checklist(user_id, get_checklist(user_id), is_reminder=True)
                    daily_sent_today[sent_key] = True
            except:
                pass

        # Разовые планы
        to_delete = []
        for user_id, tasks in user_tasks.items():
            for task_time, items in tasks.copy().items():
                if now_utc >= float(task_time):
                    try:
                        send_checklist(user_id, items, is_reminder=True)
                    except:
                        pass
                    to_delete.append((user_id, task_time))
        for user_id, task_time in to_delete:
            del user_tasks[user_id][task_time]
            save_all()

        for user_id, minutes in user_remind_before.items():
            for task_time in list(user_tasks.get(user_id, {}).keys()):
                remind_time = float(task_time) - (minutes * 60)
                if now_utc >= remind_time and remind_time > 0:
                    key = f"reminded_{user_id}_{task_time}"
                    if not getattr(check_scheduler, key, False):
                        setattr(check_scheduler, key, True)
                        try:
                            bot.send_message(user_id, f"⚠️ Напоминаю: до выхода {minutes} минут!")
                        except:
                            pass
        time.sleep(30)

def tz_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    for label, tz_name, utc in RUSSIAN_TIMEZONES:
        keyboard.add(InlineKeyboardButton(
            f"{label} ({utc})",
            callback_data=f"tz_{tz_name}"
        ))
    return keyboard

def is_new_user(user_id):
    return (user_id not in user_timezone and
            user_id not in user_daily_time and
            user_id not in user_city)

def onboarding_step_tz(user_id):
    onboarding[user_id] = "tz"
    bot.send_message(user_id,
        "👋 Привет! Давай настроим бот за минуту.\n\n"
        "Шаг 1 из 4 — 🌐 Выбери свой часовой пояс:",
        reply_markup=tz_keyboard())

def onboarding_step_city(user_id):
    onboarding[user_id] = "city"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("⏭ Пропустить", callback_data="setup_skip_city"))
    bot.send_message(user_id,
        "Шаг 2 из 4 — 🌍 В каком городе живёшь?\n\n"
        "Напиши город — буду показывать погоду каждое утро.\n"
        "Например: Москва",
        reply_markup=kb)

def onboarding_step_daily(user_id):
    onboarding[user_id] = "daily"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("⏭ Пропустить", callback_data="setup_skip_daily"))
    bot.send_message(user_id,
        "Шаг 3 из 4 — ⏰ В какое время присылать чеклист каждый день?\n\n"
        "Напиши время в формате 07:30",
        reply_markup=kb)

def onboarding_step_work(user_id):
    onboarding[user_id] = "work"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("⏭ Пропустить", callback_data="setup_skip_work"))
    bot.send_message(user_id,
        "Шаг 4 из 4 — 🚌 Во сколько надо быть на работе и сколько ехать?\n\n"
        "Напиши через пробел: время и минуты в дороге\n"
        "Например: 09:00 40\n\n"
        "Каждое утро буду писать во сколько выходить 👆",
        reply_markup=kb)

def onboarding_done(user_id):
    onboarding.pop(user_id, None)
    tz_name = user_timezone.get(user_id, DEFAULT_TZ)
    tz_label = next((f"{l} ({utc})" for l, tz, utc in RUSSIAN_TIMEZONES if tz == tz_name), tz_name)
    daily = user_daily_time.get(user_id)
    city = user_city.get(user_id)
    work = user_work_info.get(user_id)
    lines = ["✅ Всё настроено! Вот твои параметры:\n",
             f"🌐 Часовой пояс: {tz_label}",
             f"🌍 Город: {city if city else '—'}",
             f"⏰ Ежедневный чеклист: {daily if daily else '—'}"]
    if work:
        lines.append(f"🚌 Работа: к {work['arrival']}, дорога {work['travel']} мин")
    lines.append("\nМожешь изменить любой параметр:\n"
                 "/timezone /weather /daily /setwork\n\n"
                 "Чтобы показать чеклист сейчас — /checklist")
    bot.send_message(user_id, "\n".join(lines))

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.chat.id
    if is_new_user(user_id):
        onboarding_step_tz(user_id)
    else:
        bot.reply_to(message, "✅ Бот-будильник с чеклистом\n\n"
                             "/timezone — часовой пояс\n"
                             "/weather Москва — погода\n"
                             "/daily 07:30 — чеклист каждый день\n"
                             "/nodaily — отключить ежедневный\n"
                             "/plan 08:30 — разовый план\n"
                             "/setwork — время работы и дорога\n"
                             "/snooze 10 — отложить план\n"
                             "/setlist — свой список\n"
                             "/remind 10 — напомнить за X мин\n"
                             "/check — что не сделано\n"
                             "/plans — все планы\n"
                             "/status — мои настройки\n"
                             "/setup — настроить заново")

@bot.message_handler(commands=['setup'])
def setup_cmd(message):
    onboarding_step_tz(message.chat.id)

@bot.message_handler(commands=['setwork'])
def setwork_cmd(message):
    parts = message.text.split()
    user_id = message.chat.id
    if len(parts) == 3:
        try:
            datetime.strptime(parts[1], "%H:%M")
            travel = int(parts[2])
            user_work_info[user_id] = {"arrival": parts[1], "travel": travel}
            save_all()
            bot.reply_to(message, f"✅ Запомнил: к {parts[1]} на работе, дорога {travel} мин\nКаждое утро буду писать во сколько выходить 🚌")
            return
        except:
            pass
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("⏭ Пропустить", callback_data="setup_skip_work"))
    pending_work[user_id] = True
    bot.reply_to(message, "🚌 Напиши время прихода на работу и время в дороге:\n\nНапример: 09:00 40", reply_markup=kb)

@bot.message_handler(commands=['timezone'])
def choose_timezone(message):
    tz_name = user_timezone.get(message.chat.id, DEFAULT_TZ)
    label = next((l for l, tz, _ in RUSSIAN_TIMEZONES if tz == tz_name), tz_name)
    bot.reply_to(message, f"🌐 Текущий часовой пояс: {label}\n\nВыбери свой регион:", reply_markup=tz_keyboard())

@bot.callback_query_handler(func=lambda call: call.data.startswith("tz_"))
def handle_timezone(call):
    tz_name = call.data[3:]
    user_id = call.message.chat.id
    label = next((l for l, tz, utc in RUSSIAN_TIMEZONES if tz == tz_name), tz_name)
    utc = next((utc for _, tz, utc in RUSSIAN_TIMEZONES if tz == tz_name), "")
    user_timezone[user_id] = tz_name
    save_all()
    in_setup = onboarding.get(user_id) == "tz"
    bot.edit_message_text(
        f"✅ Часовой пояс: {label} ({utc})",
        call.message.chat.id,
        call.message.message_id
    )
    bot.answer_callback_query(call.id)
    if in_setup:
        onboarding_step_city(user_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("setup_skip_"))
def handle_setup_skip(call):
    user_id = call.message.chat.id
    step = call.data.replace("setup_skip_", "")
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass
    if step == "city":
        onboarding_step_daily(user_id)
    elif step == "daily":
        onboarding_step_work(user_id)
    elif step == "work":
        onboarding_done(user_id)

@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/') and onboarding.get(m.chat.id) == "city")
def handle_onboarding_city(message):
    user_id = message.chat.id
    city = message.text.strip()
    result = get_weather(city)
    if result:
        user_city[user_id] = city
        save_all()
        bot.reply_to(message, f"✅ Город сохранён: {city}\n\n{result}")
        onboarding_step_daily(user_id)
    else:
        bot.reply_to(message, "❌ Город не найден. Попробуй написать иначе, например: Москва или Moscow")

@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/') and onboarding.get(m.chat.id) == "daily")
def handle_onboarding_daily(message):
    user_id = message.chat.id
    time_str = message.text.strip()
    try:
        datetime.strptime(time_str, "%H:%M")
    except:
        bot.reply_to(message, "❌ Неверный формат. Напиши как 07:30")
        return
    user_daily_time[user_id] = time_str
    save_all()
    bot.reply_to(message, f"✅ Чеклист каждый день в {time_str}")
    onboarding_step_work(user_id)

@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/') and onboarding.get(m.chat.id) == "work")
def handle_onboarding_work(message):
    user_id = message.chat.id
    parts = message.text.strip().split()
    try:
        if len(parts) != 2:
            raise ValueError()
        datetime.strptime(parts[0], "%H:%M")
        travel = int(parts[1])
        user_work_info[user_id] = {"arrival": parts[0], "travel": travel}
        save_all()
        bot.reply_to(message, f"✅ Запомнил: к {parts[0]} на работе, дорога {travel} мин")
        onboarding_done(user_id)
    except:
        bot.reply_to(message, "❌ Неверный формат. Напиши: 09:00 40")

@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/') and m.chat.id in pending_work)
def handle_pending_work(message):
    user_id = message.chat.id
    parts = message.text.strip().split()
    try:
        if len(parts) != 2:
            raise ValueError()
        datetime.strptime(parts[0], "%H:%M")
        travel = int(parts[1])
        user_work_info[user_id] = {"arrival": parts[0], "travel": travel}
        pending_work.pop(user_id, None)
        save_all()
        bot.reply_to(message, f"✅ Запомнил: к {parts[0]} на работе, дорога {travel} мин\nКаждое утро буду писать во сколько выходить 🚌")
    except:
        bot.reply_to(message, "❌ Неверный формат. Напиши: 09:00 40")

@bot.message_handler(commands=['plan'])
def plan(message):
    parts = message.text.split()
    if len(parts) == 1:
        pending_plan[message.chat.id] = True
        bot.reply_to(message, "📅 На какое время и дату запланировать?\n\nПримеры:\n• 08:30 — сегодня/завтра\n• 08:30 15.06 — конкретный день")
        return
    time_str = parts[1]
    date_str = parts[2] if len(parts) >= 3 else None
    _apply_plan(message.chat.id, time_str, message, date_str)

def _apply_plan(user_id, time_str, message, date_str=None):
    try:
        local_dt = parse_user_time(user_id, time_str, date_str)
        timestamp = local_dt.timestamp()
    except Exception as e:
        bot.reply_to(message, "❌ Неверный формат.\n\nПримеры:\n• 08:30\n• 08:30 15.06")
        return
    if user_id not in user_tasks:
        user_tasks[user_id] = {}
    user_tasks[user_id][timestamp] = get_checklist(user_id)
    pending_plan.pop(user_id, None)
    save_all()
    bot.reply_to(message, f"✅ Запланировал на {local_dt.strftime('%H:%M %d.%m')}")

@bot.message_handler(commands=['timer'])
def set_timer(message):
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "❌ /timer 07:30")
        return
    user_id = message.chat.id
    try:
        local_dt = parse_user_time(user_id, parts[1])
        timestamp = local_dt.timestamp()
        if user_id not in user_tasks:
            user_tasks[user_id] = {}
        user_tasks[user_id][timestamp] = get_checklist(user_id)
        save_all()
        bot.reply_to(message, f"⏰ Таймер на {local_dt.strftime('%H:%M %d.%m')}")
    except:
        bot.reply_to(message, "❌ Ошибка")

@bot.message_handler(commands=['setlist'])
def setlist(message):
    bot.reply_to(message, "📝 Отправь новый список построчно:\n\nСвет\nТелефон\nКлючи\nЕда")

@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/') and m.chat.id in pending_daily)
def handle_pending_daily(message):
    _apply_daily(message.chat.id, message.text.strip(), message)

@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/') and m.chat.id in pending_plan)
def handle_pending_plan(message):
    parts = message.text.strip().split()
    time_str = parts[0]
    date_str = parts[1] if len(parts) >= 2 else None
    _apply_plan(message.chat.id, time_str, message, date_str)

@bot.message_handler(func=lambda m: m.text and len(m.text.split('\n')) > 1 and m.chat.id in user_tasks)
def save_setlist(message):
    items = [line.strip() for line in message.text.split('\n') if line.strip()]
    if len(items) >= 2:
        user_checklists[message.chat.id] = items
        save_all()
        bot.reply_to(message, f"✅ Чеклист сохранён ({len(items)} пунктов)")

@bot.message_handler(commands=['remind'])
def set_remind(message):
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "❌ /remind 10")
        return
    try:
        minutes = int(parts[1])
        if 1 <= minutes <= 60:
            user_remind_before[message.chat.id] = minutes
            save_all()
            bot.reply_to(message, f"✅ Напомню за {minutes} минут")
        else:
            bot.reply_to(message, "❌ От 1 до 60")
    except:
        bot.reply_to(message, "❌ Нужно число")

@bot.message_handler(commands=['checklist'])
def show_checklist(message):
    user_id = message.chat.id
    send_checklist(user_id, get_checklist(user_id), is_reminder=False)

@bot.message_handler(commands=['check'])
def check_status(message):
    user_id = message.chat.id
    if user_id in active_checklists:
        data = active_checklists[user_id]
        checked = data["checked_ids"]
        items = data["items"]
        done = len(checked)
        total = len(items)
        if done == total:
            bot.reply_to(message, "✅ Всё готово!")
        else:
            missing = [items[i] for i in range(total) if i not in checked]
            bot.reply_to(message, f"✅ {done}/{total}\n\n❌ Осталось:\n" + "\n".join(f"• {i}" for i in missing[:10]))
    else:
        bot.reply_to(message, "Нет активного чеклиста. Запланируй /plan")

@bot.message_handler(commands=['plans'])
def show_plans(message):
    user_id = message.chat.id
    tz = get_tz(user_id)
    if user_id not in user_tasks or not user_tasks[user_id]:
        bot.reply_to(message, "Нет планов")
        return
    text = "📅 Планы:\n"
    remind = user_remind_before.get(user_id, 0)
    if remind:
        text += f"⏰ Напомню за {remind} мин\n"
    for ts in user_tasks[user_id].keys():
        dt = datetime.fromtimestamp(float(ts), tz=tz)
        text += f"• {dt.strftime('%H:%M %d.%m')}\n"
    bot.reply_to(message, text)

@bot.message_handler(commands=['del'])
def delete_plan(message):
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "❌ /del 08:30")
        return
    time_str = parts[1]
    user_id = message.chat.id
    tz = get_tz(user_id)
    if user_id not in user_tasks:
        bot.reply_to(message, "Нет планов")
        return
    found = None
    for ts in list(user_tasks[user_id].keys()):
        dt = datetime.fromtimestamp(float(ts), tz=tz)
        if dt.strftime("%H:%M") == time_str:
            found = ts
            break
    if found:
        del user_tasks[user_id][found]
        save_all()
        bot.reply_to(message, f"❌ Удалил {time_str}")
    else:
        bot.reply_to(message, f"Не нашёл план на {time_str}")

@bot.message_handler(commands=['daily'])
def set_daily(message):
    parts = message.text.split()
    if len(parts) == 1:
        pending_daily[message.chat.id] = True
        bot.reply_to(message, "⏰ В какое время присылать чеклист?\nНапиши время в формате: 07:30")
        return
    _apply_daily(message.chat.id, parts[1], message)

def _apply_daily(user_id, time_str, message):
    try:
        datetime.strptime(time_str, "%H:%M")
    except:
        bot.reply_to(message, "❌ Неверный формат. Напиши время как 07:30")
        return
    tz_name = user_timezone.get(user_id, DEFAULT_TZ)
    label = next((l for l, tz, _ in RUSSIAN_TIMEZONES if tz == tz_name), tz_name)
    user_daily_time[user_id] = time_str
    pending_daily.pop(user_id, None)
    save_all()
    bot.reply_to(message, f"✅ Буду присылать чеклист каждый день в {time_str} ({label}) ⏰")

@bot.message_handler(commands=['status'])
def status(message):
    user_id = message.chat.id
    tz_name = user_timezone.get(user_id, DEFAULT_TZ)
    tz_label = next((f"{l} ({utc})" for l, tz, utc in RUSSIAN_TIMEZONES if tz == tz_name), tz_name)
    daily = user_daily_time.get(user_id)
    remind = user_remind_before.get(user_id)
    tasks = user_tasks.get(user_id, {})
    tz = get_tz(user_id)

    lines = ["⚙️ Твои настройки:\n"]
    lines.append(f"🌐 Часовой пояс: {tz_label}")
    lines.append(f"⏰ Ежедневный чеклист: {daily if daily else '❌ не установлен'}")
    lines.append(f"🔔 Напомнить заранее: {f'{remind} мин' if remind else '❌ не установлено'}")

    if tasks:
        lines.append(f"\n📅 Разовые планы ({len(tasks)}):")
        for ts in sorted(float(k) for k in tasks.keys()):
            dt = datetime.fromtimestamp(ts, tz=tz)
            lines.append(f"  • {dt.strftime('%H:%M %d.%m')}")
    else:
        lines.append("\n📅 Разовых планов нет")

    checklist = get_checklist(user_id)
    is_custom = user_id in user_checklists
    lines.append(f"\n📋 Чеклист: {'свой' if is_custom else 'стандартный'} ({len(checklist)} пунктов)")

    bot.reply_to(message, "\n".join(lines))

@bot.message_handler(commands=['nodaily'])
def remove_daily(message):
    user_id = message.chat.id
    if user_id in user_daily_time:
        del user_daily_time[user_id]
        save_all()
        bot.reply_to(message, "❌ Ежедневный чеклист отключён")
    else:
        bot.reply_to(message, "У тебя не было ежедневного чеклиста")

@bot.message_handler(commands=['reset'])
def reset_confirm(message):
    keyboard = InlineKeyboardMarkup()
    keyboard.add(
        InlineKeyboardButton("✅ Да, сбросить всё", callback_data=f"reset_confirm_{message.chat.id}"),
        InlineKeyboardButton("❌ Отмена", callback_data=f"reset_cancel_{message.chat.id}")
    )
    bot.reply_to(message, "⚠️ Сбросить все настройки?\n\nБудут удалены: часовой пояс, ежедневный чеклист, напоминания, планы и свой список.", reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith("reset_"))
def handle_reset(call):
    parts = call.data.split("_")
    action = parts[1]
    user_id = int(parts[2])
    if user_id != call.message.chat.id:
        bot.answer_callback_query(call.id, "Не твоя кнопка")
        return
    if action == "confirm":
        user_tasks.pop(user_id, None)
        user_checklists.pop(user_id, None)
        user_remind_before.pop(user_id, None)
        user_daily_time.pop(user_id, None)
        user_timezone.pop(user_id, None)
        pending_daily.pop(user_id, None)
        pending_plan.pop(user_id, None)
        save_all()
        bot.edit_message_text("✅ Все настройки сброшены. Начинаем с чистого листа!", call.message.chat.id, call.message.message_id)
    else:
        bot.edit_message_text("❌ Сброс отменён.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.message_handler(commands=['snooze'])
def snooze_plan(message):
    parts = message.text.split()
    minutes = 10
    if len(parts) == 2:
        try:
            minutes = int(parts[1])
            if not (1 <= minutes <= 120):
                bot.reply_to(message, "❌ От 1 до 120 минут")
                return
        except:
            bot.reply_to(message, "❌ Нужно число, например: /snooze 15")
            return
    user_id = message.chat.id
    tz = get_tz(user_id)
    tasks = user_tasks.get(user_id, {})
    if not tasks:
        bot.reply_to(message, "Нет запланированных планов")
        return
    nearest_ts = min(float(k) for k in tasks.keys())
    nearest_key = next(k for k in tasks.keys() if float(k) == nearest_ts)
    items = tasks[nearest_key]
    new_ts = nearest_ts + minutes * 60
    del user_tasks[user_id][nearest_key]
    user_tasks[user_id][new_ts] = items
    save_all()
    new_time = datetime.fromtimestamp(new_ts, tz=tz).strftime('%H:%M %d.%m')
    bot.reply_to(message, f"⏰ Отложил на {minutes} мин → теперь в {new_time}")

DAYS_RU = ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]

def weather_emoji(desc):
    d = desc.lower()
    if "гроза" in d: return "⛈"
    if "дождь" in d or "ливень" in d or "морось" in d: return "🌧"
    if "снег" in d or "метель" in d or "вьюга" in d: return "❄️"
    if "туман" in d: return "🌫"
    if "пасмурн" in d: return "☁️"
    if "облач" in d or "переменн" in d: return "⛅️"
    return "☀️"

def what_to_wear(temp_c, desc):
    t = int(temp_c)
    d = desc.lower()
    has_rain = any(w in d for w in ["дождь","ливень","морось","гроза"])
    has_snow = any(w in d for w in ["снег","метель","вьюга"])
    if t <= -20:
        outfit = "🥶 Очень морозно! Термобельё, пуховик, шапка, шарф, варежки"
    elif t <= -10:
        outfit = "🧥 Пуховик, шапка, шарф, перчатки"
    elif t <= 0:
        outfit = "🧥 Тёплая куртка, шапка, перчатки"
    elif t <= 8:
        outfit = "🧥 Куртка, шапка можно"
    elif t <= 15:
        outfit = "🫧 Лёгкая куртка или пальто"
    elif t <= 22:
        outfit = "👕 Кофта или лёгкая куртка"
    else:
        outfit = "☀️ Лёгкая одежда, жарко!"
    if has_rain: outfit += " ☂️ Возьми зонт!"
    if has_snow: outfit += " 👢 Обуй непромокаемое!"
    return outfit

def get_exchange_rates():
    try:
        r = requests.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=8)
        d = r.json()
        usd = round(d["Valute"]["USD"]["Value"], 2)
        eur = round(d["Valute"]["EUR"]["Value"], 2)
        cny = round(d["Valute"]["CNY"]["Value"], 2)
        return f"💵 Доллар: {usd}₽  💶 Евро: {eur}₽  🀄 Юань: {cny}₽"
    except:
        return None

def get_meme():
    try:
        r = requests.get("https://meme-api.com/gimme/ru", timeout=8)
        d = r.json()
        if d.get("url") and not d.get("nsfw", False):
            return d["url"]
        r2 = requests.get("https://meme-api.com/gimme", timeout=8)
        d2 = r2.json()
        return d2.get("url")
    except:
        return None

def get_weather(city):
    try:
        url = f"https://wttr.in/{requests.utils.quote(city)}?format=j1&lang=ru"
        r = requests.get(url, timeout=10)
        d = r.json()
        cur = d["current_condition"][0]
        temp = cur["temp_C"]
        feels = cur["FeelsLikeC"]
        desc = cur["lang_ru"][0]["value"]
        wind = cur["windspeedKmph"]
        humidity = cur["humidity"]
        today = d["weather"][0]
        max_t = today["maxtempC"]
        min_t = today["mintempC"]
        emoji = weather_emoji(desc)
        wear = what_to_wear(temp, desc)

        forecast_lines = []
        for i, w in enumerate(d["weather"][:3]):
            day_date = date.today() + timedelta(days=i)
            day_name = DAYS_RU[day_date.weekday()]
            d_desc = w["hourly"][4]["lang_ru"][0]["value"] if w["hourly"] else ""
            d_emoji = weather_emoji(d_desc)
            forecast_lines.append(
                f"  {day_name} {day_date.strftime('%d.%m')} {d_emoji} {w['maxtempC']}°/{w['mintempC']}°"
            )

        return (f"{emoji} {desc}\n"
                f"🌡 {temp}°C (ощущается {feels}°C)\n"
                f"📊 Днём {max_t}° / ночью {min_t}°  💨 {wind} км/ч  💧 {humidity}%\n"
                f"\n{wear}\n"
                f"\n📅 Прогноз:\n" + "\n".join(forecast_lines))
    except:
        return None

def weather_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("📍 Сменить город", callback_data="weather_change"))
    return keyboard

@bot.message_handler(commands=['weather'])
def weather_cmd(message):
    user_id = message.chat.id
    parts = message.text.split(maxsplit=1)
    if len(parts) == 1:
        city = user_city.get(user_id)
        if city:
            result = get_weather(city)
            if result:
                bot.reply_to(message, f"🌍 {city}\n\n{result}", reply_markup=weather_keyboard())
            else:
                bot.reply_to(message, "❌ Не могу получить погоду, попробуй позже", reply_markup=weather_keyboard())
        else:
            pending_weather[user_id] = True
            bot.reply_to(message, "🌍 Напиши свой город:\n\nНапример: Москва")
        return
    city = parts[1].strip()
    user_city[user_id] = city
    result = get_weather(city)
    if result:
        bot.reply_to(message, f"🌍 {city}\n\n{result}\n\n✅ Город сохранён — теперь просто /weather", reply_markup=weather_keyboard())
    else:
        bot.reply_to(message, "❌ Город не найден. Попробуй написать по-английски: Moscow")

@bot.callback_query_handler(func=lambda call: call.data == "weather_change")
def handle_weather_change(call):
    user_id = call.message.chat.id
    pending_weather[user_id] = True
    bot.answer_callback_query(call.id)
    bot.send_message(user_id, "🌍 Напиши новый город:\n\nНапример: Москва или Екатеринбург")

@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/') and m.chat.id in pending_weather)
def handle_pending_weather(message):
    user_id = message.chat.id
    city = message.text.strip()
    result = get_weather(city)
    if result:
        user_city[user_id] = city
        pending_weather.pop(user_id, None)
        bot.reply_to(message, f"🌍 {city}\n\n{result}\n\n✅ Город сохранён — теперь просто /weather", reply_markup=weather_keyboard())
    else:
        bot.reply_to(message, "❌ Город не найден. Попробуй написать по-английски: Moscow")

@bot.callback_query_handler(func=lambda call: call.data.startswith("chk_"))
def handle_checklist(call):
    parts = call.data.split("_")
    # Форматы: chk_finish_{user_id} или chk_{user_id}_{idx}
    if parts[1] == "finish":
        user_id = int(parts[2])
        action = "finish"
    else:
        user_id = int(parts[1])
        action = parts[2]

    if user_id != call.message.chat.id:
        bot.answer_callback_query(call.id, "Не твой чеклист")
        return
    if action == "finish":
        if user_id in active_checklists:
            checked = active_checklists[user_id]["checked_ids"]
            total = len(active_checklists[user_id]["items"])
            if len(checked) == total:
                bot.edit_message_text("✅ Всё готово! Удачи!", call.message.chat.id, call.message.message_id)
            else:
                missing = [active_checklists[user_id]["items"][i] for i in range(total) if i not in checked]
                text = "⚠️ Забыл:\n" + "\n".join(f"• {item}" for item in missing[:10])
                bot.edit_message_text(text, call.message.chat.id, call.message.message_id)
            del active_checklists[user_id]
        bot.answer_callback_query(call.id)
    else:
        idx = int(action)
        if user_id in active_checklists:
            if idx in active_checklists[user_id]["checked_ids"]:
                active_checklists[user_id]["checked_ids"].remove(idx)
            else:
                active_checklists[user_id]["checked_ids"].add(idx)
            update_checklist_keyboard(user_id)
        bot.answer_callback_query(call.id)

thread = threading.Thread(target=check_scheduler, daemon=True)
thread.start()

# Сбрасываем вебхук, если был установлен ранее
try:
    bot.remove_webhook()
    print("✅ Вебхук сброшен")
except Exception as e:
    print(f"⚠️ Не удалось сбросить вебхук: {e}")

bot.set_my_commands([
    telebot.types.BotCommand("start", "Главное меню"),
    telebot.types.BotCommand("setup", "🔧 Настроить бот заново (мастер)"),
    telebot.types.BotCommand("timezone", "Выбрать часовой пояс"),
    telebot.types.BotCommand("weather", "Погода — /weather Москва"),
    telebot.types.BotCommand("setwork", "Время работы и дорога — /setwork 09:00 40"),
    telebot.types.BotCommand("checklist", "Показать чеклист сейчас"),
    telebot.types.BotCommand("daily", "Чеклист каждый день — /daily 07:30"),
    telebot.types.BotCommand("nodaily", "Отключить ежедневный чеклист"),
    telebot.types.BotCommand("plan", "Разовый план — /plan 08:30"),
    telebot.types.BotCommand("snooze", "Отложить ближайший план — /snooze 10"),
    telebot.types.BotCommand("remind", "Напомнить за X мин — /remind 10"),
    telebot.types.BotCommand("check", "Что не сделано"),
    telebot.types.BotCommand("plans", "Все планы"),
    telebot.types.BotCommand("del", "Удалить план — /del 08:30"),
    telebot.types.BotCommand("setlist", "Свой список"),
    telebot.types.BotCommand("status", "Мои настройки"),
    telebot.types.BotCommand("reset", "Сбросить все настройки"),
])

print("✅ Бот запущен!")
while True:
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        print(f"⚠️ Ошибка polling: {e}. Перезапуск через 5 сек...")
        time.sleep(5)
