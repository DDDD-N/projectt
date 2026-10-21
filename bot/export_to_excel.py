import sqlite3
import pandas as pd
from datetime import datetime
import logging

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.WARNING)
logger = logging.getLogger(__name__)

def export_data_to_excel(user_id):
    global db_path
    try:
        conn = sqlite3.connect(db_path)

        # Извлекаем данные из таблицы users
        cursor = conn.cursor()
        cursor.execute("SELECT username, banks, balances, balance_threshold, pin FROM users WHERE user_id = ?", (user_id,))
        user_data = cursor.fetchone()

        if not user_data:
            conn.close()
            return None

        username, banks_str, balances_str, balance_threshold, pin = user_data
        balances_dict = eval(balances_str) if balances_str else {}

        # Основные счета
        banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()] if banks_str else []
        main_accounts_data = {
            "Банк": banks_list,
            "Баланс (руб.)": [float(balances_dict.get(bank, 0)) for bank in banks_list]
        }
        df_main_accounts = pd.DataFrame(main_accounts_data)
        if not df_main_accounts.empty:
            df_main_accounts.loc["Итого"] = ["Общая сумма", df_main_accounts["Баланс (руб.)"].sum()]

        # Транзакции (доходы, расходы, переводы)
        query_transactions = """
            SELECT amount, category, trans_type, bank_name, account_type, date 
            FROM transactions WHERE user_id = ?
        """
        df_transactions = pd.read_sql_query(query_transactions, conn, params=(user_id,))
        if not df_transactions.empty:
            df_transactions.columns = ["Сумма (руб.)", "Категория", "Тип транзакции", "Банк", "Тип счета", "Дата"]
            df_transactions["Сумма (руб.)"] = df_transactions["Сумма (руб.)"].astype(float)
            
            # Разделение на доходы, расходы и переводы
            df_incomes = df_transactions[df_transactions["Тип транзакции"] == "доход"]
            df_expenses = df_transactions[df_transactions["Тип транзакции"] == "вычет"]
            df_transfers = df_transactions[df_transactions["Тип транзакции"] == "перевод"]
            
            if not df_incomes.empty:
                df_incomes.loc["Итого"] = [df_incomes["Сумма (руб.)"].sum(), "", "Доходы", "", "", ""]
            if not df_expenses.empty:
                df_expenses.loc["Итого"] = [df_expenses["Сумма (руб.)"].sum(), "", "Расходы", "", "", ""]
            if not df_transfers.empty:
                df_transfers.loc["Итого"] = [df_transfers["Сумма (руб.)"].sum(), "", "Переводы", "", "", ""]

        # Кредиты (если есть)
        query_credits = """
            SELECT id, bank_name, amount, rate, term, monthly_payment, remaining_amount, payment_day 
            FROM credits WHERE user_id = ?
        """
        df_credits = pd.read_sql_query(query_credits, conn, params=(user_id,))
        if not df_credits.empty:
            df_credits.columns = [
                "ID",
                "Банк",
                "Сумма кредита (руб.)",
                "Годовая ставка (%)",
                "Срок (мес.)",
                "Ежемесячный платёж (руб.)",
                "Остаток долга (руб.)",
                "День платежа"
            ]
            df_credits["Общая выплата (руб.)"] = df_credits["Ежемесячный платёж (руб.)"] * df_credits["Срок (мес.)"]
            df_credits.loc["Итого"] = [
                "",  # ID
                "",  # Банк
                df_credits["Сумма кредита (руб.)"].sum(),
                "",  # Ставка
                "",  # Срок
                df_credits["Ежемесячный платёж (руб.)"].sum(),
                df_credits["Остаток долга (руб.)"].sum(),
                "",  # День платежа
                df_credits["Общая выплата (руб.)"].sum()
            ]

        # Сводка
        total_income = df_incomes["Сумма (руб.)"][:-1].sum() if not df_incomes.empty else 0
        total_expense = df_expenses["Сумма (руб.)"][:-1].sum() if not df_expenses.empty else 0
        total_transfers = df_transfers["Сумма (руб.)"][:-1].sum() if not df_transfers.empty else 0

        summary_data = {
            "Метрика": [
                "Имя пользователя",
                "Общий баланс (основные счета)",
                "Общий доход",
                "Общий расход",
                "Общие переводы",
                "Общая сумма кредитов",
                "Общий остаток по кредитам",
                "Сумма ежемесячных платежей",
                "Общая выплата по кредитам"
            ],
            "Значение": [
                username,
                main_accounts_data["Баланс (руб.)"][:-1].sum() if not df_main_accounts.empty else 0,
                total_income,
                total_expense,
                total_transfers,
                df_credits["Сумма кредита (руб.)"][:-1].sum() if not df_credits.empty else 0,
                df_credits["Остаток долга (руб.)"][:-1].sum() if not df_credits.empty else 0,
                df_credits["Ежемесячный платёж (руб.)"][:-1].sum() if not df_credits.empty else 0,
                df_credits["Общая выплата (руб.)"][:-1].sum() if not df_credits.empty else 0
            ]
        }
        df_summary = pd.DataFrame(summary_data)

        # Проверка на наличие данных
        if df_main_accounts.empty and df_transactions.empty and df_credits.empty:
            conn.close()
            return None

        # Создаем Excel-файл
        excel_file = f"user_data_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        with pd.ExcelWriter(excel_file, engine="openpyxl") as writer:
            df_summary.to_excel(writer, sheet_name="Сводка", index=False)
            if not df_main_accounts.empty:
                df_main_accounts.to_excel(writer, sheet_name="Основные счета", index=False)
            if not df_incomes.empty:
                df_incomes.to_excel(writer, sheet_name="Доходы", index=False)
            if not df_expenses.empty:
                df_expenses.to_excel(writer, sheet_name="Расходы", index=False)
            if not df_transfers.empty:
                df_transfers.to_excel(writer, sheet_name="Переводы", index=False)
            if not df_credits.empty:
                df_credits.to_excel(writer, sheet_name="Кредиты", index=False)

        conn.close()
        return excel_file

    except Exception as e:
        logger.error(f"Ошибка в export_data_to_excel для user_id={user_id}: {e}")
        return None

# Тестирование
if __name__ == "__main__":
    db_path = "users.db"  # Укажите путь к вашей базе данных
    user_id = 682967079  # Ваш user_id
    result = export_data_to_excel(user_id)
    if result:
        print(f"Файл успешно создан: {result}")
    else:
        print("Не удалось создать файл.")