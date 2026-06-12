import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import threading
import time
import json
import os
from datetime import datetime
import pytz

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
else:
    user_tasks = {}
    user_checklists = {}
    user_remind_before = {}
    user_daily_time = {}
    user_timezone = {}

daily_sent_today = {}
pending_daily = {}  # user_id -> True, ждём время для /daily
pending_plan = {}   # user_id -> True, ждём время для /plan

def save_all():
    with open(DATA_FILE, "w") as f:
        json.dump({
            "tasks": user_tasks,
            "checklists": user_checklists,
            "remind_before": user_remind_before,
            "daily_time": user_daily_time,
            "timezone": user_timezone
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

def send_checklist(user_id, items, is_reminder=False):
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

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "✅ Бот-будильник с чеклистом\n\n"
                         "/timezone — выбрать часовой пояс\n"
                         "/checklist — показать чеклист сейчас\n"
                         "/daily 07:30 — чеклист каждый день в это время\n"
                         "/nodaily — отключить ежедневный чеклист\n"
                         "/plan 08:30 — разовый план\n"
                         "/snooze 10 — отложить ближайший план на N мин\n"
                         "/setlist — свой список\n"
                         "/remind 10 — напомнить за X мин\n"
                         "/check — что не сделано\n"
                         "/plans — все планы\n"
                         "/del 08:30 — удалить план\n"
                         "/status — мои настройки")

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
    bot.edit_message_text(
        f"✅ Часовой пояс установлен: {label} ({utc})\n\nТеперь /daily и /plan работают по твоему времени.",
        call.message.chat.id,
        call.message.message_id
    )
    bot.answer_callback_query(call.id)

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
    telebot.types.BotCommand("timezone", "Выбрать часовой пояс"),
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
