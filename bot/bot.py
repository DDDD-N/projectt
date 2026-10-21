import sqlite3
import os
import requests
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, CallbackContext
import telegram
import matplotlib.pyplot as plt
import pandas as pd
from transactions import init_transactions_db, addtransaction, get_transactions as transactions, fetch_user_data as trans_fetch_user_data, set_db_path as trans_set_db_path, save_user_data, save_transaction, get_user_banks
from reminders import init_reminders_db, reminder
from transfers import set_db_path as transfer_set_db_path, transfer
from accounts import init_accounts_db, addaccount, set_db_path as acc_set_db_path, save_account, get_accounts, ACCOUNT_TYPES
from credits import init_credits_db, addcredit, paycredit, credits, set_db_path as credit_set_db_path
from budgets import set_db_path as budget_set_db_path, setbudget, budget, update_budget
from credit_reminders import set_db_path as reminders_set_db_path, setup_reminders, setcreditreminder
from balance_alerts import set_db_path as alerts_set_db_path, setup_balance_alerts, setthreshold
import asyncio
import httpx
import logging

# Настройка логирования (INFO для отладки)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

from dotenv import load_dotenv
load_dotenv()
TOKEN = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
BANKS = ["Сбербанк", "ВТБ", "Альфа-Банк", "Тинькофф", "Газпромбанк", "Промсвязьбанк", "Другой банк"]
CURRENCIES = ["USD", "EUR", "CNY", "GBP"]

user_banks = {}
db_path = None

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

