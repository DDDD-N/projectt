import sqlite3
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import CallbackContext
import logging

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

db_path = None

def set_db_path(path):
    """Установка пути к базе данных."""
    global db_path
    db_path = path
    init_credit_reminders_db()

def init_credit_reminders_db():
    """Инициализация структуры базы данных для напоминаний по кредитам."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("PRAGMA table_info(credits)")
        columns = [col[1] for col in cursor.fetchall()]
        if "payment_day" not in columns:
            cursor.execute("ALTER TABLE credits ADD COLUMN payment_day INTEGER DEFAULT NULL")
            logger.info("Добавлен столбец 'payment_day' в таблицу credits.")
        
        conn.commit()
        conn.close()
        logger.info("Таблица credits готова для напоминаний.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при инициализации credit_reminders: {e}")

async def check_credit_reminders(context: CallbackContext):
    """Проверка сроков платежей по кредитам и отправка напоминаний в день платежа."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, id, bank_name, monthly_payment, term, remaining_amount, payment_day 
            FROM credits 
            WHERE remaining_amount > 0
        """)
        credits = cursor.fetchall()
        conn.close()

        today = datetime.now()
        for user_id, credit_id, bank_name, monthly_payment, term, remaining_amount, payment_day in credits:
            try:
                if today.day == payment_day and remaining_amount > 0:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"📅 Сегодня ({today.day}.{today.month:02d}.{today.year}) "
                             f"день платежа по кредиту в {bank_name} (ID: {credit_id}).\n"
                             f"Сумма: {monthly_payment:.2f} руб.\n"
                             f"Остаток: {remaining_amount:.2f} руб.\n"
                             f"Оплатите с помощью /paycredit"
                    )
                    logger.info(f"Отправлено напоминание пользователю {user_id} о кредите {credit_id} в {bank_name}")
            except Exception as e:
                logger.error(f"Ошибка при обработке кредита {credit_id} для user_id {user_id}: {e}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при проверке кредитов: {e}")

def setup_reminders(app):
    """Настройка периодической проверки напоминаний."""
    app.job_queue.run_repeating(check_credit_reminders, interval=86400, first=10)  # Проверка раз в сутки
    logger.info("Напоминания о кредитах настроены с интервалом 24 часа.")

async def setcreditreminder(update: Update, context: CallbackContext):
    """Команда для изменения дня платежа (опционально)."""
    user_id = update.effective_user.id
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, bank_name, remaining_amount, payment_day 
        FROM credits 
        WHERE user_id = ?
    """, (user_id,))
    credits = cursor.fetchall()
    conn.close()

    if not credits:
        await update.message.reply_text("У вас нет активных кредитов.")
        return

    credit_options = [f"{bank} (ID: {id}) - Остаток: {remaining:.2f} руб., День платежа: {day} число" 
                      for id, bank, remaining, day in credits]
    await update.message.reply_text(
        "Выберите кредит для изменения дня платежа:",
        reply_markup=ReplyKeyboardMarkup([[option] for option in credit_options], one_time_keyboard=True)
    )
    context.user_data["setcreditreminder_step"] = "select_credit"

async def handle_setcreditreminder_steps(update: Update, context: CallbackContext):
    """Обработка шагов для команды /setcreditreminder."""
    user_id = update.effective_user.id
    step = context.user_data.get("setcreditreminder_step")
    message_text = update.message.text.strip()

    if step == "select_credit":
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, bank_name, remaining_amount, payment_day 
            FROM credits 
            WHERE user_id = ?
        """, (user_id,))
        credits = cursor.fetchall()
        conn.close()

        credit_dict = {f"{bank} (ID: {id}) - Остаток: {remaining:.2f} руб., День платежа: {day} число": id 
                       for id, bank, remaining, day in credits}
        
        if message_text not in credit_dict:
            await update.message.reply_text("Выберите кредит из списка.")
            return
        
        context.user_data["credit_id"] = credit_dict[message_text]
        context.user_data["setcreditreminder_step"] = "enter_day"
        await update.message.reply_text("Введите новый день месяца для платежа (1-31):")

    elif step == "enter_day":
        day = is_valid_number(message_text)
        if day is None or day < 1 or day > 31 or not day.is_integer():
            await update.message.reply_text("Введите целое число от 1 до 31.")
            return

        credit_id = context.user_data["credit_id"]
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE credits 
                SET payment_day = ? 
                WHERE user_id = ? AND id = ?
            """, (int(day), user_id, credit_id))
            conn.commit()
            conn.close()
            await update.message.reply_text(
                f"День платежа установлен: {int(day)} число каждого месяца.",
                reply_markup=ReplyKeyboardRemove()
            )
        except sqlite3.Error as e:
            await update.message.reply_text(f"Ошибка при установке дня платежа: {e}")

        context.user_data.pop("credit_id", None)
        context.user_data.pop("setcreditreminder_step", None)

def is_valid_number(text):
    """Проверка, является ли текст числом."""
    try:
        return float(text)
    except ValueError:
        return None