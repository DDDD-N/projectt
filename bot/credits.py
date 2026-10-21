import sqlite3
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import CallbackContext
from transactions import fetch_user_data as trans_fetch_user_data, save_user_data, save_transaction, get_user_banks
from accounts import get_accounts

db_path = None

def set_db_path(path):
    global db_path
    db_path = path

def init_credits_db():
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS credits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            bank_name TEXT,
            amount REAL,
            term INTEGER,
            rate REAL,
            monthly_payment REAL,
            remaining_amount REAL,
            payment_day INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    
    cursor.execute("PRAGMA table_info(credits)")
    columns = [col[1] for col in cursor.fetchall()]
    if "remaining_amount" not in columns:
        cursor.execute("ALTER TABLE credits ADD COLUMN remaining_amount REAL DEFAULT NULL")
        cursor.execute("UPDATE credits SET remaining_amount = amount WHERE remaining_amount IS NULL")
    if "payment_day" not in columns:
        cursor.execute("ALTER TABLE credits ADD COLUMN payment_day INTEGER DEFAULT NULL")
    
    conn.commit()
    conn.close()
    print("Таблица credits инициализирована.")

def save_credit(user_id, bank_name, amount, term, rate, monthly_payment, payment_day):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO credits (user_id, bank_name, amount, term, rate, monthly_payment, remaining_amount, payment_day)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, bank_name, amount, term, rate, monthly_payment, amount, payment_day))
        conn.commit()
        conn.close()
        return True, "Кредит успешно добавлен."
    except Exception as e:
        conn.close()
        return False, str(e)

def get_credits(user_id):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, bank_name, amount, term, rate, monthly_payment, remaining_amount, payment_day 
        FROM credits 
        WHERE user_id = ?
    """, (user_id,))
    credits = cursor.fetchall()
    conn.close()
    return credits

def update_credit_remaining(user_id, credit_id, payment_amount, bank_name, account_type):
    conn = None
    try:
        # Открываем соединение с увеличенным тайм-аутом (например, 10 секунд)
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        
        # Проверяем остаток кредита
        cursor.execute("""
            SELECT remaining_amount, bank_name FROM credits 
            WHERE user_id = ? AND id = ?
        """, (user_id, credit_id))
        result = cursor.fetchone()
        if not result:
            return False, "Кредит не найден."
        
        current_remaining, credit_bank_name = result
        new_remaining = max(0, current_remaining - payment_amount)

        # Получаем данные пользователя
        banks_str, balances_dict, _ = trans_fetch_user_data(user_id)
        
        # Обновляем баланс счета
        if account_type == "Основной счёт":
            current_balance = float(balances_dict.get(bank_name, '0'))
            if current_balance < payment_amount:
                return False, f"Недостаточно средств на основном счёте {bank_name}."
            balances_dict[bank_name] = str(current_balance - payment_amount)
            save_user_data(user_id, None, banks=banks_str, balances=balances_dict)
        else:
            cursor.execute("""
                SELECT balance FROM accounts 
                WHERE user_id = ? AND bank_name = ? AND account_type = ?
            """, (user_id, bank_name, account_type))
            result = cursor.fetchone()
            if not result or result[0] < payment_amount:
                return False, f"Недостаточно средств на {account_type} в {bank_name}."
            new_balance = result[0] - payment_amount
            cursor.execute("""
                UPDATE accounts 
                SET balance = ? 
                WHERE user_id = ? AND bank_name = ? AND account_type = ?
            """, (new_balance, user_id, bank_name, account_type))

        # Обновляем остаток кредита
        cursor.execute("""
            UPDATE credits 
            SET remaining_amount = ? 
            WHERE user_id = ? AND id = ?
        """, (new_remaining, user_id, credit_id))
        
        # Сохраняем транзакцию в той же транзакции
        category = f"Погашение кредита (ID: {credit_id})"
        banks = get_user_banks(user_id)
        if bank_name not in banks:
            return False, f"Банк '{bank_name}' не найден в ваших данных."
        
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"Сохранение транзакции: user_id={user_id}, amount={payment_amount}, category={category}, bank_name={bank_name}, account_type={account_type}, trans_type='вычет', date={date_str}")
        cursor.execute("""
            INSERT INTO transactions (user_id, amount, category, bank_name, account_type, trans_type, date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, float(payment_amount), category, bank_name, account_type, "вычет", date_str))
        
        # Завершаем транзакцию
        conn.commit()
        print(f"Транзакция успешно сохранена для user_id={user_id}")
        
        return True, f"Платёж внесён со счёта {account_type} в {bank_name}. Остаток кредита: {new_remaining:.2f} руб."
    except sqlite3.Error as e:
        if conn:
            conn.rollback()  # Откатываем изменения в случае ошибки
        print(f"Ошибка SQLite в update_credit_remaining: {e}")
        return False, f"Ошибка базы данных: {str(e)}"
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Неизвестная ошибка в update_credit_remaining: {e}")
        return False, f"Неизвестная ошибка: {str(e)}"
    finally:
        if conn:
            conn.close()  # Гарантируем закрытие соединения
    