def get_inline_menu():
    keyboard = [
        [
            InlineKeyboardButton("👀 Баланс", callback_data="view"),
            InlineKeyboardButton("📊 Итог", callback_data="total"),
        ],
        [
            InlineKeyboardButton("📈 График", callback_data="chart"),
            InlineKeyboardButton("💸 Конвертер", callback_data="convert"),
            InlineKeyboardButton("📜 История", callback_data="transactions"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def is_valid_number(text):
    try:
        return float(text)
    except ValueError:
        return None

def init_db(app):
    global db_path
    db_path = os.path.abspath(os.getenv("DB_PATH", "users.db"))
    db_directory = os.path.dirname(db_path)

    os.makedirs(db_directory, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            username TEXT,
            banks TEXT,
            balances TEXT,
            message TEXT,
            pin TEXT,
            balance_threshold REAL DEFAULT 1000.0
        )
    """)

    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    if "balance_threshold" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN balance_threshold REAL DEFAULT 1000.0")

    conn.commit()
    conn.close()
    
    trans_set_db_path(db_path)
    transfer_set_db_path(db_path)
    acc_set_db_path(db_path)
    credit_set_db_path(db_path)
    budget_set_db_path(db_path)
    reminders_set_db_path(db_path)
    alerts_set_db_path(db_path)
    init_transactions_db()
    init_reminders_db(db_path)
    init_accounts_db()
    init_credits_db()
    setup_reminders(app)
    setup_balance_alerts(app)
    app.bot_data["db_path"] = db_path

async def get_exchange_rates():
    url = "https://api.exchangerate-api.com/v4/latest/RUB"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            return data["rates"] if "rates" in data else None
        except (httpx.RequestError, httpx.TimeoutException) as e:
            logger.error(f"Ошибка получения курса валют: {e}")
            return None

def set_pin(user_id: int, pin: str, db_path: str) -> bool:
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("PRAGMA table_info(users);")
        columns = [column[1] for column in cursor.fetchall()]
        if "pin" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN pin TEXT")

        cursor.execute("""
            INSERT INTO users (user_id, pin) 
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET pin = excluded.pin
        """, (user_id, pin))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка при установке PIN-кода: {e}")
        return False

def check_pin(user_id: int, pin: str, db_path: str) -> bool:
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT pin FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()

        if result and result[0]:
            return result[0] == pin
        return True  # Если PIN-код не установлен, доступ открыт
    except Exception as e:
        logger.error(f"Ошибка при проверке PIN-кода: {e}")
        return False

def delete_account(user_id: int) -> bool:
    global db_path
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM transactions WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM reminders WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM accounts WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM credits WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM budgets WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка в delete_account для user_id={user_id}: {e}")
        return False

def export_data_to_excel(user_id):
    global db_path
    try:
        conn = sqlite3.connect(db_path)

        banks_str, balances_dict, _ = trans_fetch_user_data(user_id)
        query_transactions = "SELECT * FROM transactions WHERE user_id = ?"
        df_transactions = pd.read_sql_query(query_transactions, conn, params=(user_id,))
        query_accounts = "SELECT bank_name, account_type, balance FROM accounts WHERE user_id = ?"
        df_accounts = pd.read_sql_query(query_accounts, conn, params=(user_id,))
        query_budgets = "SELECT category, amount FROM budgets WHERE user_id = ?"
        df_budgets = pd.read_sql_query(query_budgets, conn, params=(user_id,))
        conn.close()

        if (not banks_str or not balances_dict) and df_transactions.empty and df_accounts.empty and df_budgets.empty:
            return None

        if banks_str and balances_dict:
            banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()]
            balances_list = [float(balances_dict.get(bank, 0)) for bank in banks_list]
            df_users = pd.DataFrame({"Банк": banks_list, "Основной счёт (руб.)": balances_list})
        else:
            df_users = pd.DataFrame(columns=["Банк", "Основной счёт (руб.)"])

        excel_file = f"user_data_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        with pd.ExcelWriter(excel_file) as writer:
            df_users.to_excel(writer, sheet_name="Основные счета", index=False)
            if not df_accounts.empty:
                df_accounts.to_excel(writer, sheet_name="Дополнительные счета", index=False)
            if not df_transactions.empty:
                df_transactions.to_excel(writer, sheet_name="Транзакции", index=False)
            if not df_budgets.empty:
                df_budgets.to_excel(writer, sheet_name="Бюджеты", index=False)
        
        return excel_file
    except Exception as e:
        logger.error(f"Ошибка в export_data_to_excel для user_id={user_id}: {e}")
        return None

async def require_pin(update: Update, context: CallbackContext, callback):
    user_id = update.effective_user.id
    banks_str, _, stored_pin = trans_fetch_user_data(user_id)
    logger.info(f"require_pin: user_id={user_id}, stored_pin={bool(stored_pin)}, state={context.user_data.get('state')}, pending_callback={context.user_data.get('pending_callback')}")

    if stored_pin:
        if context.user_data.get("state") != "WAITING_PIN":
            try:
                if update.message:
                    await update.message.reply_text("Введите ваш PIN-код для доступа к данным:", reply_markup=get_main_menu())
                elif update.callback_query:
                    await update.callback_query.message.reply_text("Введите ваш PIN-код для доступа к данным:", reply_markup=get_inline_menu())
                    context.user_data["original_update"] = update
                context.user_data["state"] = "WAITING_PIN"
                context.user_data["pending_callback"] = callback
                logger.info(f"require_pin: Запрошен PIN-код, установлен state=WAITING_PIN для user_id={user_id}")
                return
            except Exception as e:
                logger.error(f"Ошибка при запросе PIN для user_id={user_id}: {e}")
                await update.message.reply_text("Ошибка при запросе PIN-кода.", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
                return
        else:
            pin_input = update.message.text.strip()
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
                if check_pin(user_id, pin_input, db_path):
                    context.user_data.pop("state", None)
                    context.user_data.pop("pending_callback", None)
                    context.user_data.pop("original_update", None)
                    context.user_data.pop("pin_verified", None)
                    logger.info(f"require_pin: PIN-код верный, выполняем callback для user_id={user_id}")
                    await update.message.reply_text("PIN-код принят, продолжаем...", reply_markup=get_main_menu())
                    await callback(update, context)
                else:
                    await update.message.reply_text("Неверный PIN-код. Попробуйте снова:", reply_markup=get_main_menu())
                    await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
                    logger.warning(f"require_pin: Неверный PIN-код для user_id={user_id}")
            except telegram.error.TimedOut:
                await update.message.reply_text("Ошибка: Не удалось обработать PIN-код.", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
                logger.error(f"require_pin: Тайм-аут при обработке PIN-кода для user_id={user_id}")
            except Exception as e:
                logger.error(f"Ошибка при проверке PIN для user_id={user_id}: {e}")
                await update.message.reply_text("Ошибка при проверке PIN-кода.", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            return
    else:
        logger.info(f"require_pin: PIN-код не установлен, выполняем callback для user_id={user_id}")
        await callback(update, context)

async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    user_id = user.id

    try:
        if update.message:
            await update.message.reply_text(
                f"Привет, {user.first_name}! Я ваш финансовый помощник.\n"
                "Начнем с выбора банков. Нажмите на название банка ниже, затем 'готово', когда выберете все:",
                reply_markup=get_main_menu()
            )
            await update.message.reply_text(
                "Или используйте интерактивное меню:",
                reply_markup=get_inline_menu()
            )
            context.user_data["state"] = "WAITING_FOR_BANKS"
            user_banks[user_id] = []
            await update.message.reply_text(
                "Выберите банки:",
                reply_markup=ReplyKeyboardMarkup([[bank] for bank in BANKS], resize_keyboard=True, one_time_keyboard=False)
            )
    except telegram.error.TimedOut:
        if update.message:
            await update.message.reply_text("Ошибка: Не удалось начать работу. Проверьте интернет-соединение.", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
    except Exception as e:
        logger.error(f"Ошибка в start для user_id={user_id}: {e}")

async def menu(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    try:
        if update.message:
            await update.message.reply_text("✨ Выберите действие:\n-------------------", reply_markup=get_main_menu())
            await update.message.reply_text("Или используйте интерактивное меню:", reply_markup=get_inline_menu())
        elif update.callback_query:
            await update.callback_query.message.reply_text("✨ Выберите действие:\n-------------------", reply_markup=get_main_menu())
            await update.callback_query.message.reply_text("Или используйте интерактивное меню:", reply_markup=get_inline_menu())
    except Exception as e:
        logger.error(f"Ошибка в menu для user_id={user_id}: {e}")

async def view(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    banks_str, balances_dict, _ = trans_fetch_user_data(user_id)

    if not banks_str or not balances_dict:
        message = "У вас еще нет сохраненных данных. Начните с /start."
    else:
        banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()]
        message = "💼 **Ваши финансы:**\n\n"
        for bank in banks_list:
            balance = balances_dict.get(bank, "Не указано")
            formatted_balance = "{:,.0f}".format(float(balance)) if balance != "Не указано" else balance
            message += f"🏦 **{bank}**\n"
            message += f"💰 Основной счёт: {formatted_balance} руб.\n"
            accounts = get_accounts(user_id, bank)
            for account_type, acc_balance in accounts:
                formatted_acc_balance = "{:,.0f}".format(float(acc_balance))
                if account_type == "Сберегательный счёт":
                    message += f"🏪 {account_type}: {formatted_acc_balance} руб.\n"
                elif account_type == "Текущий счёт":
                    message += f"📈 {account_type}: {formatted_acc_balance} руб.\n"
                elif account_type == "Накопительный вклад":
                    message += f"💎 {account_type}: {formatted_acc_balance} руб.\n"
                else:
                    message += f"➡️ {account_type}: {formatted_acc_balance} руб.\n"
            message += "\n"

    try:
        if update.message:
            await update.message.reply_text(message, reply_markup=get_main_menu(), parse_mode="Markdown")
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        elif update.callback_query:
            await update.callback_query.message.reply_text(message, reply_markup=get_inline_menu(), parse_mode="Markdown")
    except telegram.error.TimedOut:
        if update.message:
            await update.message.reply_text("Ошибка: Не удалось показать данные.", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        elif update.callback_query:
            await update.callback_query.message.reply_text("Ошибка: Не удалось показать данные.", reply_markup=get_inline_menu())
    except Exception as e:
        logger.error(f"Ошибка в view для user_id={user_id}: {e}")

async def total(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    banks_str, balances_dict, _ = trans_fetch_user_data(user_id)
    accounts = get_accounts(user_id)

    if not balances_dict and not accounts:
        message = "У вас еще нет данных о балансах."
    else:
        total_main = sum(float(str(balance).replace(",", "")) for balance in balances_dict.values())
        total_accounts = sum(float(balance) for _, balance in accounts)
        total_amount = total_main + total_accounts
        message = f"Общее количество средств: {total_amount:,.2f} руб.\n"
        message += f"Основные счета: {total_main:,.2f} руб.\n"
        message += f"Дополнительные счета: {total_accounts:,.2f} руб."

    try:
        if update.message:
            await update.message.reply_text(message, reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        elif update.callback_query:
            await update.callback_query.message.reply_text(message, reply_markup=get_inline_menu())
    except telegram.error.TimedOut:
        if update.message:
            await update.message.reply_text("Ошибка: Не удалось показать итог.", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        elif update.callback_query:
            await update.callback_query.message.reply_text("Ошибка: Не удалось показать итог.", reply_markup=get_inline_menu())
    except Exception as e:
        logger.error(f"Ошибка в total для user_id={user_id}: {e}")

async def chart(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    banks_str, balances_dict, _ = trans_fetch_user_data(user_id)
    accounts = get_accounts(user_id)

    labels = []
    balances = []

    if banks_str and balances_dict:
        banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()]
        for bank in banks_list:
            balance = float(balances_dict.get(bank, 0))
            if balance > 0:
                labels.append(f"{bank} (Основной)")
                balances.append(balance)

    for account_type, balance in accounts:
        bank_name = [acc[0] for acc in get_accounts(user_id) if acc[1] == balance][0]
        balance = float(balance)
        if balance > 0:
            labels.append(f"{bank_name} ({account_type})")
            balances.append(balance)

    if not balances:
        message = "Все балансы равны нулю или отрицательны."
        try:
            if update.message:
                await update.message.reply_text(message, reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            elif update.callback_query:
                await update.callback_query.message.reply_text(message, reply_markup=get_inline_menu())
            return
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения в chart для user_id={user_id}: {e}")
        return

    plt.figure(figsize=(8, 6))
    plt.pie(balances, labels=labels, autopct='%1.1f%%', startangle=90)
    plt.title("Распределение средств по всем счетам")
    plt.axis('equal')

    chart_file = f"chart_{user_id}.png"
    plt.savefig(chart_file)
    plt.close()

    try:
        with open(chart_file, "rb") as file:
            if update.message:
                await update.message.reply_photo(photo=file, caption="Распределение средств по всем счетам", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            elif update.callback_query:
                await update.callback_query.message.reply_photo(photo=file, caption="Распределение средств по всем счетам", reply_markup=get_inline_menu())
        os.remove(chart_file)
    except telegram.error.TimedOut:
        if update.message:
            await update.message.reply_text("Ошибка: Не удалось отправить график.", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        elif update.callback_query:
            await update.callback_query.message.reply_text("Ошибка: Не удалось отправить график.", reply_markup=get_inline_menu())
    except Exception as e:
        logger.error(f"Ошибка в chart для user_id={user_id}: {e}")

async def convert(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    banks_str, balances_dict, _ = trans_fetch_user_data(user_id)

    if not banks_str or not balances_dict:
        message = "У вас еще нет сохраненных данных. Начните с /start."
        try:
            if update.message:
                await update.message.reply_text(message, reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            elif update.callback_query:
                await update.callback_query.message.reply_text(message, reply_markup=get_inline_menu())
            return
        except Exception as e:
            logger.error(f"Ошибка в convert для user_id={user_id}: {e}")
        return

    banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()]
    if not banks_list:
        message = "У вас нет банков для конвертации."
        try:
            if update.message:
                await update.message.reply_text(message, reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            elif update.callback_query:
                await update.callback_query.message.reply_text(message, reply_markup=get_inline_menu())
            return
        except Exception as e:
            logger.error(f"Ошибка в convert для user_id={user_id}: {e}")
        return

    try:
        keyboard = [
            [InlineKeyboardButton(bank, callback_data=f"convert_bank_{bank}")] 
            for bank in banks_list 
            if bank and isinstance(bank, str) and len(bank) <= 100
        ]
        if not keyboard:
            raise ValueError("Нет валидных банков для создания кнопок.")
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.message:
            await update.message.reply_text("Выберите банк для конвертации:", reply_markup=reply_markup)
        elif update.callback_query:
            await update.callback_query.message.reply_text("Выберите банк для конвертации:", reply_markup=reply_markup)
        context.user_data["convert_step"] = "select_bank"
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения в convert для user_id={user_id}: {e}")
        if update.message:
            await update.message.reply_text("Произошла ошибка при выборе банка.", reply_markup=get_inline_menu())
        elif update.callback_query:
            await update.callback_query.message.reply_text("Произошла ошибка при выборе банка.", reply_markup=get_inline_menu())
        context.user_data.clear()
        return

async def show_transactions(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    trans_list = transactions(user_id)

    target_date = datetime.now().strftime("%Y-%m-%d")
    filtered_trans = [t for t in trans_list if t[5].startswith(target_date)]

    if not filtered_trans:
        message = f"📅 Транзакции за {target_date}:\n\n"
        message += "📈 Доходы:\nНет доходов.\n\n"
        message += "📉 Расходы:\nНет расходов.\n\n"
        message += "🔄 Переводы:\nНет переводов."
    else:
        message = f"📅 Транзакции за {target_date}:\n\n"
        incomes = [t for t in filtered_trans if t[2] == "доход"]
        expenses = [t for t in filtered_trans if t[2] == "вычет"]
        transfers = [t for t in filtered_trans if t[2] == "перевод"]

        message += "📈 Доходы:\n"
        if not incomes:
            message += "Нет доходов.\n\n"
        else:
            for amount, category, _, bank_name, account_type, date in incomes:
                message += f"{float(amount):.2f} руб. | {category} | {bank_name} ({account_type}) | {date}\n"
            message += "\n"

        message += "📉 Расходы:\n"
        if not expenses:
            message += "Нет расходов.\n\n"
        else:
            for amount, category, _, bank_name, account_type, date in expenses:
                message += f"{float(amount):.2f} руб. | {category} | {bank_name} ({account_type}) | {date}\n"
            message += "\n"

        message += "🔄 Переводы:\n"
        if not transfers:
            message += "Нет переводов."
        else:
            for amount, category, _, _, _, date in transfers:
                message += f"{float(amount):.2f} руб. | {category} | {date}\n"

    try:
        if update.message:
            await update.message.reply_text(message, reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        elif update.callback_query:
            await update.callback_query.message.reply_text(message, reply_markup=get_inline_menu())
    except telegram.error.TimedOut:
        if update.message:
            await update.message.reply_text("Ошибка: Не удалось показать транзакции.", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        elif update.callback_query:
            await update.callback_query.message.reply_text("Ошибка: Не удалось показать транзакции.", reply_markup=get_inline_menu())
    except Exception as e:
        logger.error(f"Ошибка в show_transactions для user_id={user_id}: {e}")

async def export(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    if not db_path:
        message = "Произошла ошибка."
    else:
        excel_file = export_data_to_excel(user_id)
        if not excel_file:
            message = "У вас еще нет данных для экспорта."
        else:
            try:
                await update.message.reply_text("Экспортирую ваши данные...", reply_markup=get_main_menu())
                with open(excel_file, "rb") as file:
                    await update.message.reply_document(document=file, filename=os.path.basename(excel_file))
                os.remove(excel_file)
                await update.message.reply_text("Данные экспортированы.", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
                return
            except telegram.error.TimedOut:
                message = "Ошибка: Не удалось отправить файл."
            except Exception as e:
                logger.error(f"Ошибка в export для user_id={user_id}: {e}")
                message = "Ошибка при экспорте данных."

    await update.message.reply_text(message, reply_markup=get_main_menu())
    await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())

async def setpin(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    context.user_data["state"] = "WAITING_FOR_PIN"
    try:
        await update.message.reply_text("Введите новый PIN-код (4 цифры):", reply_markup=get_main_menu())
        await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
    except telegram.error.TimedOut:
        await update.message.reply_text("Ошибка: Не удалось запросить PIN-код.", reply_markup=get_main_menu())
        await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
    except Exception as e:
        logger.error(f"Ошибка в setpin для user_id={user_id}: {e}")

async def deleteaccount(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    if delete_account(user_id):
        user_banks.pop(user_id, None)
        context.user_data.pop("state", None)
        message = "Ваш аккаунт и все данные успешно удалены."
    else:
        message = "Ошибка при удалении аккаунта."

    try:
        await update.message.reply_text(message, reply_markup=get_main_menu())
        await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
    except telegram.error.TimedOut:
        await update.message.reply_text("Ошибка: Не удалось подтвердить удаление.", reply_markup=get_main_menu())
        await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
    except Exception as e:
        logger.error(f"Ошибка в deleteaccount для user_id={user_id}: {e}")

async def addtransaction(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    banks = get_user_banks(user_id)
    
    if not banks:
        await update.message.reply_text("У вас нет сохранённых банков. Используйте /start для добавления.", reply_markup=get_main_menu())
        await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        return
    
    await update.message.reply_text(
        "Выберите тип транзакции:",
        reply_markup=ReplyKeyboardMarkup([["Доход", "Вычет"]], one_time_keyboard=True)
    )
    context.user_data["addtransaction_step"] = "select_type"

async def reset_state(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    context.user_data.clear()
    try:
        await update.message.reply_text("Состояние бота сброшено. Вы можете начать заново.", reply_markup=get_main_menu())
        await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        logger.info(f"reset_state: Состояние сброшено для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка в reset_state для user_id={user_id}: {e}")
        await update.message.reply_text("Ошибка при сбросе состояния.", reply_markup=get_main_menu())
        await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())

async def reset_pin_state(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    context.user_data.pop("state", None)
    context.user_data.pop("pending_callback", None)
    context.user_data.pop("original_update", None)
    context.user_data.pop("pin_verified", None)
    try:
        await update.message.reply_text("Состояние PIN-кода сброшено. Попробуйте выполнить команду снова.", reply_markup=get_main_menu())
        await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        logger.info(f"reset_pin_state: Состояние PIN-кода сброшено для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка в reset_pin_state для user_id={user_id}: {e}")
        await update.message.reply_text("Ошибка при сбросе состояния PIN-кода.", reply_markup=get_main_menu())
        await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())

async def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    command = query.data
    command_map = {
        "view": view,
        "total": total,
        "chart": chart,
        "convert": convert,
        "transactions": show_transactions,
    }

    if command in command_map:
        try:
            await require_pin(update, context, command_map[command])
        except Exception as e:
            logger.error(f"Ошибка при вызове команды {command} для user_id={user_id}: {e}")
            await query.message.reply_text("Произошла ошибка при выполнении команды.", reply_markup=get_inline_menu())
    elif command.startswith("convert_bank_"):
        selected_bank = command[len("convert_bank_"):]
        banks_str, balances_dict, _ = trans_fetch_user_data(user_id)
        banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()]

        if selected_bank not in banks_list:
            await query.message.reply_text("Банк не найден.", reply_markup=get_inline_menu())
            return

        context.user_data["convert_bank"] = selected_bank
        accounts = get_accounts(user_id, selected_bank)
        account_options = ["Основной счёт"] + [acc_type for acc_type, _ in accounts]
        keyboard = [
            [InlineKeyboardButton(acc, callback_data=f"convert_acc_{acc}")] for acc in account_options
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Выберите счёт для конвертации:", reply_markup=reply_markup)
        context.user_data["convert_step"] = "select_account"
    elif command.startswith("convert_acc_"):
        selected_account = command[len("convert_acc_"):]
        selected_bank = context.user_data.get("convert_bank")
        banks_str, balances_dict, _ = trans_fetch_user_data(user_id)
        banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()]

        if not selected_bank or selected_bank not in banks_list:
            await query.message.reply_text("Ошибка: банк не выбран или не найден.", reply_markup=get_inline_menu())
            return

        accounts = get_accounts(user_id, selected_bank)
        account_options = ["Основной счёт"] + [acc_type for acc_type, _ in accounts]

        if selected_account not in account_options:
            await query.message.reply_text("Счёт не найден.", reply_markup=get_inline_menu())
            return

        context.user_data["convert_account"] = selected_account
        keyboard = [
            [InlineKeyboardButton(currency, callback_data=f"convert_currency_{currency}")] for currency in CURRENCIES
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Выберите валюту для конвертации:", reply_markup=reply_markup)
        context.user_data["convert_step"] = "select_currency"
    elif command.startswith("convert_currency_"):
        selected_currency = command[len("convert_currency_"):]
        selected_bank = context.user_data.get("convert_bank")
        selected_account = context.user_data.get("convert_account")
        banks_str, balances_dict, _ = trans_fetch_user_data(user_id)

        if not selected_bank or not selected_account:
            await query.message.reply_text("Ошибка: не выбран банк или счёт.", reply_markup=get_inline_menu())
            return

        if selected_currency not in CURRENCIES:
            await query.message.reply_text("Валюта не найдена.", reply_markup=get_inline_menu())
            return

        if selected_account == "Основной счёт":
            amount_rub = float(balances_dict.get(selected_bank, 0))
        else:
            with sqlite3.connect(db_path, timeout=10) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT balance FROM accounts 
                    WHERE user_id = ? AND bank_name = ? AND account_type = ?
                """, (user_id, selected_bank, selected_account))
                result = cursor.fetchone()
                amount_rub = float(result[0]) if result else 0.0

        rates = await get_exchange_rates()

        if not rates or selected_currency not in rates:
            message = f"Не удалось получить курс для {selected_currency}."
        else:
            rub_to_target = rates[selected_currency]
            converted_amount = amount_rub * rub_to_target
            message = (
                f"💰 Сумма в {selected_bank} ({selected_account}): {amount_rub:,.2f} RUB\n"
                f"🔄 Курс на {datetime.now().strftime('%Y-%m-%d')}: 1 RUB = {rub_to_target:.4f} {selected_currency}\n"
                f"💱 Конвертация в {selected_currency}: {converted_amount:,.2f} {selected_currency}"
            )
        await query.message.reply_text(message, reply_markup=get_inline_menu())
        context.user_data.pop("convert_bank", None)
        context.user_data.pop("convert_account", None)
        context.user_data.pop("convert_step", None)
    else:
        await query.message.reply_text("Неизвестная команда.", reply_markup=get_inline_menu())

async def echo(update: Update, context: CallbackContext):
    user = update.effective_user
    user_id = user.id
    message_text = update.message.text.strip()

    if context.user_data.get("state") == "WAITING_PIN":
        await require_pin(update, context, context.user_data.get("pending_callback"))
        return

    if context.user_data.get("state") == "WAITING_FOR_PIN":
        if not message_text.isdigit() or len(message_text) != 4:
            await update.message.reply_text("PIN-код должен состоять из 4 цифр. Попробуйте снова:", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            return
        if set_pin(user_id, message_text, db_path):
            context.user_data["state"] = None
            await update.message.reply_text("PIN-код успешно установлен.", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        else:
            await update.message.reply_text("Ошибка при установке PIN-кода.", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        return

    command_map = {
        "💸 Транзакция": addtransaction,
        "🔄 Перевод": transfer,
        "🏦 Счёт": addaccount,
        "⏰ Напоминание": reminder,
        "📤 Экспорт": export,
        "💳 Кредит": addcredit,
        "💰 Погашение": paycredit,
        "📋 Кредиты": credits,
        "🎯 Бюджет": setbudget,
        "📊 Просмотр": budget,
        "🔐 Пин-код": setpin,
        "🗑 Удалить": deleteaccount,
        "⏳ Кредит. напоминание": setcreditreminder,
        "⚠️ Порог баланса": setthreshold,
    }

    if message_text in command_map:
        try:
            await require_pin(update, context, command_map[message_text])
            return
        except Exception as e:
            logger.error(f"Ошибка при вызове команды {message_text} для user_id={user_id}: {e}")
            await update.message.reply_text("Произошла ошибка при выполнении команды.", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            return

    if context.user_data.get("state") == "WAITING_FOR_REMINDER":
        try:
            parts = message_text.rsplit(" ", 2)
            if len(parts) < 3:
                await update.message.reply_text(
                    "Введите напоминание и дату в формате: Текст напоминания YYYY-MM-DD HH:MM\n"
                    "Пример: Оплатить интернет 2025-03-15 10:00",
                    reply_markup=get_main_menu()
                )
                return
            context.args = parts
            await reminder(update, context)
            context.user_data.pop("state", None)
            return
        except Exception as e:
            logger.error(f"Ошибка при обработке напоминания для user_id={user_id}: {e}")
            await update.message.reply_text(
                "Ошибка при создании напоминания. Проверьте формат даты (YYYY-MM-DD HH:MM).",
                reply_markup=get_main_menu()
            )
            context.user_data.pop("state", None)
            return

    if not context.user_data.get("state") and not context.user_data.get("setbudget_step"):
        try:
            parts = message_text.rsplit(" ", 2)
            if len(parts) >= 3:
                date_str = f"{parts[-2]} {parts[-1]}"
                datetime.strptime(date_str, "%Y-%m-%d %H:%M")
                context.args = parts
                await reminder(update, context)
                return
        except ValueError:
            pass

    if context.user_data.get("state") == "WAITING_FOR_BANKS":
        if message_text in BANKS[:-1]:
            if message_text not in user_banks[user_id]:
                user_banks[user_id].append(message_text)
            try:
                await update.message.reply_text(
                    f"Вы выбрали: {', '.join(user_banks[user_id])}\n"
                    "Добавьте еще банк или нажмите 'готово'.",
                    reply_markup=ReplyKeyboardMarkup([[bank] for bank in BANKS], resize_keyboard=True, one_time_keyboard=False)
                )
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            except telegram.error.TimedOut:
                await update.message.reply_text("Ошибка: Не удалось отправить список банков.", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        elif message_text == "Другой банк":
            context.user_data["state"] = "WAITING_FOR_CUSTOM_BANK"
            try:
                await update.message.reply_text("Введите название банка:", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            except telegram.error.TimedOut:
                await update.message.reply_text("Ошибка: Не удалось запросить название банка.", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        elif message_text.lower() == "готово":
            if not user_banks[user_id]:
                await update.message.reply_text("Выберите хотя бы один банк.", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
                return
            context.user_data["state"] = "WAITING_FOR_BALANCE"
            context.user_data["current_bank_index"] = 0
            try:
                await update.message.reply_text(
                    f"Сколько средств на счете в {user_banks[user_id][0]}? (введите сумму числом)",
                    reply_markup=get_main_menu()
                )
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            except telegram.error.TimedOut:
                await update.message.reply_text("Ошибка: Не удалось запросить баланс.", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        else:
            await update.message.reply_text("Выберите банк из списка или 'готово'.", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
    
    elif context.user_data.get("state") == "WAITING_FOR_CUSTOM_BANK":
        custom_bank = message_text.capitalize()
        if custom_bank not in user_banks[user_id]:
            user_banks[user_id].append(custom_bank)
        try:
            await update.message.reply_text(
                f"Банк '{custom_bank}' добавлен.\nДобавьте еще или нажмите 'готово'.",
                reply_markup=ReplyKeyboardMarkup([[bank] for bank in BANKS], resize_keyboard=True, one_time_keyboard=False)
            )
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            context.user_data["state"] = "WAITING_FOR_BANKS"
        except telegram.error.TimedOut:
            await update.message.reply_text("Ошибка: Не удалось подтвердить добавление банка.", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
    
    elif context.user_data.get("state") == "WAITING_FOR_BALANCE":
        number = is_valid_number(message_text)
        if number is None:
            await update.message.reply_text("Введите сумму числом.", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            return

        banks = user_banks.get(user_id, [])
        current_index = context.user_data.get("current_bank_index", 0)

        if current_index >= len(banks):
            context.user_data["state"] = None
            context.user_data.pop("current_bank_index", None)
            save_user_data(user_id, user.username, banks=", ".join(banks), balances=context.user_data.get("balances", {}))
            await update.message.reply_text("Все данные сохранены.", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            return

        current_bank = banks[current_index]
        balances = context.user_data.setdefault("balances", {})
        balances[current_bank] = str(number)

        current_index += 1
        context.user_data["current_bank_index"] = current_index
        if current_index < len(banks):
            await update.message.reply_text(f"Сколько средств в {banks[current_index]}? (введите сумму числом)", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        else:
            context.user_data["state"] = None
            context.user_data.pop("current_bank_index", None)
            save_user_data(user_id, user.username, banks=", ".join(banks), balances=context.user_data.get("balances", {}))
            await update.message.reply_text("Все данные сохранены.", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())

    elif "setbudget_step" in context.user_data:
        from budgets import handle_setbudget_steps
        try:
            await handle_setbudget_steps(update, context)
            return
        except Exception as e:
            logger.error(f"Ошибка при обработке бюджета для user_id={user_id}: {e}")
            await update.message.reply_text("Ошибка при установке бюджета.", reply_markup=get_main_menu())
            context.user_data.clear()
            return

    elif "transfer_step" in context.user_data:
        await transfer(update, context)
    
    elif "addtransaction_step" in context.user_data:
        step = context.user_data["addtransaction_step"]
        if step == "select_type":
            if message_text.lower() not in ["доход", "вычет"]:
                await update.message.reply_text("Выберите 'Доход' или 'Вычет'.", reply_markup=ReplyKeyboardMarkup([["Доход", "Вычет"]], one_time_keyboard=True))
                return
            context.user_data["addtransaction_type"] = message_text.lower()
            context.user_data["addtransaction_step"] = "enter_amount"
            await update.message.reply_text("Введите сумму транзакции (например, 500):", reply_markup=get_main_menu())

        elif step == "enter_amount":
            try:
                amount = float(message_text)
                if amount <= 0:
                    await update.message.reply_text("Сумма должна быть положительной. Введите снова:", reply_markup=get_main_menu())
                    return
                context.user_data["addtransaction_amount"] = amount
                context.user_data["addtransaction_step"] = "enter_category"
                await update.message.reply_text("Введите категорию (например, Зарплата или Еда):", reply_markup=get_main_menu())
            except ValueError:
                await update.message.reply_text("Сумма должна быть числом. Введите снова:", reply_markup=get_main_menu())

        elif step == "enter_category":
            context.user_data["addtransaction_category"] = message_text.capitalize()
            banks = get_user_banks(user_id)
            bank_keyboard = [[f"🏦 {bank}" for bank in banks[i:i+2]] for i in range(0, len(banks), 2)]
            await update.message.reply_text(
                "Выберите банк:",
                reply_markup=ReplyKeyboardMarkup(bank_keyboard, one_time_keyboard=True)
            )
            context.user_data["addtransaction_step"] = "select_bank"

        elif step == "select_bank":
            selected_bank = message_text.replace("🏦 ", "").strip()
            banks_str, _, _ = trans_fetch_user_data(user_id)
            banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()]
            if selected_bank not in banks_list:
                await update.message.reply_text("Выберите банк из списка.", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
                return
            context.user_data["addtransaction_bank"] = selected_bank
            accounts = get_accounts(user_id, selected_bank)
            account_options = [("💰 Основной счёт", "Основной счёт")] + [(f"🏪 {acc_type}", acc_type) for acc_type, _ in accounts]
            account_keyboard = [[acc_display for acc_display, _ in account_options[i:i+2]] for i in range(0, len(account_options), 2)]
            await update.message.reply_text(
                f"Выберите счёт для {selected_bank}:",
                reply_markup=ReplyKeyboardMarkup(account_keyboard, one_time_keyboard=True)
            )
            context.user_data["addtransaction_step"] = "select_account"

        elif step == "select_account":
            selected_account_display = message_text.strip()
            bank_name = context.user_data["addtransaction_bank"]
            accounts = get_accounts(user_id, bank_name)
            account_options = [("💰 Основной счёт", "Основной счёт")] + [(f"🏪 {acc_type}", acc_type) for acc_type, _ in accounts]
            selected_account = next((acc_type for acc_display, acc_type in account_options if acc_display == selected_account_display), None)

            if not selected_account:
                await update.message.reply_text("Выберите счёт из списка.", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
                return

            amount = context.user_data["addtransaction_amount"]
            category = context.user_data["addtransaction_category"]
            trans_type = context.user_data["addtransaction_type"]
            banks_str, balances_dict, _ = trans_fetch_user_data(user_id)

            if trans_type == "вычет":
                if selected_account == "Основной счёт":
                    current_balance = float(balances_dict.get(bank_name, 0))
                else:
                    conn = sqlite3.connect(db_path)
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT balance FROM accounts 
                        WHERE user_id = ? AND bank_name = ? AND account_type = ?
                    """, (user_id, bank_name, selected_account))
                    result = cursor.fetchone()
                    current_balance = float(result[0]) if result else 0.0
                    conn.close()
                if current_balance < amount:
                    await update.message.reply_text(f"Недостаточно средств на {selected_account} в {bank_name}.", reply_markup=get_main_menu())
                    await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
                    return

            success, message = save_transaction(user_id, amount, category, bank_name, selected_account, trans_type)
            if success:
                if selected_account == "Основной счёт":
                    current_balance = float(balances_dict.get(bank_name, 0))
                    if trans_type == "доход":
                        balances_dict[bank_name] = str(current_balance + amount)
                    elif trans_type == "вычет":
                        balances_dict[bank_name] = str(current_balance - amount)
                    save_user_data(user_id, user.username, banks=banks_str, balances=balances_dict)
                else:
                    conn = sqlite3.connect(db_path)
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT balance FROM accounts 
                        WHERE user_id = ? AND bank_name = ? AND account_type = ?
                    """, (user_id, bank_name, selected_account))
                    current_balance = float(cursor.fetchone()[0])
                    new_balance = current_balance + amount if trans_type == "доход" else current_balance - amount
                    cursor.execute("""
                        UPDATE accounts 
                        SET balance = ? 
                        WHERE user_id = ? AND bank_name = ? AND account_type = ?
                    """, (new_balance, user_id, bank_name, selected_account))
                    conn.commit()
                    conn.close()

                await update_budget(user_id, category, amount, trans_type, update)

                await update.message.reply_text(
                    f"✅ Транзакция добавлена:\n"
                    f"Тип: {trans_type.capitalize()}\n"
                    f"Сумма: {amount:.2f} руб.\n"
                    f"Категория: {category}\n"
                    f"Банк: {bank_name}\n"
                    f"Счёт: {selected_account}",
                    reply_markup=get_main_menu()
                )
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            else:
                await update.message.reply_text(f"Ошибка: {message}", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())

            keys_to_remove = [
                "addtransaction_step",
                "addtransaction_type",
                "addtransaction_amount",
                "addtransaction_category",
                "addtransaction_bank",
                "addtransaction_account"
            ]
            for key in keys_to_remove:
                context.user_data.pop(key, None)
            logger.info(f"addtransaction: Очищены ключи транзакции, текущее состояние user_data={context.user_data}")

    elif "addaccount_step" in context.user_data:
        step = context.user_data["addaccount_step"]
        if step == "select_bank":
            selected_bank = message_text.strip()
            banks_str, _, _ = trans_fetch_user_data(user_id)
            banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()]

            if selected_bank not in banks_list:
                await update.message.reply_text("Выберите банк из списка.", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
                return

            context.user_data["addaccount_bank"] = selected_bank
            context.user_data["addaccount_step"] = "select_type"
            await update.message.reply_text(
                "Выберите тип счёта:",
                reply_markup=ReplyKeyboardMarkup([[acc_type] for acc_type in ACCOUNT_TYPES], resize_keyboard=True, one_time_keyboard=False)
            )
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        elif step == "select_type":
            selected_type = message_text.strip()
            if selected_type not in ACCOUNT_TYPES:
                await update.message.reply_text("Выберите тип счёта из списка.", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
                return

            context.user_data["addaccount_type"] = selected_type
            context.user_data["addaccount_step"] = "enter_balance"
            await update.message.reply_text(
                f"Введите начальный баланс для '{selected_type}' в {context.user_data['addaccount_bank']} (руб.):",
                reply_markup=get_main_menu()
            )
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        elif step == "enter_balance":
            balance = is_valid_number(message_text.strip())
            if balance is None or balance < 0:
                await update.message.reply_text("Введите корректную положительную сумму числом.", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
                return

            context.user_data["addaccount_balance"] = balance
            if context.user_data["addaccount_type"] == "Накопительный вклад":
                context.user_data["addaccount_step"] = "enter_term"
                await update.message.reply_text("Введите срок вклада в месяцах:", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            else:
                bank_name = context.user_data["addaccount_bank"]
                account_type = context.user_data["addaccount_type"]
                success, message = save_account(user_id, bank_name, account_type, balance)
                if success:
                    await update.message.reply_text(
                        f"Счёт добавлен: {account_type} для {bank_name}, баланс: {balance:.2f} руб.",
                        reply_markup=get_main_menu()
                    )
                    await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
                else:
                    await update.message.reply_text(f"Ошибка при добавлении счёта: {message}", reply_markup=get_main_menu())
                    await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
                context.user_data.pop("addaccount_bank", None)
                context.user_data.pop("addaccount_type", None)
                context.user_data.pop("addaccount_balance", None)
                context.user_data.pop("addaccount_step", None)
        elif step == "enter_term":
            term = is_valid_number(message_text.strip())
            if term is None or term <= 0:
                await update.message.reply_text("Введите корректный положительный срок в месяцах.", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
                return

            context.user_data["addaccount_term"] = int(term)
            context.user_data["addaccount_step"] = "enter_rate"
            await update.message.reply_text("Введите годовую процентную ставку (например, 5 для 5%):", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        elif step == "enter_rate":
            rate = is_valid_number(message_text.strip())
            if rate is None or rate < 0:
                await update.message.reply_text("Введите корректную положительную процентную ставку.", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
                return

            bank_name = context.user_data["addaccount_bank"]
            account_type = context.user_data["addaccount_type"]
            balance = context.user_data["addaccount_balance"]
            term = context.user_data["addaccount_term"]
            rate = float(rate) / 100

            n = 12
            t = term / 12
            final_amount = balance * (1 + rate / n) ** (n * t)

            success, message = save_account(user_id, bank_name, account_type, balance, term, rate * 100)
            if success:
                await update.message.reply_text(
                    f"Накопительный вклад добавлен:\n"
                    f"Банк: {bank_name}\n"
                    f"Начальный баланс: {balance:.2f} руб.\n"
                    f"Срок: {term} мес.\n"
                    f"Ставка: {rate * 100:.2f}%\n"
                    f"Итоговая сумма через {term} мес.: {final_amount:.2f} руб.",
                    reply_markup=get_main_menu()
                )
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
            else:
                await update.message.reply_text(f"Ошибка при добавлении вклада: {message}", reply_markup=get_main_menu())
                await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())

            context.user_data.pop("addaccount_bank", None)
            context.user_data.pop("addaccount_type", None)
            context.user_data.pop("addaccount_balance", None)
            context.user_data.pop("addaccount_term", None)
            context.user_data.pop("addaccount_step", None)
    
    elif "addcredit_step" in context.user_data:
        from credits import handle_credit_steps
        await handle_credit_steps(update, context)
    elif "paycredit_step" in context.user_data:
        from credits import handle_paycredit_steps
        await handle_paycredit_steps(update, context)
    elif "setcreditreminder_step" in context.user_data:
        from credit_reminders import handle_setcreditreminder_steps
        await handle_setcreditreminder_steps(update, context)
    elif "setthreshold_step" in context.user_data:
        from balance_alerts import handle_setthreshold_steps
        await handle_setthreshold_steps(update, context)
    else:
        if context.user_data.get("state") not in ["WAITING_FOR_BANKS", "WAITING_FOR_CUSTOM_BANK", "WAITING_FOR_BALANCE"]:
            save_user_data(user_id, user.username, message=message_text)
            await update.message.reply_text(f"Текст сохранен: {message_text}", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())

async def error_handler(update: Update, context: CallbackContext):
    logger.error(msg="Произошло исключение:", exc_info=context.error)
    try:
        if update.message:
            await update.message.reply_text("Произошла ошибка. Попробуйте снова.", reply_markup=get_main_menu())
            await update.message.reply_text("Выберите действие: ✨", reply_markup=get_inline_menu())
        elif update.callback_query:
            await update.callback_query.message.reply_text("Произошла ошибка. Попробуйте снова.", reply_markup=get_inline_menu())
    except Exception as e:
        logger.error(f"Ошибка в error_handler: {e}")

async def run_bot():
    from telegram.request import HTTPXRequest
    request = HTTPXRequest(
        connection_pool_size=10,
        http_version="1.1",
        read_timeout=60.0,
        write_timeout=60.0,
        connect_timeout=60.0,
    )

    app = Application.builder().token(TOKEN).request(request).build()
    init_db(app)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("view", lambda update, context: require_pin(update, context, view)))
    app.add_handler(CommandHandler("export", lambda update, context: require_pin(update, context, export)))
    app.add_handler(CommandHandler("total", lambda update, context: require_pin(update, context, total)))
    app.add_handler(CommandHandler("convert", lambda update, context: require_pin(update, context, convert)))
    app.add_handler(CommandHandler("chart", lambda update, context: require_pin(update, context, chart)))
    app.add_handler(CommandHandler("transactions", lambda update, context: require_pin(update, context, show_transactions)))
    app.add_handler(CommandHandler("setpin", setpin))
    app.add_handler(CommandHandler("deleteaccount", lambda update, context: require_pin(update, context, deleteaccount)))
    app.add_handler(CommandHandler("addtransaction", lambda update, context: require_pin(update, context, addtransaction)))
    app.add_handler(CommandHandler("transfer", lambda update, context: require_pin(update, context, transfer)))
    app.add_handler(CommandHandler("addaccount", lambda update, context: require_pin(update, context, addaccount)))
    app.add_handler(CommandHandler("reminder", lambda update, context: require_pin(update, context, reminder)))
    app.add_handler(CommandHandler("addcredit", lambda update, context: require_pin(update, context, addcredit)))
    app.add_handler(CommandHandler("paycredit", lambda update, context: require_pin(update, context, paycredit)))
    app.add_handler(CommandHandler("credits", lambda update, context: require_pin(update, context, credits)))
    app.add_handler(CommandHandler("setbudget", lambda update, context: require_pin(update, context, setbudget)))
    app.add_handler(CommandHandler("budget", lambda update, context: require_pin(update, context, budget)))
    app.add_handler(CommandHandler("setcreditreminder", lambda update, context: require_pin(update, context, setcreditreminder)))
    app.add_handler(CommandHandler("setthreshold", lambda update, context: require_pin(update, context, setthreshold)))
    app.add_handler(CommandHandler("reset", reset_state))
    app.add_handler(CommandHandler("resetpin", reset_pin_state))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    max_retries = 5
    retry_delay = 5

    for attempt in range(max_retries):
        try:
            await app.initialize()
            if WEBAPP_URL:
                from telegram import MenuButtonWebApp, WebAppInfo
                await app.bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(text="Открыть", web_app=WebAppInfo(url=WEBAPP_URL)))
            await app.start()
            await app.updater.start_polling(
                drop_pending_updates=True,
                poll_interval=1.0,
                timeout=30,
            )
            while True:
                await asyncio.sleep(3600)
        except telegram.error.TimedOut as e:
            logger.error(f"Попытка {attempt + 1}/{max_retries} не удалась: {e}. Повтор через {retry_delay} сек.")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
            else:
                logger.error("Все попытки исчерпаны. Бот не смог запуститься.")
                raise
        except Exception as e:
            logger.error(f"Неизвестная ошибка при запуске: {e}")
            raise
        finally:
            if app.updater and app.updater.running:
                await app.updater.stop()
            await app.stop()
            await app.shutdown()

if __name__ == "__main__":
    asyncio.run(run_bot())