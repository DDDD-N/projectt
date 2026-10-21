import sqlite3
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import CallbackContext
from transactions import fetch_user_data as trans_fetch_user_data

db_path = None

# Инициализация базы данных для бюджетов
def init_budgets_db():
    global db_path
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            user_id INTEGER,
            category TEXT,
            amount REAL,
            PRIMARY KEY (user_id, category)
        )
    """)
    conn.commit()
    conn.close()
    print("Таблица budgets инициализирована")

# Установка пути к базе данных
def set_db_path(path):
    global db_path
    db_path = path
    init_budgets_db()

# Проверка корректности числа
def is_valid_number(text):
    try:
        return float(text)
    except ValueError:
        return None

# Установка бюджета (начало пошагового процесса)
async def setbudget(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    print(f"Команда /setbudget вызвана для user_id {user_id}")
    
    # Запрашиваем категорию
    await update.message.reply_text(
        "Введите категорию бюджета (например, Еда, Транспорт):",
        reply_markup=ReplyKeyboardRemove()  # Убираем клавиатуру для чистого ввода
    )
    context.user_data["setbudget_step"] = "enter_category"

# Обработка шагов установки бюджета
async def handle_setbudget_steps(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    step = context.user_data.get("setbudget_step")
    message_text = update.message.text.strip()

    if step == "enter_category":
        # Сохраняем категорию и запрашиваем сумму
        context.user_data["budget_category"] = message_text.capitalize()
        await update.message.reply_text(
            f"Категория '{message_text}' выбрана. Введите сумму бюджета (например, 10000):"
        )
        context.user_data["setbudget_step"] = "enter_amount"

    elif step == "enter_amount":
        # Проверяем сумму
        amount = is_valid_number(message_text)
        if amount is None or amount < 0:
            await update.message.reply_text("Сумма должна быть положительным числом. Введите снова:")
            return
        
        category = context.user_data["budget_category"]
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute("INSERT OR REPLACE INTO budgets (user_id, category, amount) VALUES (?, ?, ?)", 
                             (user_id, category, amount))
                conn.commit()
            print(f"Бюджет для {category} сохранён: {amount}")
            await update.message.reply_text(
                f"Бюджет на категорию '{category}' установлен: {amount:,.0f} руб."
            )
        except sqlite3.Error as e:
            print(f"Ошибка базы данных в setbudget: {e}")
            await update.message.reply_text("Произошла ошибка при сохранении бюджета. Попробуйте позже.")

        # Очищаем данные после завершения
        context.user_data.pop("setbudget_step", None)
        context.user_data.pop("budget_category", None)

# Просмотр бюджета
async def budget(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    print(f"Команда /budget вызвана для user_id {user_id}")
    
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT category, amount FROM budgets WHERE user_id = ?", (user_id,))
            budgets = cursor.fetchall()
    except sqlite3.Error as e:
        print(f"Ошибка базы данных в budget: {e}")
        await update.message.reply_text("Произошла ошибка при загрузке бюджета. Попробуйте позже.")
        return
    
    if not budgets:
        await update.message.reply_text("У вас ещё нет установленного бюджета. Используйте /setbudget для начала.")
        return
    
    message = "💰 **Ваш бюджет на текущий период:**\n\n"
    total_budget = 0
    
    for category, amount in budgets:
        message += f"📌 **{category}**\n"
        message += f"  Остаток бюджета: {amount:,.0f} руб.\n\n"
        total_budget += amount
    
    message += f"🏦 **Итого:**\n"
    message += f"  Остаток: {total_budget:,.0f} руб."
    
    await update.message.reply_text(message, parse_mode="Markdown")

# Обновление бюджета при добавлении транзакции
async def update_budget(user_id, category, amount, trans_type, update: Update):
    if trans_type != "вычет":
        return
    
    print(f"Обновление бюджета для user_id {user_id}, категория: {category}, сумма: {amount}")
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT amount FROM budgets WHERE user_id = ? AND category = ?", (user_id, category))
            budget = cursor.fetchone()
            if budget:
                current_budget = budget[0]
                new_budget = current_budget - amount
                if new_budget < 0:
                    await update.message.reply_text(f"⚠️ Внимание: Бюджет на '{category}' превышен на {-new_budget:,.0f} руб.")
                    new_budget = 0
                cursor.execute("UPDATE budgets SET amount = ? WHERE user_id = ? AND category = ?", 
                               (new_budget, user_id, category))
                conn.commit()
                print(f"Бюджет для {category} обновлён: {new_budget}")
            else:
                print(f"Бюджет для категории {category} не найден")
    except sqlite3.Error as e:
        print(f"Ошибка базы данных в update_budget: {e}")
        await update.message.reply_text("Произошла ошибка при обновлении бюджета. Попробуйте позже.")