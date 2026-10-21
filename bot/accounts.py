import sqlite3
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import CallbackContext
from transactions import fetch_user_data as trans_fetch_user_data

db_path = None
ACCOUNT_TYPES = ["Текущий счёт", "Сберегательный счёт", "Накопительный вклад"]

def set_db_path(path):
    global db_path
    db_path = path

def init_accounts_db():
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            bank_name TEXT,
            account_type TEXT,
            balance REAL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    
    cursor.execute("PRAGMA table_info(accounts)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if "term" not in columns:
        cursor.execute("ALTER TABLE accounts ADD COLUMN term INTEGER DEFAULT NULL")
        print("Добавлен столбец 'term' в таблицу accounts.")
    if "rate" not in columns:
        cursor.execute("ALTER TABLE accounts ADD COLUMN rate REAL DEFAULT NULL")
        print("Добавлен столбец 'rate' в таблицу accounts.")
    
    conn.commit()
    conn.close()

def save_account(user_id, bank_name, account_type, balance, term=None, rate=None):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO accounts (user_id, bank_name, account_type, balance, term, rate)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, bank_name, account_type, float(balance), term, rate))
        conn.commit()
        conn.close()
        return True, "Счёт успешно добавлен."
    except sqlite3.IntegrityError:
        conn.close()
        return False, "Такой счёт уже существует."
    except Exception as e:
        conn.close()
        return False, str(e)

def get_accounts(user_id, bank_name=None):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    if bank_name:
        cursor.execute("""
            SELECT account_type, balance FROM accounts 
            WHERE user_id = ? AND bank_name = ?
        """, (user_id, bank_name))
    else:
        cursor.execute("SELECT account_type, balance FROM accounts WHERE user_id = ?", (user_id,))
    accounts = cursor.fetchall()
    conn.close()
    return accounts

async def addaccount(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    banks_str, _, _ = trans_fetch_user_data(user_id)

    if not banks_str:
        await update.message.reply_text("У вас нет банков. Добавьте их с помощью /start.")
        return

    banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()]
    await update.message.reply_text(
        "Выберите банк для добавления счёта:",
        reply_markup=ReplyKeyboardMarkup([[bank] for bank in banks_list], one_time_keyboard=True)
    )
    context.user_data["addaccount_step"] = "select_bank"