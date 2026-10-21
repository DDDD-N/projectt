import sqlite3
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import CallbackContext, CommandHandler
import logging
from transactions import fetch_user_data as trans_fetch_user_data
from accounts import get_accounts

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

db_path = None

def set_db_path(path):
    """Установка пути к базе данных."""
    global db_path
    db_path = path

def get_balance_advice(balance, threshold):
    """Возвращает совет в зависимости от уровня баланса."""
    if balance < threshold * 0.1:  # Менее 10% от порога
        return (
            "Ваш баланс критически низкий! Рекомендуем:\n"
            "- Сократите необязательные расходы.\n"
            "- Рассмотрите перевод средств с другого счёта.\n"
            "- Проверьте возможность дополнительного дохода."
        )
    elif balance < threshold * 0.5:  # Менее 50% от порога
        return (
            "Ваш баланс ниже половины установленного порога. Советы:\n"
            "- Пересмотрите бюджет и приоритеты расходов.\n"
            "- Избегайте крупных покупок в ближайшее время.\n"
            "- Проверьте свои транзакции с помощью /transactions."
        )
    else:
        return (
            "Ваш баланс приближается к нижнему порогу. Рекомендации:\n"
            "- Следите за расходами с помощью /budget.\n"
            "- Подумайте о пополнении счёта.\n"
            "- Используйте /total для общей картины."
        )

def get_user_threshold(user_id):
    """Получение индивидуального порога пользователя из базы данных."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT balance_threshold FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        return float(result[0]) if result and result[0] is not None else 1000.0  # По умолчанию 1000 руб.
    except Exception as e:
        logger.error(f"Ошибка при получении порога для user_id {user_id}: {e}")
        return 1000.0

async def check_low_balance(context: CallbackContext):
    """Проверка балансов пользователя и отправка уведомлений при низком уровне для основного и текущего счетов."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()
        conn.close()

        for (user_id,) in users:
            banks_str, balances_dict, _ = trans_fetch_user_data(user_id)
            if not banks_str or not balances_dict:
                continue

            threshold = get_user_threshold(user_id)  # Получаем индивидуальный порог
            banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()]

            # Проверка основного счёта
            for bank in banks_list:
                balance = float(balances_dict.get(bank, 0))
                if balance < threshold:
                    advice = get_balance_advice(balance, threshold)
                    message = (
                        f"⚠️ Низкий баланс на основном счёте в {bank}!\n"
                        f"Текущий баланс: {balance:,.2f} руб.\n"
                        f"Ваш порог: {threshold:,.2f} руб.\n\n"
                        f"{advice}"
                    )
                    try:
                        await context.bot.send_message(chat_id=user_id, text=message)
                        logger.info(f"Отправлено уведомление о низком балансе пользователю {user_id} для основного счёта в {bank}")
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления пользователю {user_id} для основного счёта в {bank}: {e}")

            # Проверка текущих счетов
            accounts = get_accounts(user_id)
            for account_type, balance in accounts:
                if account_type == "Текущий счёт":  # Проверяем только текущие счета
                    balance = float(balance)
                    if balance < threshold:
                        bank_name = [acc[0] for acc in get_accounts(user_id) if acc[1] == balance and acc[0] == account_type][0]
                        advice = get_balance_advice(balance, threshold)
                        message = (
                            f"⚠️ Низкий баланс на текущем счёте в {bank_name}!\n"
                            f"Текущий баланс: {balance:,.2f} руб.\n"
                            f"Ваш порог: {threshold:,.2f} руб.\n\n"
                            f"{advice}"
                        )
                        try:
                            await context.bot.send_message(chat_id=user_id, text=message)
                            logger.info(f"Отправлено уведомление о низком балансе пользователю {user_id} для текущего счёта в {bank_name}")
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления пользователю {user_id} для текущего счёта в {bank_name}: {e}")

    except sqlite3.Error as e:
        logger.error(f"Ошибка при проверке балансов: {e}")
    except Exception as e:
        logger.error(f"Неизвестная ошибка в check_low_balance: {e}")

