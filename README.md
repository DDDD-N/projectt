# Finance Bot + Telegram Mini App

Telegram-бот учёта финансов (SQLite) и мини-приложение для просмотра счетов, истории, кредитов и бюджета.

```
bot/      код бота (запуск: python bot/bot.py)
api.py    FastAPI: /api/summary (проверка подписи initData) + раздача webapp/
webapp/   Mini App (index.html)
```

## Запуск
```bash
pip install -r requirements.txt
cp .env.example .env        # заполните BOT_TOKEN, WEBAPP_URL
python bot/bot.py           # бот
uvicorn api:app --port 8000 # мини-приложение (тот же DB_PATH)
```
Mini App требует публичный **HTTPS**-адрес: для теста `cloudflared tunnel --url http://localhost:8000`,
для продакшена — VPS/Render/Railway/Fly.io. Укажите адрес в `WEBAPP_URL` — бот сам поставит кнопку меню «Открыть».
(Либо вручную: @BotFather → /mybots → Bot Settings → Menu Button.)

## Безопасность
Токен только в `.env` (в git не попадает). Файл `*.db` тоже в `.gitignore`.
