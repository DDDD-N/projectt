"""API + статика для Telegram Mini App (только чтение базы бота)."""
import ast, hashlib, hmac, json, os, sqlite3, time
from urllib.parse import parse_qsl
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles

load_dotenv()
BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.path.abspath(os.getenv("DB_PATH", "users.db"))
app = FastAPI()


def verify_init_data(init_data: str) -> int:
    """Проверка подписи Telegram initData, возвращает user_id."""
    data = dict(parse_qsl(init_data, keep_blank_values=True))
    received = data.pop("hash", None)
    if not received:
        raise HTTPException(401, "no hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        raise HTTPException(401, "bad signature")
    if time.time() - int(data.get("auth_date", 0)) > 86400:
        raise HTTPException(401, "expired")
    return json.loads(data["user"])["id"]


def rows(conn, sql, args):
    cur = conn.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


@app.get("/api/summary")
def summary(x_init_data: str = Header("")):
    uid = verify_init_data(x_init_data)
    with sqlite3.connect(DB_PATH) as c:
        u = c.execute("SELECT banks, balances FROM users WHERE user_id=?", (uid,)).fetchone()
        banks = [b.strip() for b in (u[0] or "").split(",") if b.strip()] if u else []
        bal = ast.literal_eval(u[1]) if u and u[1] else {}
        main = [{"bank": b, "balance": float(bal.get(b, 0))} for b in banks]
        return {
            "main": main,
            "accounts": rows(c, "SELECT bank_name, account_type, balance FROM accounts WHERE user_id=?", (uid,)),
            "transactions": rows(c, "SELECT amount, category, trans_type, bank_name, date FROM transactions WHERE user_id=? ORDER BY date DESC LIMIT 50", (uid,)),
            "credits": rows(c, "SELECT bank_name, amount, monthly_payment, remaining_amount, payment_day FROM credits WHERE user_id=?", (uid,)),
            "budgets": rows(c, "SELECT category, amount FROM budgets WHERE user_id=?", (uid,)),
        }


app.mount("/", StaticFiles(directory="webapp", html=True), name="web")