def calculate_monthly_payment(amount, term, rate):
    monthly_rate = rate / 100 / 12
    monthly_payment = (monthly_rate * amount) / (1 - (1 + monthly_rate) ** (-term))
    return monthly_payment

async def addcredit(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    banks_str, _, _ = trans_fetch_user_data(user_id)

    if not banks_str:
        await update.message.reply_text("У вас нет банков. Добавьте их с помощью /start.")
        return

    banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()]
    await update.message.reply_text(
        "Выберите банк для добавления кредита:",
        reply_markup=ReplyKeyboardMarkup([[bank] for bank in banks_list], one_time_keyboard=True)
    )
    context.user_data["addcredit_step"] = "select_bank"

async def handle_credit_steps(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    step = context.user_data.get("addcredit_step")
    message_text = update.message.text.strip()

    if step == "select_bank":
        banks_str, _, _ = trans_fetch_user_data(user_id)
        banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()]
        
        if message_text not in banks_list:
            await update.message.reply_text("Выберите банк из списка.")
            return
        
        context.user_data["credit_bank"] = message_text
        context.user_data["addcredit_step"] = "enter_amount"
        await update.message.reply_text("Введите сумму кредита (руб.):")

    elif step == "enter_amount":
        amount = is_valid_number(message_text)
        if amount is None or amount <= 0:
            await update.message.reply_text("Введите корректную положительную сумму числом.")
            return
        
        context.user_data["credit_amount"] = amount
        context.user_data["addcredit_step"] = "enter_term"
        await update.message.reply_text("Введите срок кредита в месяцах:")

    elif step == "enter_term":
        term = is_valid_number(message_text)
        if term is None or term <= 0 or not term.is_integer():
            await update.message.reply_text("Введите корректный положительный срок в месяцах (целое число).")
            return
        
        context.user_data["credit_term"] = int(term)
        context.user_data["addcredit_step"] = "enter_rate"
        await update.message.reply_text("Введите годовую процентную ставку (например, 10 для 10%):")

    elif step == "enter_rate":
        rate = is_valid_number(message_text)
        if rate is None or rate < 0:
            await update.message.reply_text("Введите корректную положительную процентную ставку числом.")
            return
        
        context.user_data["credit_rate"] = float(rate)
        context.user_data["addcredit_step"] = "enter_payment_day"
        await update.message.reply_text("Введите день месяца для платежа (1-31):")

    elif step == "enter_payment_day":
        payment_day = is_valid_number(message_text)
        if payment_day is None or payment_day < 1 or payment_day > 31 or not payment_day.is_integer():
            await update.message.reply_text("Введите корректный день месяца (целое число от 1 до 31).")
            return
        
        bank_name = context.user_data["credit_bank"]
        amount = context.user_data["credit_amount"]
        term = context.user_data["credit_term"]
        rate = context.user_data["credit_rate"]
        payment_day = int(payment_day)
        
        monthly_payment = calculate_monthly_payment(amount, term, rate)
        total_payment = monthly_payment * term

        success, message = save_credit(user_id, bank_name, amount, term, rate, monthly_payment, payment_day)
        if success:
            await update.message.reply_text(
                f"Кредит добавлен:\n"
                f"Банк: {bank_name}\n"
                f"Сумма: {amount:.2f} руб.\n"
                f"Срок: {term} мес.\n"
                f"Ставка: {rate:.2f}%\n"
                f"Ежемесячный платёж: {monthly_payment:.2f} руб.\n"
                f"День платежа: {payment_day} число\n"
                f"Общая выплата: {total_payment:.2f} руб.",
                reply_markup=ReplyKeyboardRemove()
            )
        else:
            await update.message.reply_text(f"Ошибка при добавлении кредита: {message}")

        context.user_data.pop("credit_bank", None)
        context.user_data.pop("credit_amount", None)
        context.user_data.pop("credit_term", None)
        context.user_data.pop("credit_rate", None)
        context.user_data.pop("addcredit_step", None)

