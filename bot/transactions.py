import sqlite3
from datetime import datetime, date
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import CallbackContext

# 📌 Глобальная переменная для пути к базе данных
db_path = None

# 📌 Установка пути к базе данных
def set_db_path(path):
    global db_path
    db_path = path

# 📌 Инициализация базы данных транзакций
def init_transactions_db():
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            category TEXT,
            bank_name TEXT,
            account_type TEXT,
            trans_type TEXT,
            date TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)
    cursor.execute("PRAGMA table_info(transactions)")
    columns = [col[1] for col in cursor.fetchall()]
    if "bank_name" not in columns:
        cursor.execute("ALTER TABLE transactions ADD COLUMN bank_name TEXT")
        print("Столбец 'bank_name' добавлен в таблицу transactions.")
    if "trans_type" not in columns:
        cursor.execute("ALTER TABLE transactions ADD COLUMN trans_type TEXT")
        print("Столбец 'trans_type' добавлен в таблицу transactions.")
    if "account_type" not in columns:
        cursor.execute("ALTER TABLE transactions ADD COLUMN account_type TEXT")
        print("Столбец 'account_type' добавлен в таблицу transactions.")
    conn.commit()
    conn.close()

# 📌 Получение списка банков пользователя
def get_user_banks(user_id):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT banks FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result and result[0]:
        return [bank.strip() for bank in result[0].split(",")]
    return []

# 📌 Сохранение данных пользователя
def save_user_data(user_id, username=None, banks=None, balances=None, message=None, pin=None):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Если PIN-код не передан, пытаемся сохранить существующий
    if pin is None:
        cursor.execute("SELECT pin FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        pin = result[0] if result and result[0] else None

    cursor.execute("""
        INSERT OR REPLACE INTO users (user_id, username, banks, balances, message, pin)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, username, banks, str(balances) if balances else None, message, pin))
    conn.commit()
    conn.close()

# 📌 Получение данных пользователя
def fetch_user_data(user_id):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT banks, balances, pin FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        banks_str, balances_str, pin = result
        balances_dict = eval(balances_str) if balances_str else {}
        return banks_str or "", balances_dict, pin
    return "", {}, None

# 📌 Сохранение транзакции
def save_transaction(user_id, amount, category, bank_name, account_type, trans_type):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        banks = get_user_banks(user_id)
        if bank_name not in banks:
            conn.close()
            print(f"Банк '{bank_name}' не найден в списке банков пользователя {user_id}: {banks}")
            return False, f"Банк '{bank_name}' не найден в ваших данных."

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"Сохранение транзакции: user_id={user_id}, amount={amount}, category={category}, bank_name={bank_name}, account_type={account_type}, trans_type={trans_type}, date={date_str}")
        cursor.execute("""
            INSERT INTO transactions (user_id, amount, category, bank_name, account_type, trans_type, date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, float(amount), category, bank_name, account_type, trans_type, date_str))
        
        conn.commit()
        conn.close()
        return True, "Транзакция успешно добавлена."
    except sqlite3.Error as e:
        conn.close()
        print(f"Ошибка SQLite в save_transaction: {e}")
        return False, f"Ошибка базы данных: {str(e)}"
    except Exception as e:
        conn.close()
        print(f"Неизвестная ошибка в save_transaction: {e}")
        return False, f"Неизвестная ошибка: {str(e)}"

# 📌 Получение списка всех транзакций пользователя
def get_transactions(user_id):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT amount, category, trans_type, bank_name, account_type, date
        FROM transactions
        WHERE user_id = ?
        ORDER BY date DESC
    """, (user_id,))
    transactions_list = cursor.fetchall()
    print(f"Извлечено транзакций для user_id={user_id}: {len(transactions_list)}")
    for t in transactions_list:
        print(f"Транзакция: {t}")
    conn.close()
    return transactions_list

# 📌 Команда /addtransaction
async def addtransaction(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    banks = get_user_banks(user_id)
    
    if not banks:
        await update.message.reply_text("У вас нет сохранённых банков. Используйте /start для добавления.")
        return
    
    # Запрашиваем тип транзакции
    await update.message.reply_text(
        "Выберите тип транзакции:",
        reply_markup=ReplyKeyboardMarkup([["Доход", "Вычет"]], one_time_keyboard=True)
    )
    context.user_data["addtransaction_step"] = "select_type"

# 📌 Просмотр транзакций за день (команда /transactions)
async def transactions(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    today = date.today().strftime("%Y-%m-%d")
    print(f"Запрос транзакций для user_id={user_id}, дата={today}")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT amount, category, bank_name, account_type, trans_type, date
            FROM transactions
            WHERE user_id = ? AND date LIKE ?
            ORDER BY date DESC
        """, (user_id, f"{today}%"))
        transactions_list = cursor.fetchall()
        print(f"Найдено транзакций: {len(transactions_list)}")
        for t in transactions_list:
            print(f"Транзакция: {t}")

        conn.close()

        if not transactions_list:
            await update.message.reply_text(f"У вас нет транзакций за {today}.")
            return

        incomes = []
        expenses = []
        transfers = []
        total_income = 0
        total_expense = 0

        for amount, category, bank_name, account_type, trans_type, trans_date in transactions_list:
            acc_display = account_type if account_type != "Основной счёт" else bank_name
            if trans_type == "доход":
                incomes.append(f"+{amount:.2f} руб. | {category} | {acc_display} | {trans_date.split()[0]}")
                total_income += amount
            elif trans_type == "вычет":
                expenses.append(f"-{amount:.2f} руб. | {category} | {acc_display} | {trans_date.split()[0]}")
                total_expense += amount
            elif trans_type == "перевод":
                transfers.append(f"{amount:.2f} руб. | {category} | {trans_date.split()[0]}")

        message = f"📅 Транзакции за {today}:\n\n"
        
        message += "📈 Доходы:\n"
        if incomes:
            message += "\n".join(incomes) + "\n"
            message += f"Итого доходов: +{total_income:.2f} руб.\n"
        else:
            message += "Нет доходов.\n"

        message += "\n📉 Расходы:\n"
        if expenses:
            message += "\n".join(expenses) + "\n"
            message += f"Итого расходов: -{total_expense:.2f} руб.\n"
        else:
            message += "Нет расходов.\n"

        message += "\n🔄 Переводы:\n"
        if transfers:
            message += "\n".join(transfers) + "\n"
        else:
            message += "Нет переводов.\n"

        await update.message.reply_text(message)
    except Exception as e:
        await update.message.reply_text(f"Ошибка при просмотре транзакций: {e}")
        print(f"Ошибка в /transactions для user_id {user_id}: {e}")