async def setthreshold(update: Update, context: CallbackContext):
    """Команда для установки индивидуального порога низкого баланса с пошаговым вводом суммы."""
    user_id = update.effective_user.id

    # Запрашиваем сумму порога
    await update.message.reply_text(
        "Введите сумму порога низкого баланса (например, 5000):",
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data["setthreshold_step"] = "enter_amount"

async def handle_setthreshold_steps(update: Update, context: CallbackContext):
    """Обработка ввода суммы порога для команды /setthreshold."""
    user_id = update.effective_user.id
    step = context.user_data.get("setthreshold_step")
    message_text = update.message.text.strip()

    if step == "enter_amount":
        try:
            threshold = float(message_text)
            if threshold <= 0:
                await update.message.reply_text("Порог должен быть положительным числом. Введите снова:")
                return

            # Сохраняем порог в базе данных
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (user_id, balance_threshold)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET balance_threshold = excluded.balance_threshold
            """, (user_id, threshold))
            conn.commit()

            # Получаем данные о балансах пользователя
            banks_str, balances_dict, _ = trans_fetch_user_data(user_id)

            # Проверяем основные счета
            low_balance_detected = False
            if banks_str and balances_dict:
                banks_list = [bank.strip() for bank in banks_str.split(",") if bank.strip()]
                for bank in banks_list:
                    balance = float(balances_dict.get(bank, 0))
                    if balance < threshold:
                        advice = get_balance_advice(balance, threshold)
                        message = (
                            f"⚠️ Низкий баланс на основном счёте в {bank}!\n"
                            f"Текущий баланс: {balance:,.2f} руб.\n"
                            f"Ваш порог: {threshold:,.2f} руб.\n\n"
                            f"{advice}"
                        )
                        await context.bot.send_message(chat_id=user_id, text=message)
                        logger.info(f"Отправлено немедленное уведомление о низком балансе пользователю {user_id} для основного счёта в {bank}")
                        low_balance_detected = True

                    # Проверяем только текущие счета для этого банка
                    accounts = get_accounts(user_id, bank)
                    for account_type, balance in accounts:
                        if account_type == "Текущий счёт":
                            balance = float(balance)
                            if balance < threshold:
                                advice = get_balance_advice(balance, threshold)
                                message = (
                                    f"⚠️ Низкий баланс на текущем счёте в {bank}!\n"
                                    f"Текущий баланс: {balance:,.2f} руб.\n"
                                    f"Ваш порог: {threshold:,.2f} руб.\n\n"
                                    f"{advice}"
                                )
                                await context.bot.send_message(chat_id=user_id, text=message)
                                logger.info(f"Отправлено немедленное уведомление о низком балансе пользователю {user_id} для текущего счёта в {bank}")
                                low_balance_detected = True

            conn.close()

            # Отправляем подтверждение установки порога
            await update.message.reply_text(f"Порог низкого баланса установлен: {threshold:,.2f} руб.")
            logger.info(f"Пользователь {user_id} установил порог низкого баланса: {threshold}")

            if not low_balance_detected and banks_str:
                await update.message.reply_text("Все ваши основные и текущие счета выше установленного порога.")

            # Очищаем состояние
            context.user_data.pop("setthreshold_step", None)

        except ValueError:
            await update.message.reply_text("Сумма должна быть числом. Введите снова:")
        except sqlite3.Error as e:
            logger.error(f"Ошибка при установке порога для user_id {user_id}: {e}")
            await update.message.reply_text("Ошибка при сохранении порога. Попробуйте позже.")
        except Exception as e:
            logger.error(f"Неизвестная ошибка в /setthreshold для user_id {user_id}: {e}")
            await update.message.reply_text("Произошла ошибка. Попробуйте снова.")

def setup_balance_alerts(app):
    """Настройка периодической проверки балансов и добавление команды /setthreshold."""
    app.job_queue.run_repeating(check_low_balance, interval=86400, first=10)  # Проверка раз в сутки
    app.add_handler(CommandHandler("setthreshold", setthreshold))  # Добавляем команду
    logger.info("Уведомления о низком балансе и команда /setthreshold настроены с интервалом 24 часа.")