async def paycredit(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    credits = get_credits(user_id)

    if not credits:
        await update.message.reply_text("У вас нет активных кредитов.")
        return

    credit_options = [f"{credit[1]} (ID: {credit[0]}) - Остаток: {credit[6]:.2f} руб." for credit in credits]
    await update.message.reply_text(
        "Выберите кредит для оплаты:",
        reply_markup=ReplyKeyboardMarkup([[option] for option in credit_options], one_time_keyboard=True)
    )
    context.user_data["paycredit_step"] = "select_credit"

async def handle_paycredit_steps(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    step = context.user_data.get("paycredit_step")
    message_text = update.message.text.strip()

    if step == "select_credit":
        credits = get_credits(user_id)
        credit_dict = {f"{credit[1]} (ID: {credit[0]}) - Остаток: {credit[6]:.2f} руб.": credit[0] for credit in credits}
        
        if message_text not in credit_dict:
            await update.message.reply_text("Выберите кредит из списка.")
            return
        
        context.user_data["credit_id"] = credit_dict[message_text]
        banks_str, balances_dict, _ = trans_fetch_user_data(user_id)
        banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()]
        account_options = []
        for bank in banks_list:
            balance = float(balances_dict.get(bank, '0'))
            account_options.append(f"{bank}: Основной счёт ({balance:.2f} руб.)")
            accounts = get_accounts(user_id, bank)
            for acc_type, balance in accounts:
                account_options.append(f"{bank}: {acc_type} ({balance:.2f} руб.)")
        
        await update.message.reply_text(
            "Выберите счёт для оплаты:",
            reply_markup=ReplyKeyboardMarkup([[option] for option in account_options], one_time_keyboard=True)
        )
        context.user_data["paycredit_step"] = "select_account"

    elif step == "select_account":
        banks_str, balances_dict, _ = trans_fetch_user_data(user_id)
        banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()]
        account_options = []
        for bank in banks_list:
            balance = float(balances_dict.get(bank, '0'))
            account_options.append(f"{bank}: Основной счёт ({balance:.2f} руб.)")
            accounts = get_accounts(user_id, bank)
            for acc_type, balance in accounts:
                account_options.append(f"{bank}: {acc_type} ({balance:.2f} руб.)")
        
        if message_text not in account_options:
            await update.message.reply_text("Выберите счёт из списка.")
            return
        
        bank_name, account_info = message_text.split(": ", 1)
        account_type = account_info.split(" (")[0]
        context.user_data["paycredit_bank"] = bank_name
        context.user_data["paycredit_account"] = account_type
        context.user_data["paycredit_step"] = "enter_payment"
        await update.message.reply_text(f"Введите сумму платежа для списания с {bank_name} ({account_type}):")

    elif step == "enter_payment":
        payment = is_valid_number(message_text)
        if payment is None or payment <= 0:
            await update.message.reply_text("Введите корректную положительную сумму числом.")
            return
        
        credit_id = context.user_data["credit_id"]
        bank_name = context.user_data["paycredit_bank"]
        account_type = context.user_data["paycredit_account"]
        success, message = update_credit_remaining(user_id, credit_id, payment, bank_name, account_type)
        
        if success:
            await update.message.reply_text(message, reply_markup=ReplyKeyboardRemove())
        else:
            await update.message.reply_text(f"Ошибка при внесении платежа: {message}")

        context.user_data.pop("credit_id", None)
        context.user_data.pop("paycredit_bank", None)
        context.user_data.pop("paycredit_account", None)
        context.user_data.pop("paycredit_step", None)

async def credits(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    credits_list = get_credits(user_id)
    
    if not credits_list:
        await update.message.reply_text("У вас нет активных кредитов.")
        return
    
    message = "Ваши кредиты:\n\n"
    for credit_id, bank, amount, term, rate, monthly, remaining, payment_day in credits_list:
        total = monthly * term
        message += (f"ID: {credit_id}\n"
                    f"Банк: {bank}\n"
                    f"Сумма: {amount:.2f} руб.\n"
                    f"Срок: {term} мес.\n"
                    f"Ставка: {rate:.2f}%\n"
                    f"Ежемесячный платёж: {monthly:.2f} руб.\n"
                    f"День платежа: {payment_day} число\n"
                    f"Остаток: {remaining:.2f} руб.\n"
                    f"Общая выплата: {total:.2f} руб.\n\n")
    await update.message.reply_text(message)

def is_valid_number(text):
    try:
        return float(text)
    except ValueError:
        return None