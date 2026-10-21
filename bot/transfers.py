import sqlite3
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import CallbackContext
from datetime import datetime

# 📌 Глобальная переменная для пути к базе данных
db_path = None

# 📌 Установка пути к базе данных
def set_db_path(path):
    global db_path
    db_path = path

# 📌 Получение списка банков пользователя
def get_user_banks(user_id):
    with sqlite3.connect(db_path, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT banks FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
    if result and result[0]:
        return [bank.strip() for bank in result[0].split(",")]
    return []

# 📌 Получение счетов для банка
def get_accounts(user_id, bank_name):
    with sqlite3.connect(db_path, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT account_type, balance 
            FROM accounts 
            WHERE user_id = ? AND bank_name = ?
        """, (user_id, bank_name))
        return cursor.fetchall()

# 📌 Выполнение перевода между счетами
def perform_transfer(user_id, source_bank, source_account, dest_bank, dest_account, amount):
    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Проверяем баланс источника
            if source_account == "Основной счёт":
                cursor.execute("SELECT balances FROM users WHERE user_id = ?", (user_id,))
                balances_dict = eval(cursor.fetchone()["balances"])
                source_balance = float(balances_dict.get(source_bank, 0))
            else:
                cursor.execute("""
                    SELECT balance FROM accounts 
                    WHERE user_id = ? AND bank_name = ? AND account_type = ?
                """, (user_id, source_bank, source_account))
                source_balance = float(cursor.fetchone()["balance"])

            if source_balance < amount:
                return False, f"Недостаточно средств на {source_account} в {source_bank}."

            # Обновляем баланс источника
            if source_account == "Основной счёт":
                new_source_balance = source_balance - amount
                balances_dict[source_bank] = str(new_source_balance)
                cursor.execute("""
                    UPDATE users 
                    SET balances = ? 
                    WHERE user_id = ?
                """, (str(balances_dict), user_id))
            else:
                new_source_balance = source_balance - amount
                cursor.execute("""
                    UPDATE accounts 
                    SET balance = ? 
                    WHERE user_id = ? AND bank_name = ? AND account_type = ?
                """, (new_source_balance, user_id, source_bank, source_account))

            # Обновляем баланс назначения
            if dest_account == "Основной счёт":
                cursor.execute("SELECT balances FROM users WHERE user_id = ?", (user_id,))
                balances_dict = eval(cursor.fetchone()["balances"])
                dest_balance = float(balances_dict.get(dest_bank, 0))
                new_dest_balance = dest_balance + amount
                balances_dict[dest_bank] = str(new_dest_balance)
                cursor.execute("""
                    UPDATE users 
                    SET balances = ? 
                    WHERE user_id = ?
                """, (str(balances_dict), user_id))
            else:
                cursor.execute("""
                    SELECT balance FROM accounts 
                    WHERE user_id = ? AND bank_name = ? AND account_type = ?
                """, (user_id, dest_bank, dest_account))
                dest_balance = float(cursor.fetchone()["balance"])
                new_dest_balance = dest_balance + amount
                cursor.execute("""
                    UPDATE accounts 
                    SET balance = ? 
                    WHERE user_id = ? AND bank_name = ? AND account_type = ?
                """, (new_dest_balance, user_id, dest_bank, dest_account))

            # Записываем транзакцию в рамках того же соединения
            category = f"Перевод с {source_bank} ({source_account}) на {dest_bank} ({dest_account})"
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("""
                INSERT INTO transactions (user_id, amount, category, bank_name, account_type, trans_type, date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, float(amount), category, source_bank, source_account, "перевод", date_str))
            print(f"Сохранение транзакции перевода: user_id={user_id}, amount={amount}, category={category}, date={date_str}")

            conn.commit()

        return True, f"Перевод успешно выполнен: {amount} руб. с {source_bank} ({source_account}) на {dest_bank} ({dest_account})."
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            return False, "Ошибка при переводе: база данных временно заблокирована. Попробуйте снова."
        return False, f"Ошибка базы данных: {str(e)}"
    except Exception as e:
        return False, f"Неизвестная ошибка при переводе: {str(e)}"

# 📌 Команда /transfer
async def transfer(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    banks = get_user_banks(user_id)

    if not banks:
        await update.message.reply_text("У вас нет сохранённых банков. Используйте /start для добавления.")
        return

    if "transfer_step" not in context.user_data:
        await update.message.reply_text(
            "Выберите банк-источник для перевода:",
            reply_markup=ReplyKeyboardMarkup([[bank] for bank in banks], one_time_keyboard=True)
        )
        context.user_data["transfer_step"] = "select_source_bank"
        return

    step = context.user_data["transfer_step"]

    if step == "select_source_bank":
        source_bank = update.message.text.strip()
        if source_bank not in banks:
            await update.message.reply_text("Выберите банк из списка.")
            return

        accounts = get_accounts(user_id, source_bank)
        account_options = ["Основной счёт"] + [acc_type for acc_type, _ in accounts]
        await update.message.reply_text(
            "Выберите счёт-источник:",
            reply_markup=ReplyKeyboardMarkup([[option] for option in account_options], one_time_keyboard=True)
        )
        context.user_data["source_bank"] = source_bank
        context.user_data["transfer_step"] = "select_source_account"

    elif step == "select_source_account":
        source_account = update.message.text.strip()
        source_bank = context.user_data["source_bank"]
        accounts = get_accounts(user_id, source_bank)
        account_options = ["Основной счёт"] + [acc_type for acc_type, _ in accounts]

        if source_account not in account_options:
            await update.message.reply_text("Выберите счёт из списка.")
            return

        await update.message.reply_text(
            "Выберите банк-назначение для перевода:",
            reply_markup=ReplyKeyboardMarkup([[bank] for bank in banks], one_time_keyboard=True)
        )
        context.user_data["source_account"] = source_account
        context.user_data["transfer_step"] = "select_dest_bank"

    elif step == "select_dest_bank":
        dest_bank = update.message.text.strip()
        if dest_bank not in banks:
            await update.message.reply_text("Выберите банк из списка.")
            return

        accounts = get_accounts(user_id, dest_bank)
        account_options = ["Основной счёт"] + [acc_type for acc_type, _ in accounts]
        await update.message.reply_text(
            "Выберите счёт-назначение:",
            reply_markup=ReplyKeyboardMarkup([[option] for option in account_options], one_time_keyboard=True)
        )
        context.user_data["dest_bank"] = dest_bank
        context.user_data["transfer_step"] = "select_dest_account"

    elif step == "select_dest_account":
        dest_account = update.message.text.strip()
        dest_bank = context.user_data["dest_bank"]
        accounts = get_accounts(user_id, dest_bank)
        account_options = ["Основной счёт"] + [acc_type for acc_type, _ in accounts]

        if dest_account not in account_options:
            await update.message.reply_text("Выберите счёт из списка.")
            return

        await update.message.reply_text("Введите сумму перевода:")
        context.user_data["dest_account"] = dest_account
        context.user_data["transfer_step"] = "enter_amount"

    elif step == "enter_amount":
        try:
            amount = float(update.message.text.strip())
            if amount <= 0:
                await update.message.reply_text("Сумма должна быть положительной.")
                return

            source_bank = context.user_data["source_bank"]
            source_account = context.user_data["source_account"]
            dest_bank = context.user_data["dest_bank"]
            dest_account = context.user_data["dest_account"]

            success, message = perform_transfer(user_id, source_bank, source_account, dest_bank, dest_account, amount)
            await update.message.reply_text(message, reply_markup=ReplyKeyboardRemove())

            # Очищаем состояние после выполнения
            context.user_data.clear()
        except ValueError:
            await update.message.reply_text("Введите корректную сумму числом.")