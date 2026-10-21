import sqlite3

# Функция для установки или обновления PIN-кода
def set_pin(user_id: int, pin: str, db_path: str) -> bool:
    """
    Устанавливает или обновляет PIN-код для пользователя.
    Возвращает True, если успешно, иначе False.
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Проверяем, существует ли колонка pin в таблице users
        cursor.execute("PRAGMA table_info(users);")
        columns = [column[1] for column in cursor.fetchall()]
        if "pin" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN pin TEXT")

        # Сохраняем PIN-код
        cursor.execute("""
            INSERT INTO users (user_id, pin) 
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET pin = excluded.pin
        """, (user_id, pin))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Ошибка при установке PIN-кода: {e}")
        return False

# Функция для проверки PIN-кода
def check_pin(user_id: int, pin: str, db_path: str) -> bool:
    """
    Проверяет, совпадает ли введённый PIN-код с сохранённым.
    Возвращает True, если PIN верный или его нет, иначе False.
    """
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
        print(f"Ошибка при проверке PIN-кода: {e}")
        return False

# Функция для удаления аккаунта и всех данных
def delete_account(user_id: int, db_path: str) -> bool:
    """
    Удаляет все данные пользователя из базы.
    Возвращает True, если успешно, иначе False.
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Ошибка при удалении аккаунта: {e}")
        return False

if __name__ == "__main__":
    # Тестовый пример
    db_path = "users.db"
    user_id = 12345
    print(set_pin(user_id, "1234", db_path))  # Установить PIN
    print(check_pin(user_id, "1234", db_path))  # Проверить PIN
    print(check_pin(user_id, "4321", db_path))  # Неверный PIN
    print(delete_account(user_id, db_path))  # Удалить аккаунт