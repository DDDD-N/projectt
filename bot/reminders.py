import sqlite3
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import CallbackContext
from datetime import datetime
import logging

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.WARNING)
logger = logging.getLogger(__name__)

# Определяем get_main_menu
def get_main_menu():
    keyboard = [
        ["💸 Транзакция", "🔄 Перевод", "🏦 Счёт"],
        ["⏰ Напоминание", "📤 Экспорт", "💳 Кредит"],
        ["💰 Погашение", "📋 Кредиты", "🎯 Бюджет"],
        ["📊 Просмотр", "🔐 Пин-код", "🗑 Удалить"],
        ["⏳ Кредит. напоминание", "⚠️ Порог баланса"],
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="✨ Выберите действие ✨"
    )

# 📌 Инициализация таблицы напоминаний
def init_reminders_db(db_path):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                date TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        conn.commit()
        conn.close()
        logger.info("Таблица напоминаний инициализирована.")
    except Exception as e:
        logger.error(f"Ошибка при инициализации таблицы напоминаний: {e}")

# 📌 Добавление напоминания
def add_reminder(db_path, user_id, text, reminder_date):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO reminders (user_id, text, date)
            VALUES (?, ?, ?)
        """, (user_id, text, reminder_date))
        
        conn.commit()
        conn.close()
        logger.info(f"Напоминание добавлено для user_id {user_id}: {text} на {reminder_date}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при добавлении напоминания: {e}")
        return False

# 📌 Получение всех напоминаний пользователя
def get_reminders(db_path, user_id):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, text, date FROM reminders WHERE user_id = ?", (user_id,))
        reminders = cursor.fetchall()
        
        conn.close()
        return reminders
    except Exception as e:
        logger.error(f"Ошибка при получении напоминаний: {e}")
        return []

# 📌 Удаление напоминания по ID
def delete_reminder(db_path, reminder_id):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()
        conn.close()
        logger.info(f"Напоминание с id {reminder_id} удалено.")
        return True
    except Exception as e:
        logger.error(f"Ошибка при удалении напоминания: {e}")
        return False

# 📌 Callback для отправки напоминания
async def send_reminder(context: CallbackContext):
    job = context.job
    user_id = job.data["user_id"]
    reminder_id = job.data["reminder_id"]
    text = job.data["text"]
    db_path = job.data["db_path"]
    
    await context.bot.send_message(chat_id=user_id, text=f"Напоминание: {text}")
    delete_reminder(db_path, reminder_id)  # Удаляем напоминание после отправки

# 📌 Команда /reminder
async def reminder(update: Update, context: CallbackContext):
    try:
        user_id = update.effective_user.id
        args = context.args

        if args:  # Если есть аргументы (например, из echo)
            if len(args) < 3:
                await update.message.reply_text(
                    "Введите напоминание и дату в формате: Текст напоминания YYYY-MM-DD HH:MM\n"
                    "Пример: Оплатить интернет 2025-03-15 10:00",
                    reply_markup=get_main_menu()
                )
                return

            # Собираем текст напоминания и дату
            text = " ".join(args[:-2])
            date_str = " ".join(args[-2:])

            # Парсим дату
            try:
                reminder_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
                if reminder_date < datetime.now():
                    await update.message.reply_text("Дата напоминания должна быть в будущем.")
                    return
            except ValueError:
                await update.message.reply_text("Неверный формат даты. Используйте YYYY-MM-DD HH:MM.")
                return

            # Получаем db_path из context.bot_data
            db_path = context.bot_data.get("db_path")
            if not db_path:
                await update.message.reply_text("Ошибка: база данных не инициализирована.")
                return

            # Добавляем напоминание в базу
            if add_reminder(db_path, user_id, text, reminder_date.strftime("%Y-%m-%d %H:%M:%S")):
                # Вычисляем время до напоминания в секундах
                time_diff = (reminder_date - datetime.now()).total_seconds()

                # Добавляем задачу в JobQueue
                context.job_queue.run_once(
                    send_reminder,
                    time_diff,
                    data={"user_id": user_id, "reminder_id": sqlite3.connect(db_path).execute("SELECT last_insert_rowid()").fetchone()[0], "text": text, "db_path": db_path}
                )
                await update.message.reply_text(f"Напоминание установлено: '{text}' на {date_str}", reply_markup=get_main_menu())
            else:
                await update.message.reply_text("Ошибка при установке напоминания.")
        else:
# Если команда вызвана без аргументов, инициируем процесс ввода
            context.user_data["state"] = "WAITING_FOR_REMINDER"
            await update.message.reply_text(
                "Введите напоминание и дату в формате: Текст напоминания YYYY-MM-DD HH:MM\n"
                "Пример: Оплатить интернет 2025-03-15 10:00",
                reply_markup=get_main_menu()
            )
    except Exception as e:
        logger.error(f"Ошибка в обработчике reminder: {e}")
        await update.message.reply_text("Произошла ошибка.", reply_markup=get_main_menu())