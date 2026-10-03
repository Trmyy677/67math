import os
import re
import json
import math
import random
import sqlite3
import hashlib
import secrets
import html
import base64
from pathlib import Path
from datetime import datetime, date, timedelta
from urllib.parse import quote

import gradio as gr

# ============================================================
# 🌸 67MATH — ENGLISH MATH LEARNING
#
# Features:
#   1. OXYZ 3D simulator
#   2. Accounts + persistent sync (SQLite)
#   3. Anki-like custom vocabulary/decks
#   4. Spaced repetition + daily/weekly/monthly tracking
#   5. Copy/share/import decks
#   6. Sound via browser SpeechSynthesis (no API key needed)
#   7. Random MCQ + numerical exercises
#   8. Integer / 1-decimal answer validation
#   9. AI explanations + tutor chatbot via OpenAI Responses API
#
# Environment variables:
#   OPENAI_API_KEY   = optional
#   OPENAI_MODEL     = optional, default "gpt-6-luna"
#   DB_PATH          = optional, default "67math.db"
#
# Install:
#   pip install gradio openai
#
# Run:
#   python app.py
#
# For real multi-device sync, deploy this app with a persistent DB volume.
# SQLite is fine for a personal/small deployment. For many users, migrate
# the same schema to PostgreSQL.
# ============================================================

DB_PATH = os.getenv("DB_PATH", "67math.db")
# ========================= AI CONFIG =========================
# Put your OpenAI key HERE. Do NOT put it in the web UI.
# Example: OPENAI_API_KEY = "sk-xxxxxxxxxxxxxxxx"
# Never commit your OpenAI API key. Set it as a deployment secret/environment variable.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
# The API key is intentionally configured in this Python file, not exposed in the frontend.
AI_CUSTOM_PROMPT = """You are 67Math Tutor, a friendly high-school mathematics tutor.
Explain step-by-step, check calculations carefully, use clean Markdown and LaTeX, and never output raw HTML.
Use Vietnamese when the student writes Vietnamese and English when the student writes English.
When an image is provided, inspect the image and solve/explain the visible problem carefully.
"""


# ============================================================
# DATABASE
# ============================================================

def db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS decks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        share_code TEXT UNIQUE,
        created_at TEXT NOT NULL,
        FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deck_id INTEGER NOT NULL,
        word TEXT NOT NULL,
        meaning TEXT DEFAULT '',
        pronunciation TEXT DEFAULT '',
        example TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        tags TEXT DEFAULT '',
        ease REAL DEFAULT 2.5,
        interval_days INTEGER DEFAULT 0,
        repetitions INTEGER DEFAULT 0,
        due_date TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(deck_id) REFERENCES decks(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        card_id INTEGER,
        deck_id INTEGER,
        rating TEXT NOT NULL,
        reviewed_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE SET NULL,
        FOREIGN KEY(deck_id) REFERENCES decks(id) ON DELETE SET NULL
    );

    CREATE TABLE IF NOT EXISTS exercise_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        topic TEXT NOT NULL,
        difficulty TEXT NOT NULL,
        question_type TEXT NOT NULL,
        correct INTEGER NOT NULL,
        answer TEXT,
        expected TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS usage_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        total_seconds INTEGER DEFAULT 0,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)
    # Lightweight migrations for profile fields.
    existing_cols = {r["name"] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    for col, typ in [("full_name", "TEXT DEFAULT ''"), ("dob", "TEXT DEFAULT ''"), ("profile_pic", "TEXT DEFAULT ''")]:
        if col not in existing_cols:
            con.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
    con.commit()
    con.close()


init_db()


# ============================================================
# AUTH
# ============================================================

def now():
    return datetime.now().isoformat(timespec="seconds")


def today():
    return date.today().isoformat()


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 180_000
    ).hex()
    return f"{salt}${digest}"


def verify_password(password, stored):
    try:
        salt, digest = stored.split("$", 1)
        check = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), 180_000
        ).hex()
        return secrets.compare_digest(check, digest)
    except Exception:
        return False


def register(username, password):
    username = (username or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        return "❌ Username: 3–32 ký tự, chỉ dùng chữ/số/._-", None
    if len(password or "") < 6:
        return "❌ Password cần ít nhất 6 ký tự.", None

    con = db()
    try:
        con.execute(
            "INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
            (username, hash_password(password), now()),
        )
        con.commit()
        uid = con.execute(
            "SELECT id FROM users WHERE username=?", (username,)
        ).fetchone()["id"]
        return f"✅ Tạo account @{username} thành công. Đã đăng nhập.", uid
    except sqlite3.IntegrityError:
        return "❌ Username đã tồn tại.", None
    finally:
        con.close()


def login(username, password):
    con = db()
    row = con.execute(
        "SELECT * FROM users WHERE username=?", ((username or "").strip(),)
    ).fetchone()
    con.close()
    if not row or not verify_password(password or "", row["password_hash"]):
        return "❌ Sai username hoặc password.", None
    return f"✅ Đăng nhập @{row['username']}. Dữ liệu sẽ sync theo account.", row["id"]


def logout():
    return "Đã đăng xuất.", None


def auth_text(user_id):
    if not user_id:
        return "🔒 Not logged in"
    con = db()
    row = con.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    con.close()
    return f"👤 @{row['username']}" if row else "🔒 Not logged in"


def get_profile(user_id):
    if not user_id:
        return "", "", "", None
    con = db()
    row = con.execute("SELECT username,full_name,dob,profile_pic FROM users WHERE id=?", (user_id,)).fetchone()
    con.close()
    if not row:
        return "", "", "", None
    pic = None
    if row["profile_pic"]:
        pic = row["profile_pic"]
    return row["username"], row["full_name"] or "", row["dob"] or "", pic


def profile_preview_html(profile_pic):
    if not profile_pic:
        return "<div class='profile-card'><div class='profile-avatar' style='display:flex;align-items:center;justify-content:center;font-size:34px'>👤</div><div><b>No profile picture</b><br><span class='muted'>Upload one below.</span></div></div>"
    return f"<div class='profile-card'><img class='profile-avatar' src='{html.escape(profile_pic, quote=True)}'><div><b>Profile picture</b><br><span class='muted'>Your saved profile photo.</span></div></div>"


def save_profile(user_id, full_name, dob, profile_pic):
    if not user_id:
        return "🔒 Login first.", "", "", profile_preview_html(None)
    pic_data = ""
    if profile_pic:
        try:
            raw = Path(profile_pic).read_bytes()
            if len(raw) > 2_000_000:
                return "❌ Profile picture must be under 2 MB.", full_name or "", dob or "", profile_preview_html(None)
            ext = Path(profile_pic).suffix.lower().replace(".", "") or "png"
            mime = "jpeg" if ext in ("jpg", "jpeg") else ext
            pic_data = f"data:image/{mime};base64," + base64.b64encode(raw).decode("ascii")
        except Exception:
            pic_data = ""
    con = db()
    if pic_data:
        con.execute("UPDATE users SET full_name=?,dob=?,profile_pic=? WHERE id=?", ((full_name or "").strip()[:120], (dob or "").strip()[:30], pic_data, user_id))
    else:
        con.execute("UPDATE users SET full_name=?,dob=? WHERE id=?", ((full_name or "").strip()[:120], (dob or "").strip()[:30], user_id))
    con.commit()
    con.close()
    _, name, birth, pic = get_profile(user_id)
    return "✅ Profile saved.", name, birth, profile_preview_html(pic)


# ============================================================
# DECKS / VOCAB
# ============================================================

DEFAULT_VOCAB = {
    "Algebra": [
        ("coefficient","hệ số","/ˌkoʊəˈfɪʃənt/","In 3x², 3 is the coefficient of x²."),
        ("quadratic equation","phương trình bậc hai","/kwɒˈdrætɪk ɪˈkweɪʒən/","A quadratic equation has degree 2."),
        ("factor","thừa số / nhân tử","/ˈfæktər/","2 and 3 are factors of 6."),
        ("variable","biến","/ˈveəriəbəl/","x is a variable."),
        ("constant","hằng số","/ˈkɒnstənt/","5 is a constant."),
        ("inequality","bất phương trình / bất đẳng thức","/ˌɪnɪˈkwɒləti/","Solve the inequality x > 3."),
        ("discriminant","biệt thức","/dɪˈskrɪmɪnənt/","The discriminant determines the number of roots."),
        ("root","nghiệm","/ruːt/","x = 2 is a root of the equation."),
    ],
    "Geometry": [
        ("perpendicular","vuông góc","/ˌpɜːrpənˈdɪkjələr/","Two lines are perpendicular if they meet at 90 degrees."),
        ("parallel","song song","/ˈpærəlel/","These two lines are parallel."),
        ("bisector","đường phân giác","/baɪˈsektər/","The angle bisector divides an angle into two equal angles."),
        ("circumcenter","tâm đường tròn ngoại tiếp","/ˈsɜːrkəmˌsentər/","The circumcenter is equidistant from the vertices."),
        ("centroid","trọng tâm","/ˈsentrɔɪd/","The centroid is the intersection of the medians."),
        ("radius","bán kính","/ˈreɪdiəs/","The radius is half the diameter."),
        ("diameter","đường kính","/daɪˈæmɪtər/","The diameter passes through the center."),
    ],
    "Calculus": [
        ("derivative","đạo hàm","/dɪˈrɪvətɪv/","The derivative describes the rate of change."),
        ("integral","tích phân","/ˈɪntɪɡrəl/","We can use an integral to find area."),
        ("limit","giới hạn","/ˈlɪmɪt/","Find the limit as x approaches zero."),
        ("continuous","liên tục","/kənˈtɪnjuəs/","The function is continuous on this interval."),
        ("increasing","đồng biến","/ɪnˈkriːsɪŋ/","The function is increasing on this interval."),
        ("decreasing","nghịch biến","/dɪˈkriːsɪŋ/","The function is decreasing on this interval."),
        ("maximum","giá trị lớn nhất / cực đại","/ˈmæksɪməm/","Find the maximum value of the function."),
        ("minimum","giá trị nhỏ nhất / cực tiểu","/ˈmɪnɪməm/","The minimum occurs at x = 2."),
    ],
    "Oxyz": [
        ("coordinate","tọa độ","/koʊˈɔːrdɪnət/","The point has coordinates (2, 1, -3)."),
        ("vector","vectơ","/ˈvektər/","Find the vector AB."),
        ("magnitude","độ dài / độ lớn","/ˈmæɡnɪtuːd/","The magnitude of the vector is 5."),
        ("midpoint","trung điểm","/ˈmɪdpɔɪnt/","Find the midpoint of AB."),
        ("distance","khoảng cách","/ˈdɪstəns/","Calculate the distance between two points."),
        ("plane","mặt phẳng","/pleɪn/","Find the equation of the plane."),
        ("line","đường thẳng","/laɪn/","Find the equation of the line."),
        ("normal vector","vectơ pháp tuyến","/ˈnɔːrməl ˈvektər/","A normal vector is perpendicular to the plane."),
    ],
}


def ensure_default_decks(user_id):
    """Seed the built-in vocabulary for every account, including old accounts.
    Existing custom decks are preserved. Missing built-in categories are added only once.
    """
    if not user_id:
        return
    con = db()
    existing = {
        r["name"] for r in con.execute(
            "SELECT name FROM decks WHERE owner_id=?", (user_id,)
        ).fetchall()
    }
    changed = False
    for category, words in DEFAULT_VOCAB.items():
        if category in existing:
            continue
        cur = con.execute(
            "INSERT INTO decks(owner_id,name,description,share_code,created_at) VALUES(?,?,?,?,?)",
            (user_id, category, f"Built-in {category} vocabulary", secrets.token_urlsafe(8), now())
        )
        deck_id = cur.lastrowid
        for word, meaning, pron, example in words:
            con.execute("""
                INSERT INTO cards(deck_id,word,meaning,pronunciation,example,due_date,created_at)
                VALUES(?,?,?,?,?,?,?)
            """, (deck_id, word, meaning, pron, example, today(), now()))
        changed = True
    if changed:
        con.commit()
    con.close()


def deck_choices(user_id):
    if not user_id:
        return []
    ensure_default_decks(user_id)
    con = db()
    rows = con.execute(
        "SELECT id,name FROM decks WHERE owner_id=? ORDER BY name", (user_id,)
    ).fetchall()
    con.close()
    return [f"{r['id']} — {r['name']}" for r in rows]


def parse_id(choice):
    try:
        return int(str(choice).split(" — ", 1)[0])
    except Exception:
        return None


def create_deck(user_id, name, description):
    if not user_id:
        return "🔒 Đăng nhập trước.", gr.update()
    name = (name or "").strip()
    if not name:
        return "❌ Tên deck trống.", gr.update()

    con = db()
    code = secrets.token_urlsafe(9)
    con.execute(
        "INSERT INTO decks(owner_id,name,description,share_code,created_at) VALUES(?,?,?,?,?)",
        (user_id, name[:80], (description or "")[:500], code, now()),
    )
    con.commit()
    con.close()
    choices = deck_choices(user_id)
    return f"✅ Đã tạo deck **{name}**.", gr.update(choices=choices, value=choices[-1] if choices else None)


def add_card(user_id, deck_choice, word, meaning, pronunciation, example, notes, tags):
    deck_id = parse_id(deck_choice)
    if not user_id:
        return "🔒 Đăng nhập trước.", gr.update()
    if not deck_id:
        return "❌ Chọn deck.", gr.update()
    if not (word or "").strip():
        return "❌ Nhập từ vựng.", gr.update()

    con = db()
    owner = con.execute(
        "SELECT owner_id FROM decks WHERE id=?", (deck_id,)
    ).fetchone()
    if not owner or owner["owner_id"] != user_id:
        con.close()
        return "❌ Deck không thuộc account này.", gr.update()

    con.execute("""
        INSERT INTO cards(deck_id,word,meaning,pronunciation,example,notes,tags,due_date,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)
    """, (
        deck_id, word.strip(), (meaning or "").strip(), (pronunciation or "").strip(),
        (example or "").strip(), (notes or "").strip(), (tags or "").strip(),
        today(), now()
    ))
    con.commit()
    con.close()
    return "✅ Đã thêm card.", refresh_deck_cards(user_id, deck_choice)[0]


def refresh_deck_cards(user_id, deck_choice):
    deck_id = parse_id(deck_choice)
    if not user_id or not deck_id:
        return "<div class='muted'>Chọn account + deck.</div>", ""
    con = db()
    rows = con.execute("""
        SELECT id,word,meaning,pronunciation,example,tags,due_date,interval_days,repetitions
        FROM cards WHERE deck_id=? ORDER BY id DESC
    """, (deck_id,)).fetchall()
    con.close()
    if not rows:
        return "<div class='empty'>Deck chưa có từ nào.</div>", ""
    blocks = []
    for r in rows:
        word = html.escape(r['word'] or '')
        speech_word = json.dumps(r['word'] or '')
        pron = html.escape(r['pronunciation'] or '')
        blocks.append(f"""
        <div class='card-row vocab-card'>
          <div class='vocab-head'>
            <div><b>{word}</b>
              <span class='tag'>{html.escape(r['tags'] or '')}</span>
            </div>
            <button class='sound-btn' title='Listen / Nghe' onclick="play67Sound({speech_word})">🔊</button>
          </div>
          <div>{html.escape(r['meaning'] or '')}</div>
          <div class='pron'>{pron}</div>
          <div class='example'>{html.escape(r['example'] or '')}</div>
          <small>Due: {r['due_date']} · interval {r['interval_days']}d · reps {r['repetitions']}</small>
        </div>
        """)
    return "".join(blocks), ""


def share_deck(user_id, deck_choice):
    deck_id = parse_id(deck_choice)
    if not user_id or not deck_id:
        return "🔒 Chọn deck sau khi đăng nhập."
    con = db()
    row = con.execute(
        "SELECT name,share_code FROM decks WHERE id=? AND owner_id=?",
        (deck_id, user_id)
    ).fetchone()
    con.close()
    if not row:
        return "❌ Không tìm thấy deck."
    return f"**Share code:** `{row['share_code']}`\n\nNgười khác có thể nhập code này để copy deck."


def copy_shared_deck(user_id, share_code):
    if not user_id:
        return "🔒 Đăng nhập trước.", gr.update()
    code = (share_code or "").strip()
    con = db()
    src = con.execute(
        "SELECT * FROM decks WHERE share_code=?", (code,)
    ).fetchone()
    if not src:
        con.close()
        return "❌ Share code không tồn tại.", gr.update()

    cur = con.execute(
        "INSERT INTO decks(owner_id,name,description,share_code,created_at) VALUES(?,?,?,?,?)",
        (user_id, src["name"] + " (copy)", src["description"], secrets.token_urlsafe(9), now())
    )
    new_id = cur.lastrowid
    cards = con.execute("SELECT * FROM cards WHERE deck_id=?", (src["id"],)).fetchall()
    for c in cards:
        con.execute("""
            INSERT INTO cards(deck_id,word,meaning,pronunciation,example,notes,tags,
                              ease,interval_days,repetitions,due_date,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            new_id,c["word"],c["meaning"],c["pronunciation"],c["example"],c["notes"],c["tags"],
            2.5,0,0,today(),now()
        ))
    con.commit()
    con.close()
    choices = deck_choices(user_id)
    return f"✅ Đã copy deck **{src['name']}**.", gr.update(choices=choices, value=choices[-1] if choices else None)


# ============================================================
# SPACED REPETITION
# ============================================================

def review_card(user_id, card_id, rating):
    if not user_id or not card_id:
        return "🔒 Đăng nhập trước.", refresh_review(user_id)

    con = db()
    card = con.execute(
        "SELECT * FROM cards WHERE id=?", (int(card_id),)
    ).fetchone()
    if not card:
        con.close()
        return "❌ Card không tồn tại.", refresh_review(user_id)

    ease = float(card["ease"])
    reps = int(card["repetitions"])
    interval = int(card["interval_days"])

    # Lightweight SM-2 style scheduling:
    # Again -> reset; Hard -> shorter interval; Good -> normal growth;
    # Easy -> stronger growth.
    if rating == "Again":
        reps = 0
        interval = 1
        ease = max(1.3, ease - 0.20)
    elif rating == "Hard":
        reps += 1
        interval = max(1, round(interval * 1.2)) if interval else 1
        ease = max(1.3, ease - 0.05)
    elif rating == "Good":
        reps += 1
        interval = 1 if interval == 0 else max(2, round(interval * ease))
    else:  # Easy
        reps += 1
        interval = 2 if interval == 0 else max(3, round(interval * ease * 1.3))
        ease += 0.05

    due = (date.today() + timedelta(days=interval)).isoformat()

    con.execute("""
        UPDATE cards SET ease=?,interval_days=?,repetitions=?,due_date=?
        WHERE id=?
    """, (ease, interval, reps, due, card["id"]))
    con.execute("""
        INSERT INTO reviews(user_id,card_id,deck_id,rating,reviewed_at)
        VALUES(?,?,?,?,?)
    """, (user_id, card["id"], card["deck_id"], rating, now()))
    con.commit()
    con.close()
    return f"✅ {rating} → ôn lại sau **{interval} ngày**.", refresh_review(user_id)


def get_due_cards(user_id, deck_id=None):
    if not user_id:
        return []
    con = db()
    if deck_id:
        rows = con.execute("""
            SELECT * FROM cards
            WHERE deck_id=? AND due_date<=?
            ORDER BY due_date,id
        """, (deck_id, today())).fetchall()
    else:
        rows = con.execute("""
            SELECT c.* FROM cards c
            JOIN decks d ON d.id=c.deck_id
            WHERE d.owner_id=? AND c.due_date<=?
            ORDER BY c.due_date,c.id
        """, (user_id, today())).fetchall()
    con.close()
    return rows


def review_card_html(card):
    if not card:
        return "<div class='flashcard'><div class='muted'>🎉 No cards due today.</div></div>"
    pronunciation = html.escape(card["pronunciation"] or "")
    return f"""
    <div class='flashcard flip-card' onclick='this.classList.toggle("flipped")'>
      <div class='flash-front'>
        <div class='tiny'>FLASHCARD · CLICK TO FLIP</div>
        <div class='bigword'>{html.escape(card['word'])}</div>
        <div class='pron'>{pronunciation}</div>
        <button class='sound-btn big-sound' onclick='event.stopPropagation(); play67Sound({json.dumps(card['word'])})'>🔊 Listen</button>
        <div class='flip-hint'>Click the card to reveal the meaning</div>
      </div>
      <div class='flash-back'>
        <div class='tiny'>MEANING</div>
        <h2>{html.escape(card['meaning'] or '')}</h2>
        <p class='example'>{html.escape(card['example'] or '')}</p>
        <p class='muted'>{html.escape(card['notes'] or '')}</p>
      </div>
    </div>
    """


def refresh_review(user_id, deck_choice=None):
    deck_id = parse_id(deck_choice)
    rows = get_due_cards(user_id, deck_id)
    card = rows[0] if rows else None
    card_id = card["id"] if card else None
    return review_card_html(card), card_id


def review_next(user_id, deck_choice, current_card_id):
    rows = get_due_cards(user_id, parse_id(deck_choice))
    ids = [r["id"] for r in rows]
    if current_card_id in ids:
        idx = ids.index(current_card_id)
        card = rows[idx + 1] if idx + 1 < len(rows) else None
    else:
        card = rows[0] if rows else None
    return review_card_html(card), (card["id"] if card else None)


# ============================================================
# VOCAB RANDOM TEST
# ============================================================

def vocab_test_start(user_id, deck_choice, count):
    if not user_id:
        return "🔒 Login first.", [], 0, gr.update(choices=[], value=None), ""
    deck_id = parse_id(deck_choice)
    con = db()
    if deck_id:
        rows = con.execute("SELECT * FROM cards WHERE deck_id=?", (deck_id,)).fetchall()
    else:
        rows = con.execute("SELECT c.* FROM cards c JOIN decks d ON d.id=c.deck_id WHERE d.owner_id=?", (user_id,)).fetchall()
    con.close()
    rows = list(rows)
    if not rows:
        return "No vocabulary available.", [], 0, gr.update(choices=[], value=None), ""
    n = max(5, min(10, int(count)))
    chosen = random.sample(rows, min(n, len(rows)))
    items=[]
    all_words=[r["word"] for r in rows]
    for r in chosen:
        distractors=random.sample([w for w in all_words if w != r["word"]], min(3, max(0,len(all_words)-1)))
        opts=distractors+[r["word"]]
        random.shuffle(opts)
        items.append({"id":r["id"],"word":r["word"],"meaning":r["meaning"],"options":opts})
    q=items[0]
    return f"**1 / {len(items)}** — Choose the English word for: **{q['meaning']}**", items, 0, gr.update(choices=q["options"], value=None), ""


def vocab_test_submit(user_id, items, idx, answer):
    if not items or idx >= len(items):
        return "Start a test first.", idx, gr.update(choices=[]), ""
    item=items[idx]
    correct = str(answer or "").strip().lower() == str(item["word"]).strip().lower()
    feedback = "### ✅ Correct!" if correct else f"### ❌ Not quite\nCorrect answer: **{item['word']}**"
    nxt=idx+1
    if nxt>=len(items):
        return feedback+"\n\n🎉 **Test complete!**", nxt, gr.update(choices=[]), ""
    q=items[nxt]
    return feedback+f"\n\n**{nxt+1} / {len(items)}** — Choose the English word for: **{q['meaning']}**", nxt, gr.update(choices=q["options"], value=None), ""


# ============================================================
# ONLINE TIME / DASHBOARD
# ============================================================

def start_online_session(user_id):
    if not user_id:
        return None
    con = db()
    stamp = now()
    cur = con.execute(
        "INSERT INTO usage_sessions(user_id,started_at,last_seen,total_seconds) VALUES(?,?,?,0)",
        (user_id, stamp, stamp)
    )
    con.commit()
    sid = cur.lastrowid
    con.close()
    return sid


def touch_online(user_id, session_id):
    if not user_id or not session_id:
        return
    con = db()
    row = con.execute("SELECT last_seen FROM usage_sessions WHERE id=? AND user_id=?", (session_id, user_id)).fetchone()
    if row:
        try:
            elapsed = max(0, min(120, int((datetime.fromisoformat(now()) - datetime.fromisoformat(row["last_seen"])).total_seconds())))
        except Exception:
            elapsed = 0
        con.execute("UPDATE usage_sessions SET total_seconds=total_seconds+?,last_seen=? WHERE id=?", (elapsed, now(), session_id))
        con.commit()
    con.close()


def format_minutes(seconds):
    minutes = max(0, int(seconds or 0) // 60)
    return f"{minutes // 60}h {minutes % 60:02d}m"


def dashboard_html(user_id):
    if not user_id:
        return "<div class='empty'>🔒 Đăng nhập để xem Dashboard.</div>"
    con = db()
    dates = con.execute("""
      SELECT DISTINCT d FROM (
        SELECT date(reviewed_at) d FROM reviews WHERE user_id=?
        UNION SELECT date(created_at) d FROM exercise_attempts WHERE user_id=?
        UNION SELECT date(created_at) d FROM chat_messages WHERE user_id=?
      ) ORDER BY d DESC
    """, (user_id, user_id, user_id)).fetchall()
    active_dates = {r["d"] for r in dates}
    streak = 0
    cur = date.today()
    while cur.isoformat() in active_dates:
        streak += 1
        cur -= timedelta(days=1)
    total_sec = con.execute("SELECT COALESCE(SUM(total_seconds),0) n FROM usage_sessions WHERE user_id=?", (user_id,)).fetchone()["n"]
    today_sec = con.execute("SELECT COALESCE(SUM(total_seconds),0) n FROM usage_sessions WHERE user_id=? AND date(started_at)=?", (user_id, today())).fetchone()["n"]
    reviews = con.execute("SELECT COUNT(*) n FROM reviews WHERE user_id=?", (user_id,)).fetchone()["n"]
    ex_total = con.execute("SELECT COUNT(*) n FROM exercise_attempts WHERE user_id=?", (user_id,)).fetchone()["n"]
    ex_correct = con.execute("SELECT COALESCE(SUM(correct),0) n FROM exercise_attempts WHERE user_id=?", (user_id,)).fetchone()["n"]
    con.close()
    accuracy = round(ex_correct / ex_total * 100) if ex_total else 0
    bars = []
    for i in range(29, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        cls = "active" if d in active_dates else ""
        bars.append(f"<span class='activity-cell {cls}' title='{d}'></span>")
    return f"""
    <div class='dashboard'>
      <div class='dash-hero'><div><div class='tiny'>67MATH DASHBOARD</div><h2>📊 Your learning activity</h2><p>Codeforces-style progress overview for your account.</p></div><div class='streak'>🔥 <b>{streak}</b><span>day streak</span></div></div>
      <div class='dash-stats'>
        <div class='dash-stat'><b>{streak}</b><span>🔥 Streak</span></div>
        <div class='dash-stat'><b>{format_minutes(today_sec)}</b><span>⏱ Today online</span></div>
        <div class='dash-stat'><b>{format_minutes(total_sec)}</b><span>⏱ Total online</span></div>
        <div class='dash-stat'><b>{reviews}</b><span>🧠 Reviews</span></div>
        <div class='dash-stat'><b>{ex_total}</b><span>📝 Exercises</span></div>
        <div class='dash-stat'><b>{accuracy}%</b><span>🎯 Accuracy</span></div>
      </div>
      <div class='activity-panel'><h3>📅 Activity — last 30 days</h3><div class='activity-grid'>{''.join(bars)}</div><small>Each active square = at least one study action.</small></div>
    </div>"""


# ============================================================
# TRACKING
# ============================================================

def tracking_html(user_id):
    if not user_id:
        return "<div class='empty'>🔒 Đăng nhập để xem progress.</div>"

    con = db()
    total = con.execute("""
        SELECT COUNT(*) n FROM cards c
        JOIN decks d ON d.id=c.deck_id WHERE d.owner_id=?
    """, (user_id,)).fetchone()["n"]

    due = con.execute("""
        SELECT COUNT(*) n FROM cards c
        JOIN decks d ON d.id=c.deck_id
        WHERE d.owner_id=? AND c.due_date<=?
    """, (user_id, today())).fetchone()["n"]

    today_reviews = con.execute("""
        SELECT COUNT(*) n FROM reviews WHERE user_id=? AND date(reviewed_at)=?
    """, (user_id, today())).fetchone()["n"]

    week_reviews = con.execute("""
        SELECT COUNT(*) n FROM reviews
        WHERE user_id=? AND date(reviewed_at)>=date('now','-6 day')
    """, (user_id,)).fetchone()["n"]

    month_reviews = con.execute("""
        SELECT COUNT(*) n FROM reviews
        WHERE user_id=? AND date(reviewed_at)>=date('now','-29 day')
    """, (user_id,)).fetchone()["n"]

    ex_total = con.execute(
        "SELECT COUNT(*) n FROM exercise_attempts WHERE user_id=?", (user_id,)
    ).fetchone()["n"]
    ex_correct = con.execute(
        "SELECT COALESCE(SUM(correct),0) n FROM exercise_attempts WHERE user_id=?", (user_id,)
    ).fetchone()["n"]
    con.close()

    accuracy = round(ex_correct / ex_total * 100) if ex_total else 0

    return f"""
    <div class='stats'>
      <div class='stat'><b>{total}</b><span>Total cards</span></div>
      <div class='stat'><b>{due}</b><span>Due today</span></div>
      <div class='stat'><b>{today_reviews}</b><span>Today</span></div>
      <div class='stat'><b>{week_reviews}</b><span>7 days</span></div>
      <div class='stat'><b>{month_reviews}</b><span>30 days</span></div>
      <div class='stat'><b>{accuracy}%</b><span>Exercise accuracy</span></div>
    </div>
    <div class='progress-note'>📅 Review history is stored per account and survives refresh/login.</div>
    """


# ============================================================
# EXERCISE GENERATOR
# ============================================================

def fmt_num(x):
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.1f}"


def numeric_variants(answer):
    return {fmt_num(float(answer)), str(round(float(answer), 1))}


def make_exercise(topic, difficulty, qtype):
    level = difficulty

    if topic == "Linear Equation":
        a = random.randint(2, 9)
        x = random.randint(-8, 10)
        b = random.randint(-12, 12)
        c = a*x+b
        q = f"Solve for x: <b>{a}x {'+' if b >= 0 else '-'} {abs(b)} = {c}</b>"
        ans = float(x)
        explanation = f"Subtract {b} from both sides: {a}x = {c-b}. Then divide by {a}: x = {x}."

    elif topic == "Quadratic Function":
        r1, r2 = random.randint(-6, 6), random.randint(-6, 6)
        b = -(r1+r2)
        c = r1*r2
        q = f"Solve: <b>x² {'+' if b >= 0 else '-'} {abs(b)}x {'+' if c >= 0 else '-'} {abs(c)} = 0</b>"
        ans = float(r1)
        explanation = f"Factor the quadratic as (x - ({r1}))(x - ({r2})) = 0, so the roots are {r1} and {r2}."

    elif topic == "Coordinate Geometry":
        x1,y1 = random.randint(-8,8),random.randint(-8,8)
        x2,y2 = random.randint(-8,8),random.randint(-8,8)
        if qtype == "mcq":
            q = f"Find AB² for A({x1},{y1}) and B({x2},{y2})."
            ans = float((x2-x1)**2+(y2-y1)**2)
            explanation = f"AB² = ({x2}-{x1})² + ({y2}-{y1})² = {int(ans)}."
        else:
            q = f"Find the midpoint x-coordinate of A({x1},{y1}) and B({x2},{y2})."
            ans = (x1+x2)/2
            explanation = f"Midpoint x = (x₁+x₂)/2 = ({x1}+{x2})/2 = {fmt_num(ans)}."

    elif topic == "Statistics":
        nums = [random.randint(2,20) for _ in range(5)]
        ans = sum(nums)/5
        q = f"Find the mean of <b>{nums}</b>."
        explanation = f"Add the five values to get {sum(nums)}, then divide by 5: {fmt_num(ans)}."

    elif topic == "Sequences":
        a1 = random.randint(1,10)
        d = random.randint(2,8)
        n = random.randint(5,12)
        ans = a1+(n-1)*d
        q = f"Arithmetic sequence: <b>a₁={a1}, d={d}</b>. Find a{n}."
        explanation = f"Use aₙ = a₁ + (n−1)d = {a1} + ({n}−1)×{d} = {ans}."

    elif topic == "Derivative":
        a = random.randint(2,9)
        n = random.randint(2,5)
        ans = a*n
        q = f"If <b>f(x)={a}x^{n}</b>, what is the coefficient of x^{n-1} in f'(x)?"
        explanation = f"Power rule: d(axⁿ)/dx = an·xⁿ⁻¹. Therefore the coefficient is {a}×{n} = {ans}."

    elif topic == "Function Optimization":
        a = random.randint(1,6)
        h = random.randint(-5,5)
        k = random.randint(-5,10)
        ans = float(k)
        q = f"Find the maximum value of <b>f(x)=-{a}(x-{h})²+{k}</b>."
        explanation = f"Since (x−{h})² ≥ 0, −{a}(x−{h})² ≤ 0. The largest value occurs when x={h}, giving {k}."

    elif topic == "Integral":
        a = random.randint(1,8)
        n = random.randint(1,4)
        ans = a/(n+1) * (2**(n+1))
        q = f"Evaluate <b>∫₀² {a}x^{n} dx</b>."
        explanation = f"An antiderivative is {a/(n+1):g}x^{n+1}. Evaluate from 0 to 2 to get {fmt_num(ans)}."

    elif topic == "Oxyz":
        x,y,z = [random.randint(-6,6) for _ in range(3)]
        ans = float(x*x+y*y+z*z)
        q = f"Find OA² for <b>A({x},{y},{z})</b>."
        explanation = f"OA² = x²+y²+z² = {x}²+{y}²+{z}² = {int(ans)}."

    else:  # Probability
        red, blue = random.randint(2,8), random.randint(2,8)
        ans = red/(red+blue)
        q = f"A box has {red} red and {blue} blue balls. Find P(red), rounded to 1 decimal."
        ans = round(ans,1)
        explanation = f"P(red) = {red}/({red}+{blue}) = {red/(red+blue):.3f}, which rounds to {ans}."

    # qtype affects presentation; answer remains numeric for reliable validation.
    if qtype == "mcq":
        # Generate numeric distractors.
        opts = {fmt_num(ans)}
        while len(opts) < 4:
            delta = random.choice([-3,-2,-1,1,2,3]) * (0.1 if abs(ans-round(ans)) > 1e-9 else 1)
            candidate = fmt_num(ans + delta)
            opts.add(candidate)
        options = list(opts)
        random.shuffle(options)
    else:
        options = []

    return {
        "question": q,
        "answer": fmt_num(ans),
        "numeric_answer": float(ans),
        "explanation": explanation,
        "type": qtype,
        "options": options,
        "topic": topic,
        "difficulty": level,
    }


def make_exercise_set(topic, difficulty, qtype, count):
    return [make_exercise(topic, difficulty, qtype) for _ in range(int(count))]


def render_exercise(e, idx, total):
    # Answer choices are rendered by the Gradio Radio component below the question.
    # Keeping them out of this HTML prevents duplicate MCQ controls.
    if e["type"] == "mcq":
        body = "<div class='answer-hint'>Choose one answer below.</div>"
    else:
        body = "<div class='answer-hint'>Enter an integer or a number rounded to one decimal place.</div>"

    return f"""
    <div class='exercise-card'>
      <div class='qhead'>Question {idx+1}/{total} · {html.escape(e['topic'])} · {html.escape(e['difficulty'])}</div>
      <div class='qtext'>{e['question']}</div>
      {body}
    </div>
    """


def generate_set(topic, difficulty, qtype, count):
    return make_exercise_set(topic, difficulty, qtype, count)


# State for exercise session is intentionally kept in a Gradio State per user session.
# The persistent result is written to SQLite after submission.


def exercise_start(topic, difficulty, qtype, count):
    items = make_exercise_set(topic, difficulty, qtype, count)
    if not items:
        return "❌ Không tạo được câu hỏi.", [], "", 0
    e = items[0]
    if qtype == "mcq":
        choices = e["options"]
    else:
        choices = []
    return render_exercise(e,0,len(items)), items, "", 0


def normalize_numeric(s):
    s = str(s or "").strip().replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def submit_exercise(user_id, items, idx, answer):
    if not items:
        return "❌ Hãy Generate trước.", [], "", idx

    idx = int(idx)
    if idx >= len(items):
        return "Đã hoàn thành.", items, "", idx

    e = items[idx]
    given = str(answer or "").strip()

    if not given:
        return "⚠️ Nhập/chọn đáp án trước.", items, render_exercise(e,idx,len(items)), idx

    x = normalize_numeric(given)
    expected = e["numeric_answer"]

    # Accept exact integer or one-decimal representation.
    correct = x is not None and abs(x-expected) < 1e-9

    con = db()
    if user_id:
        con.execute("""
            INSERT INTO exercise_attempts(
                user_id,topic,difficulty,question_type,correct,answer,expected,created_at
            ) VALUES(?,?,?,?,?,?,?,?)
        """, (
            user_id,e["topic"],e["difficulty"],e["type"],int(correct),
            given,e["answer"],now()
        ))
        con.commit()
    con.close()

    if correct:
        feedback = f"### ✅ Correct!\n\n**Answer:** `{e['answer']}`"
    else:
        feedback = (
            f"### ❌ Not quite\n\n"
            f"**Your answer:** `{given}`  \n"
            f"**Correct answer:** `{e['answer']}`\n\n"
            f"**Why:** {e['explanation']}"
        )

    next_idx = idx + 1
    if next_idx >= len(items):
        return feedback + "\n\n🎉 **Exercise set complete!**", items, "", next_idx

    nxt = items[next_idx]
    return (
        feedback + f"\n\n---\n\n{render_exercise(nxt,next_idx,len(items))}",
        items,
        "",
        next_idx,
    )


# ============================================================
# AI
# ============================================================

def ai_client(api_key=None):
    key = api_key or OPENAI_API_KEY
    if not key:
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=key)
    except Exception:
        return None


def ai_answer(prompt, system, api_key=None):
    client = ai_client(api_key)
    system = (system + "\n\n" + AI_CUSTOM_PROMPT).strip() if AI_CUSTOM_PROMPT else system
    if not client:
        return (
            "AI chưa được bật. Đặt biến môi trường `OPENAI_API_KEY` rồi restart app. "
            "Các chức năng vocab, spaced repetition và exercise local vẫn chạy bình thường."
        )
    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            instructions=system,
            input=prompt,
        )
        return response.output_text
    except Exception as exc:
        return f"AI error: {type(exc).__name__}: {exc}"


def ai_explain(user_question, correct_answer, explanation, api_key=None):
    return ai_answer(
        f"""
Question:
{user_question}

Expected answer:
{correct_answer}

Existing explanation:
{explanation}

Explain the mistake in simple Vietnamese + English mathematical notation.
Do not just repeat the answer. Show the key step the student likely missed.
""",
        "You are a patient high-school mathematics tutor. Be concise and educational."
        , api_key
    )


def image_to_data_url(path):
    if not path:
        return None
    try:
        raw=Path(path).read_bytes()
        if len(raw)>8_000_000:
            return None
        ext=Path(path).suffix.lower()
        mime={".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",".webp":"image/webp"}.get(ext,"image/png")
        return f"data:{mime};base64,"+base64.b64encode(raw).decode("ascii")
    except Exception:
        return None


def chat(user_id, message, image_path=None, history=None, api_key=None):
    message=(message or "").strip()
    history=history or []
    if not message and not image_path:
        return history, "", None
    client=ai_client(api_key)
    if not client:
        reply="AI is not configured. Put your API key in OPENAI_API_KEY near the top of 67math_app.py and restart the app."
    else:
        system=AI_CUSTOM_PROMPT
        content=[]
        if message: content.append({"type":"input_text","text":message})
        image_url=image_to_data_url(image_path)
        if image_url: content.append({"type":"input_image","image_url":image_url})
        try:
            resp=client.responses.create(model=OPENAI_MODEL,input=[{"role":"user","content":content}],instructions=system)
            reply=resp.output_text or "I could not generate an answer."
        except Exception as e:
            reply=f"AI error: {e}"
    if user_id:
        con=db(); con.execute("INSERT INTO chat_messages(user_id,role,content,created_at) VALUES(?,?,?,?)",(user_id,"user",message or "[image]",now())); con.execute("INSERT INTO chat_messages(user_id,role,content,created_at) VALUES(?,?,?,?)",(user_id,"assistant",reply,now())); con.commit(); con.close()
    history=history+[[message or "📷 Image",reply]]
    return history,"",None



# ============================================================
# OXYZ
# ============================================================

OXYZ_HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body.dark-mode{background:#17131a!important;color:#f6eaf0!important}body.dark-mode .panel,body.dark-mode .sidebar,body.dark-mode .calculator,body.dark-mode .object-list{background:#241d25!important;color:#f6eaf0!important;border-color:#4b3440!important}body.dark-mode input,body.dark-mode select,body.dark-mode button{background:#30232c!important;color:#f6eaf0!important;border-color:#59414d!important}body.dark-mode .title{color:#ff9fc2!important}

*{box-sizing:border-box}
body{margin:0;font-family:Arial,sans-serif;background:#f7f5fa;overflow:hidden}
#app{display:flex;height:760px;width:100%}
#sidebar{width:310px;background:#fff;border-right:1px solid #ddd;padding:18px;overflow-y:auto}
.title{font-size:24px;font-weight:bold;color:#80648f;margin-bottom:15px}
.section{margin-top:18px;border-top:1px solid #eee;padding-top:12px}
.section-title{font-size:14px;font-weight:bold;color:#777;margin-bottom:9px}
button{border:none;border-radius:9px;padding:8px 11px;margin:3px;cursor:pointer;font-weight:bold;font-size:12px}
button:hover{opacity:.8}
.btn-blue{background:#a7c7e7}.btn-pink{background:#f7a8c4}
.btn-purple{background:#c8a2c8}.btn-green{background:#b8e0d2}
.btn-yellow{background:#f6e7a1}.btn-gray{background:#eee}
input,select{border:1px solid #ddd;border-radius:7px;padding:6px;margin:2px;width:60px}
input[type=text]{width:120px}input[type=color]{width:45px;height:30px;padding:2px}
.object{display:flex;align-items:center;justify-content:space-between;padding:7px;margin:4px 0;background:#f8f7fa;border-radius:8px}
#calculation{background:#faf8fc;border-radius:10px;padding:10px;margin-top:8px;font-size:13px;line-height:1.6}
#scene{flex:1;position:relative;background:#f8f9fc}
#toolbar{position:absolute;top:15px;left:15px;z-index:10;background:rgba(255,255,255,.92);padding:8px;border-radius:12px}
.tool{font-size:18px;padding:8px 12px}.active{outline:3px solid #c8a2c8}
#hint{position:absolute;bottom:15px;left:15px;z-index:10;background:rgba(255,255,255,.9);padding:9px 13px;border-radius:10px;font-size:12px;color:#777}
#selected{position:absolute;top:15px;right:15px;z-index:10;background:rgba(255,255,255,.93);padding:12px;border-radius:12px;min-width:210px}
</style>
</head>
<body>
<div id="app">
<div id="sidebar">
<div class="title">📐 OXYZ Simulator</div>
<div class="section">
<div class="section-title">🔵 ADD POINT</div>
<div>Name: <input id="pointName" type="text" value="A"></div>
<div>X: <input id="px" value="1"> Y: <input id="py" value="1"> Z: <input id="pz" value="2"></div>
<div>Color: <input id="pointColor" type="color" value="#a7c7e7"></div>
<button class="btn-blue" onclick="addPoint()">+ Add Point</button>
</div>
<div class="section">
<div class="section-title">📐 ADD GEOMETRY</div>
<div>Line:<br>A:<input id="lineA" type="text" value="A"> B:<input id="lineB" type="text" value="B">
<button class="btn-purple" onclick="addLine()">+ Line</button></div>
<br>
<div>Plane:<br>A:<input id="planeA" type="text" value="A"> B:<input id="planeB" type="text" value="B"> C:<input id="planeC" type="text" value="C">
<button class="btn-pink" onclick="addPlane()">+ Plane</button></div>
</div>
<div class="section">
<div class="section-title">📦 3D SHAPES</div>
<button class="btn-green" onclick="addCube()">+ Cube</button>
<button class="btn-yellow" onclick="addSphere()">+ Sphere</button>
</div>
<div class="section">
<div class="section-title">📈 FUNCTION / SURFACE</div>
<input id="functionInput" type="text" value="x*x + y*y" style="width:180px">
<button class="btn-blue" onclick="addSurface()">z = f(x,y)</button>
</div>
<div class="section">
<div class="section-title">🧮 CALCULATOR</div>
<select id="calcType" onchange="updateCalculatorInputs()" style="width:100%;margin-bottom:6px">
<option value="distance">📏 Distance — 2 Points</option>
<option value="vector">➡ Vector — 2 Points</option>
<option value="midpoint">• Midpoint — 2 Points</option>
<option value="linePlane">✕ Line ∩ Plane</option>
<option value="sphereVolume">⚪ Sphere Volume</option>
<option value="sphereArea">⚪ Sphere Surface Area</option>
<option value="cubeVolume">🟩 Cube Volume</option>
</select>
<div id="calcInputs"></div>
<button class="btn-gray" onclick="calculateSelected()">🧮 Calculate</button>
<div id="calculation">Chọn đối tượng rồi bấm Calculate.</div>
</div>
<div class="section">
<div class="section-title">📋 OBJECTS</div>
<div id="objects"></div>
</div>
</div>
<div id="scene">
<div id="toolbar"><button id="handButton" class="tool active" onclick="setHandMode()">✋</button>
<button class="tool" onclick="resetCamera()">⌂</button></div>
<div id="selected"><b>Selected Object</b><div id="selectedInfo">None</div><br>Color:
<input id="selectedColor" type="color" value="#a7c7e7" oninput="changeSelectedColor()"></div>
<div id="hint">✋ Rotate | 🖱️ Drag points | Scroll = zoom</div>
</div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/DragControls.js"></script>
<script>
const container=document.getElementById("scene");
let scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(45,container.clientWidth/container.clientHeight,.1,1000);
camera.position.set(10,8,10);
let renderer=new THREE.WebGLRenderer({antialias:true});
renderer.setSize(container.clientWidth,container.clientHeight);container.appendChild(renderer.domElement);
let controls=new THREE.OrbitControls(camera,renderer.domElement);controls.enableDamping=true;
scene.add(new THREE.AmbientLight(0xffffff,.8));
let light=new THREE.DirectionalLight(0xffffff,1);light.position.set(10,15,10);scene.add(light);
scene.add(new THREE.AxesHelper(8));scene.add(new THREE.GridHelper(16,16,"#cccccc","#e8e8e8"));
let points=[],lines=[],planes=[],solids=[],surfaces=[],selectedObject=null,dragControls=null,handMode=true;
function findPoint(n){return points.find(p=>p.userData.name.toLowerCase()===n.toLowerCase())}
function setupDragging(){
 if(dragControls)dragControls.dispose();
 dragControls=new THREE.DragControls(points,camera,renderer.domElement);
 dragControls.addEventListener("dragstart",e=>{if(!handMode)return;controls.enabled=false;selectedObject=e.object;showSelected()});
 dragControls.addEventListener("drag",e=>{updateGeometry();showSelected()});
 dragControls.addEventListener("dragend",()=>{controls.enabled=true;updateGeometry()});
}
function addPoint(){
 let name=document.getElementById("pointName").value.trim();
 let x=parseFloat(px.value),y=parseFloat(py.value),z=parseFloat(pz.value),color=pointColor.value;
 if(!name||points.some(p=>p.userData.name===name)||[x,y,z].some(Number.isNaN))return alert("Tên/toạ độ không hợp lệ.");
 let p=new THREE.Mesh(new THREE.SphereGeometry(.22,24,24),new THREE.MeshStandardMaterial({color}));
 p.position.set(x,y,z);p.userData={type:"point",name};scene.add(p);points.push(p);updateObjects();setupDragging();
}
function addLine(){
 let A=findPoint(lineA.value),B=findPoint(lineB.value);
 if(!A||!B||A===B)return alert("Cần hai điểm khác nhau.");
 let l=new THREE.Line(new THREE.BufferGeometry().setFromPoints([A.position,B.position]),new THREE.LineBasicMaterial({color:"#c8a2c8"}));
 l.userData={type:"line",A,B};scene.add(l);lines.push(l);updateObjects();
}
function addPlane(){
 let A=findPoint(planeA.value),B=findPoint(planeB.value),C=findPoint(planeC.value);
 if(!A||!B||!C)return alert("Không tìm thấy A/B/C.");
 let p=new THREE.Mesh(new THREE.PlaneGeometry(8,8),new THREE.MeshBasicMaterial({color:"#f7a8c4",transparent:true,opacity:.45,side:THREE.DoubleSide}));
 p.userData={type:"plane",A,B,C};scene.add(p);planes.push(p);updatePlane(p);updateObjects();
}
function updatePlane(p){
 let A=p.userData.A.position,B=p.userData.B.position,C=p.userData.C.position;
 let n=new THREE.Vector3().crossVectors(new THREE.Vector3().subVectors(B,A),new THREE.Vector3().subVectors(C,A));
 if(n.length()<.0001)return;n.normalize();
 p.position.copy(new THREE.Vector3().add(A).add(B).add(C).multiplyScalar(1/3));
 p.quaternion.setFromUnitVectors(new THREE.Vector3(0,0,1),n);
}
function addCube(){let s=3,o=new THREE.Mesh(new THREE.BoxGeometry(s,s,s),new THREE.MeshStandardMaterial({color:"#b8e0d2",transparent:true,opacity:.65}));o.position.y=s/2;o.userData={type:"cube",size:s};scene.add(o);solids.push(o);updateObjects()}
function addSphere(){let r=2,o=new THREE.Mesh(new THREE.SphereGeometry(r,32,24),new THREE.MeshStandardMaterial({color:"#f7a8c4",transparent:true,opacity:.55}));o.userData={type:"sphere",radius:r};scene.add(o);solids.push(o);updateObjects()}
function addSurface(){
 let expression=functionInput.value,size=5,segments=35,v=[],ind=[];
 function f(x,y){try{return Function("x","y","return "+expression)(x,y)}catch{return 0}}
 for(let i=0;i<=segments;i++){let x=-size+2*size*i/segments;for(let j=0;j<=segments;j++){let y=-size+2*size*j/segments;v.push(x,f(x,y),y)}}
 for(let i=0;i<segments;i++)for(let j=0;j<segments;j++){let a=i*(segments+1)+j,b=a+1,c=a+segments+1,d=c+1;ind.push(a,b,d,a,d,c)}
 let g=new THREE.BufferGeometry();g.setAttribute("position",new THREE.Float32BufferAttribute(v,3));g.setIndex(ind);g.computeVertexNormals();
 let s=new THREE.Mesh(g,new THREE.MeshStandardMaterial({color:"#a7c7e7",side:THREE.DoubleSide,transparent:true,opacity:.7}));
 s.userData={type:"surface",expression};scene.add(s);surfaces.push(s);updateObjects()
}
function updateGeometry(){lines.forEach(l=>l.geometry.setFromPoints([l.userData.A.position,l.userData.B.position]));planes.forEach(updatePlane)}
function updateCalculatorInputs(){
 let t=calcType.value,box=calcInputs;
 let opts=points.map((p,i)=>`<option value="${i}">${p.userData.name}</option>`).join("");
 if(t==="distance"||t==="vector"||t==="midpoint")box.innerHTML=`P1<select id="cp1">${opts}</select>P2<select id="cp2">${opts}</select>`;
 else box.innerHTML="<small>Use the visual objects above for geometry calculations.</small>";
}
function calculateSelected(){
 let t=calcType.value;
 if(t==="distance"||t==="vector"||t==="midpoint"){
   let A=points[Number(cp1.value)],B=points[Number(cp2.value)];if(!A||!B||A===B)return;
   let dx=B.position.x-A.position.x,dy=B.position.y-A.position.y,dz=B.position.z-A.position.z;
   if(t==="distance")calculation.innerHTML=`d = √(${dx.toFixed(2)}² + ${dy.toFixed(2)}² + ${dz.toFixed(2)}²)<br><b>${Math.hypot(dx,dy,dz).toFixed(3)}</b>`;
   if(t==="vector")calculation.innerHTML=`<b>AB = (${dx.toFixed(2)}, ${dy.toFixed(2)}, ${dz.toFixed(2)})</b>`;
   if(t==="midpoint")calculation.innerHTML=`<b>M = (${((A.position.x+B.position.x)/2).toFixed(2)}, ${((A.position.y+B.position.y)/2).toFixed(2)}, ${((A.position.z+B.position.z)/2).toFixed(2)})</b>`;
 }
}
function updateObjects(){
 objects.innerHTML="";
 [...points,...lines,...planes,...solids,...surfaces].forEach(o=>{
   let name=o.userData.type==="point"?o.userData.name:o.userData.type;
   let d=document.createElement("div");d.className="object";d.innerHTML=`<span>${name}</span><button>×</button>`;
   d.onclick=e=>{if(e.target.tagName==="BUTTON"){scene.remove(o);updateObjects();return}selectedObject=o;showSelected()};
   objects.appendChild(d);
 });updateCalculatorInputs()
}
function showSelected(){
 if(!selectedObject){selectedInfo.innerHTML="None";return}
 if(selectedObject.userData.type==="point")selectedInfo.innerHTML=`<b>Point ${selectedObject.userData.name}</b><br>X=${selectedObject.position.x.toFixed(2)}<br>Y=${selectedObject.position.y.toFixed(2)}<br>Z=${selectedObject.position.z.toFixed(2)}`;
 if(selectedObject.material?.color)selectedColor.value="#"+selectedObject.material.color.getHexString()
}
function changeSelectedColor(){if(selectedObject?.material?.color)selectedObject.material.color.set(selectedColor.value)}
function setHandMode(){handMode=!handMode;handButton.classList.toggle("active",handMode)}
function resetCamera(){camera.position.set(10,8,10);controls.target.set(0,0,0);controls.update()}
addPoint();pointName.value="B";px.value=5;py.value=2;pz.value=3;addPoint();pointName.value="C";px.value=2;py.value=5;pz.value=4;addPoint();addLine();addPlane();
function animate(){requestAnimationFrame(animate);controls.update();renderer.render(scene,camera)}animate();
function applyOxyzLanguage(lang){
 const vi={"OXYZ 3D Simulator":"Mô phỏng OXYZ 3D","Add Point":"Thêm điểm","Add Line":"Thêm đường thẳng","Add Plane":"Thêm mặt phẳng","Add Cube":"Thêm hình lập phương","Add Sphere":"Thêm hình cầu","Add Surface":"Thêm mặt cong","Calculator":"Máy tính","Objects":"Đối tượng","Selected":"Đang chọn","Reset Camera":"Đặt lại camera","Hand Mode":"Chế độ tay","Color":"Màu","Calculate":"Tính toán","Distance":"Khoảng cách","Vector":"Vectơ","Midpoint":"Trung điểm","Line - Plane":"Đường thẳng - Mặt phẳng","Sphere Volume":"Thể tích cầu","Sphere Surface":"Diện tích cầu","Cube Volume":"Thể tích lập phương"};
 document.querySelectorAll('button,label,h2,h3,.title,.section-title').forEach(el=>{
   if(!el.dataset.en) el.dataset.en=el.textContent.trim();
   const en=el.dataset.en; el.textContent=lang==='vi'?(vi[en]||en):en;
 });
}
function applyOxyzTheme(dark){document.body.classList.toggle('dark-mode',dark); document.querySelectorAll('input,select,button').forEach(el=>el.classList.toggle('dark-ui',dark))}
window.addEventListener('message',e=>{if(e.data?.type==='67math-theme')applyOxyzTheme(e.data.dark);if(e.data?.type==='67math-lang')applyOxyzLanguage(e.data.lang)});

window.onresize=()=>{camera.aspect=container.clientWidth/container.clientHeight;camera.updateProjectionMatrix();renderer.setSize(container.clientWidth,container.clientHeight)}
</script>
</body>
</html>
"""

OXYZ_IFRAME = f"""
<iframe srcdoc="{html.escape(OXYZ_HTML, quote=True)}"
class="oxyz-frame" title="OXYZ 3D Simulator"></iframe>
"""


# ============================================================
# GRADIO UI
# ============================================================

CSS = """
html,body,.gradio-container{background:#fff4f8!important}
.gradio-container{max-width:1450px!important}
h1,h2,h3{color:#d95f8a!important}
button{border-radius:13px!important}
.primary{background:#f7a8c4!important;border-color:#f7a8c4!important;color:white!important}
.oxyz-frame{width:100%;height:790px;border:0;border-radius:18px;display:block}
.pink-box,.flashcard,.exercise-card,.card-row,.empty{
 background:#fff;border:2px solid #fce1ea;border-radius:18px;padding:20px;margin:10px 0;color:#4b3941
}
.flashcard{text-align:center;min-height:260px}
.bigword{font-size:42px;font-weight:800;color:#4b3941;margin:18px}
.pron{color:#8b6f78;font-style:italic;margin:7px}
.tiny{color:#d95f8a;font-weight:800;letter-spacing:2px}
.card-row{line-height:1.65}
.card-row b{font-size:19px}
.tag{background:#fff0f5;border-radius:10px;padding:3px 8px;margin-left:8px;color:#d95f8a}
.example{color:#8b6f78;font-style:italic}
.muted{color:#8b6f78}
.stats{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}
.stat{background:white;border:2px solid #fce1ea;border-radius:16px;padding:18px;text-align:center}
.stat b{display:block;font-size:30px;color:#d95f8a}
.stat span{color:#8b6f78}
.progress-note{margin:12px 0;padding:12px;background:#fff5f8;border-radius:12px}
.exercise-card{padding:24px}
.qhead{font-weight:800;color:#d95f8a}
.qtext{font-size:19px;margin:15px 0}
.mcq{display:block;padding:11px;margin:7px 0;background:#fff7fa;border-radius:10px;cursor:pointer}
.answer-input{padding:14px;background:#fff5f8;border-radius:12px}
.vocab-head{display:flex;justify-content:space-between;align-items:center;gap:12px}
.sound-btn{border:1px solid #f3b3ca!important;background:#fff4f8!important;color:#c94d7c!important;border-radius:12px!important;padding:7px 11px!important;cursor:pointer}
.sound-btn:hover{transform:translateY(-1px)}
.big-sound{font-size:15px!important;padding:10px 16px!important;margin:8px 0 14px}
body.dark-mode .sound-btn{background:#34252f!important;color:#ffb1cb!important;border-color:#694554!important}
body.dark-mode,body.dark-mode .gradio-container{background:#151218!important;color:#f7edf2!important}
body.dark-mode .gradio-container label,body.dark-mode .gradio-container .label-wrap,body.dark-mode .gradio-container .wrap{color:#f7edf2!important}
body.dark-mode input,body.dark-mode textarea,body.dark-mode button,body.dark-mode select{color:#f7edf2!important}
body.dark-mode .gradio-container button{background:#29202a!important;border-color:#513b48!important}
body.dark-mode .gradio-container .primary{background:#d95f8a!important;color:#fff!important}
@media(max-width:900px){.stats{grid-template-columns:repeat(2,1fr)}}
/* 67Math theme system */
body.dark-mode .gradio-container{background:#17131a!important;color:#f6eaf0!important}
body.dark-mode .gradio-container h1,body.dark-mode .gradio-container h2,body.dark-mode .gradio-container h3{color:#ff9fc2!important}
body.dark-mode .pink-box,body.dark-mode .flashcard,body.dark-mode .exercise-card,body.dark-mode .card-row,body.dark-mode .empty,body.dark-mode .stat{background:#241d25!important;border-color:#4b3440!important;color:#f6eaf0!important}
body.dark-mode .progress-note,body.dark-mode .answer-input{background:#2a2029!important;color:#f6eaf0!important}
body.dark-mode .mcq{background:#30232c!important;color:#f6eaf0!important}
body.dark-mode .muted,body.dark-mode .example,body.dark-mode .pron,body.dark-mode .stat span{color:#cdbbc4!important}
body.dark-mode input,body.dark-mode textarea,body.dark-mode select{background:#241d25!important;color:#f6eaf0!important;border-color:#59414d!important}
body.dark-mode .block,body.dark-mode .form,body.dark-mode .panel{background:#1d181f!important}
.theme-bar{display:flex;justify-content:flex-end;gap:8px;align-items:center;margin-bottom:10px;padding:8px 4px;background:var(--background-fill);border-bottom:1px solid var(--border-color)}
.account-nav{margin-top:10px!important}.main-nav{margin-top:0!important}
.main-nav > .tab-nav{position:fixed!important;top:0!important;left:0!important;right:0!important;width:100%!important;z-index:10000!important;background:var(--background-fill)!important;padding:10px 10px 10px 64px!important;border-bottom:1px solid var(--border-color)!important;gap:6px!important;box-shadow:0 3px 14px rgba(0,0,0,.10)!important}
.main-nav > .tabitem{padding-top:62px!important}
.main-nav > .tab-nav button{border-radius:12px!important;padding:11px 16px!important;font-weight:650!important;white-space:nowrap}
.account-section{margin:18px 0 26px;padding:20px;border:2px solid #fce1ea;border-radius:20px;background:#fff;scroll-margin-top:80px}
.account-section h2,.account-section h3{margin-top:0}
.auth-card{padding:16px;border:1px solid #f1d6e1;border-radius:16px;background:#fff8fb}
.account-title{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}
body.dark-mode .account-section{background:#241d25;border-color:#4b3440}
body.dark-mode .auth-card{background:#2a2029;border-color:#59414d}

.auth-modal{position:fixed!important;inset:0!important;z-index:20000!important;display:flex!important;align-items:center!important;justify-content:center!important;background:rgba(20,12,18,.28)!important;backdrop-filter:blur(7px)!important;-webkit-backdrop-filter:blur(7px)!important;padding:24px!important}.auth-modal.hidden{display:none!important}.auth-modal-inner{width:min(560px,94vw);max-height:88vh;overflow:auto;padding:24px;border:2px solid #f1d6e1;border-radius:24px;background:#fff;box-shadow:0 24px 80px rgba(0,0,0,.22)}body.dark-mode .auth-modal-inner{background:#241d25;border-color:#59414d;color:#f6eaf0}.modal-close{float:right;border:0;background:transparent;font-size:26px;cursor:pointer}.account-page{padding-top:4px}.account-page .account-section{margin-top:0}body.dark-mode .auth-modal .gr-input,body.dark-mode .auth-modal input,body.dark-mode .auth-modal textarea{background:#2a2029!important;color:#f6eaf0!important;border-color:#59414d!important}
.dashboard{margin-top:10px}.dash-hero{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:24px;border-radius:20px;background:linear-gradient(135deg,#fff0f6,#fff)}.dash-hero h2{margin:4px 0}.streak{min-width:110px;text-align:center;font-size:18px}.streak b{font-size:38px;color:#d95f8a;display:block}.streak span{display:block}.dash-stats{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:12px 0}.dash-stat{padding:18px;border:2px solid #fce1ea;border-radius:16px;background:#fff;text-align:center}.dash-stat b{display:block;font-size:25px;color:#d95f8a}.dash-stat span{color:#8b6f78}.activity-panel{padding:18px;border:2px solid #fce1ea;border-radius:18px;background:#fff}.activity-grid{display:grid;grid-template-columns:repeat(15,1fr);gap:6px;margin:12px 0}.activity-cell{height:22px;border-radius:5px;background:#eee}.activity-cell.active{background:#d95f8a;box-shadow:0 0 0 1px #f6a5c2 inset}body.dark-mode .dash-hero,body.dark-mode .dash-stat,body.dark-mode .activity-panel{background:#241d25;border-color:#4b3440}body.dark-mode .activity-cell{background:#3b3038}@media(max-width:900px){.dash-stats{grid-template-columns:repeat(2,1fr)}}

.theme-bar{justify-content:space-between!important}.theme-controls{margin-left:auto}.account-icon{position:fixed;left:14px;top:12px;z-index:9999;width:42px;height:42px;border-radius:50%;border:1px solid #f1c6d6;background:#fff;font-size:20px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.08)}
.flip-card{min-height:300px;position:relative;cursor:pointer;perspective:1000px}.flip-card>div{backface-visibility:hidden;transition:transform .35s}.flash-back{display:none}.flip-card.flipped .flash-front{display:none}.flip-card.flipped .flash-back{display:flex;flex-direction:column;justify-content:center;min-height:300px}.flip-hint{margin-top:18px;color:#9b7a87;font-size:13px}.profile-card{display:flex;gap:20px;align-items:center;padding:20px;border:2px solid #fce1ea;border-radius:18px;background:#fff}.profile-avatar{width:90px;height:90px;border-radius:50%;object-fit:cover;background:#fff0f6;border:3px solid #f2b5ca}.login-menu{padding:8px 0}.ai-image{border:2px dashed #f1c6d6;border-radius:14px;padding:12px}
body.dark-mode .account-icon{background:#241d25;color:#fff;border-color:#59414d}.dark-mode .main-nav > .tab-nav{background:#17131a!important;color:#f6eaf0!important}.dark-mode .main-nav > .tab-nav button{color:#f6eaf0!important}.profile-card,body.dark-mode .profile-card{background:#fff}body.dark-mode .profile-card{background:#241d25;color:#f6eaf0}
.markdown.prose pre,.markdown.prose code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace} .ai-output{font-size:16px;line-height:1.75;padding:16px;border-radius:16px;border:1px solid #f1d6e1;background:#fff;overflow-wrap:anywhere}.ai-output p{margin:.65em 0}.ai-output code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.ai-output pre{white-space:pre-wrap;overflow-x:auto}.ai-output table{width:100%;border-collapse:collapse}.ai-output th,.ai-output td{padding:8px;border:1px solid #ead9df}body.dark-mode .ai-output{background:#241d25;border-color:#4b3440}
.theme-bar button{min-width:105px}
"""

TOPICS = [
    "Linear Equation","Quadratic Function","Coordinate Geometry","Statistics",
    "Sequences","Derivative","Function Optimization","Integral","Oxyz","Probability"
]

with gr.Blocks(
    theme=gr.themes.Soft(primary_hue="pink", secondary_hue="pink", neutral_hue="stone"),
    css=CSS,
    title="🌸 67Math"
) as app:

    user_state = gr.State(None)
    exercise_state = gr.State([])
    exercise_index = gr.State(0)
    review_card_state = gr.State(None)
    online_session_state = gr.State(None)

    # Global language + theme controls. The JS layer updates the whole Gradio UI,
    # while Python remains responsible for data/content language where applicable.
    gr.HTML("""
    <button class="account-icon" title="Account" onclick="open67Account()">👤</button>
    <div class="theme-bar">
      <span id="67math-lang-label">Language</span>
      <div class="theme-controls"><button id="67math-vi" type="button">🇻🇳 VI</button><button id="67math-en" type="button">🇬🇧 EN</button><button id="67math-theme" type="button">🌙</button></div>
    </div>
    <script>
    window.open67Account=function(){
      const tabs=[...document.querySelectorAll('.main-nav > .tab-nav button')];
      const target=tabs.find(b=>(b.textContent||'').includes('Account'));
      if(target) target.click();
      setTimeout(()=>{const m=document.querySelector('.auth-modal'); if(m)m.classList.remove('hidden');},180);
    };
    window.close67Auth=function(){const m=document.querySelector('.auth-modal');if(m)m.classList.add('hidden');};
    document.addEventListener('keydown',e=>{if(e.key==='Escape')window.close67Auth();});
    window.play67Sound=async function(word){
      const clean=String(word||'').trim(); if(!clean)return;
      try{
        const r=await fetch('https://api.dictionaryapi.dev/api/v2/entries/en/'+encodeURIComponent(clean));
        if(r.ok){const data=await r.json(); const audio=data?.[0]?.phonetics?.find(x=>x.audio)?.audio; if(audio){const a=new Audio(audio); a.preload='auto'; await a.play(); return;}}
      }catch(e){}
      try{ if('speechSynthesis' in window){speechSynthesis.cancel(); const u=new SpeechSynthesisUtterance(clean); u.lang='en-US'; u.rate=.78; speechSynthesis.speak(u); return;} }catch(e){}
      alert('No pronunciation audio is available in this browser.');
    };
    (()=>{
      const translations={vi:{"Language":"Ngôn ngữ","Account":"Tài khoản","Vocabulary":"Từ vựng","Exercise Generator":"Tạo bài tập","AI Math Tutor":"Trợ lý Toán AI","My Library":"Thư viện của tôi","Review":"Ôn tập","Test":"Kiểm tra","Progress":"Tiến độ","Dashboard":"Bảng điều khiển","Profile":"Hồ sơ","Login / Account":"Đăng nhập / Tài khoản","Login":"Đăng nhập","Register":"Đăng ký","Create account":"Tạo tài khoản","Logout":"Đăng xuất","Username":"Tên tài khoản","Password":"Mật khẩu","New username":"Tên tài khoản mới","New password":"Mật khẩu mới","Full name":"Họ và tên","Date of birth":"Ngày sinh","Profile picture":"Ảnh đại diện","Save profile":"Lưu hồ sơ","MCQ answer":"Đáp án trắc nghiệm","Number answer":"Đáp án số","Submit":"Nộp bài","Generate":"Tạo bài","Question type":"Loại câu hỏi","Difficulty":"Độ khó","Topic":"Chủ đề","Your question":"Câu hỏi của bạn","Send":"Gửi","Clear":"Xóa","OXYZ Simulator":"Mô phỏng OXYZ"}};
      const placeholders={vi:{"Enter your username":"Nhập tên tài khoản","Enter your password":"Nhập mật khẩu","3–32 characters":"3–32 ký tự","At least 6 characters":"Ít nhất 6 ký tự","YYYY-MM-DD":"YYYY-MM-DD","Explain this problem step by step...":"Giải bài này từng bước..."}};
      function translate(lang){
        document.documentElement.dataset.lang=lang;
        document.querySelectorAll('body *').forEach(el=>{
          if(el.children.length===0){const raw=el.dataset.en67||el.textContent.trim();if(raw){if(!el.dataset.en67)el.dataset.en67=raw;const out=lang==='vi'?(translations.vi[el.dataset.en67]||el.dataset.en67):el.dataset.en67;if(el.textContent!==out)el.textContent=out;}}
          if(el.tagName==='INPUT'||el.tagName==='TEXTAREA'){const raw=el.dataset.ph67||el.placeholder;if(raw){if(!el.dataset.ph67)el.dataset.ph67=raw;el.placeholder=lang==='vi'?(placeholders.vi[raw]||raw):raw;}}
        });
        document.querySelectorAll('iframe').forEach(f=>{try{f.contentWindow.postMessage({type:'67math-lang',lang},'*')}catch(e){}});
      }
      function theme(dark){
        document.documentElement.classList.toggle('dark-mode',dark);
        document.body.classList.toggle('dark-mode',dark);
        document.documentElement.style.colorScheme=dark?'dark':'light';
        localStorage.setItem('67math-theme',dark?'dark':'light');
        const b=document.getElementById('67math-theme');if(b)b.textContent=dark?'☀️':'🌙';
        document.querySelectorAll('iframe').forEach(f=>{try{f.contentWindow.postMessage({type:'67math-theme',dark},'*')}catch(e){}});
      }
      const lang=localStorage.getItem('67math-lang')||'en';
      const dark=localStorage.getItem('67math-theme')==='dark';
      const init=()=>{theme(dark);translate(lang);};
      document.getElementById('67math-vi').onclick=()=>{localStorage.setItem('67math-lang','vi');translate('vi');};
      document.getElementById('67math-en').onclick=()=>{localStorage.setItem('67math-lang','en');translate('en');};
      document.getElementById('67math-theme').onclick=()=>theme(!document.body.classList.contains('dark-mode'));
      init();
      setTimeout(init,300); setTimeout(init,1200);
      new MutationObserver(()=>{clearTimeout(window.__67tr);window.__67tr=setTimeout(()=>translate(localStorage.getItem('67math-lang')||'en'),80);}).observe(document.body,{childList:true,subtree:true});
    })();
    </script>
    """)

    gr.Markdown("# 🌸 67Math\n### OXYZ • Vocabulary • Exercises • AI Tutor")

    # Modal is rendered separately so it overlays the current page instead of taking layout space.
    with gr.Group(elem_classes=["auth-modal", "hidden"]) as auth_modal:
        gr.HTML("<div class='auth-modal-inner'><button class='modal-close' onclick='close67Auth()'>×</button><h2>🔐 Login / Register</h2><p class='muted'>Your account syncs vocabulary, exercises, progress and profile.</p>")
        with gr.Row():
            with gr.Column(elem_classes=["auth-card"]):
                gr.Markdown("### 🔑 Login")
                login_user=gr.Textbox(label="Username", placeholder="Enter your username")
                login_pass=gr.Textbox(label="Password", type="password", placeholder="Enter your password")
                login_btn=gr.Button("Login", variant="primary")
            with gr.Column(elem_classes=["auth-card"]):
                gr.Markdown("### ✨ Create account")
                reg_user=gr.Textbox(label="New username", placeholder="3–32 characters")
                reg_pass=gr.Textbox(label="New password", type="password", placeholder="At least 6 characters")
                register_btn=gr.Button("Create account", variant="primary")
        with gr.Row():
            logout_btn=gr.Button("Logout", scale=0)
            auth_close_btn=gr.Button("Close", scale=0)
        gr.HTML("</div>")

    # ---------------- ACCOUNT / DASHBOARD ----------------
    # Account is its own navigator tab. Login/Register opens as a modal overlay.
    # The fixed navigator starts here; Account is a dedicated tab.
    with gr.Tabs(elem_classes=["main-nav"]):
        with gr.Tab("👤 Account"):
            with gr.Row():
                gr.Markdown("## 👤 Account")
                account_login_open=gr.Button("🔐 Login / Register", variant="primary", scale=0)
            auth_status=gr.Markdown("🔒 Not logged in")
            with gr.Tabs():
                with gr.Tab("📊 Dashboard"):
                    dashboard=gr.HTML("<div class='empty'>Login to see your dashboard.</div>")
                    dashboard_refresh=gr.Button("↻ Refresh dashboard")
                with gr.Tab("👤 Profile"):
                    with gr.Row():
                        profile_name=gr.Textbox(label="Full name")
                        profile_dob=gr.Textbox(label="Date of birth", placeholder="YYYY-MM-DD")
                    profile_pic=gr.Image(label="Profile picture", type="filepath", sources=["upload"], height=140)
                    profile_preview=gr.HTML(profile_preview_html(None))
                    profile_save=gr.Button("Save profile", variant="primary")
                    profile_status=gr.Markdown()


    # ---------------- FEATURE TABS ----------------

        # ---------------- OXYZ ----------------
        with gr.Tab("📐 OXYZ Simulator"):
            gr.HTML(OXYZ_IFRAME)

        # ---------------- VOCAB ----------------
        with gr.Tab("🃏 Vocabulary"):

            with gr.Tabs():

                with gr.Tab("📚 My Library"):
                    with gr.Row():
                        deck_select = gr.Dropdown([], label="Deck", scale=2)
                        refresh_btn = gr.Button("↻ Refresh")
                    with gr.Row():
                        new_deck_name = gr.Textbox(label="New deck name")
                        new_deck_desc = gr.Textbox(label="Description")
                    create_deck_btn = gr.Button("🌷 Create deck", variant="primary")
                    deck_status = gr.Markdown()

                    gr.Markdown("### ➕ Add vocabulary")
                    with gr.Row():
                        v_word = gr.Textbox(label="Word / phrase")
                        v_meaning = gr.Textbox(label="Meaning")
                        v_pron = gr.Textbox(label="Pronunciation / IPA")
                    with gr.Row():
                        v_example = gr.Textbox(label="Example")
                        v_notes = gr.Textbox(label="Notes")
                        v_tags = gr.Textbox(label="Tags")
                    add_card_btn = gr.Button("＋ Add card", variant="primary")
                    card_list = gr.HTML("<div class='empty'>Login → chọn deck.</div>")

                    gr.Markdown("### 🔗 Share / Copy deck")
                    with gr.Row():
                        share_btn = gr.Button("🔗 Generate share code")
                        share_output = gr.Markdown()
                    with gr.Row():
                        share_code = gr.Textbox(label="Paste share code")
                        copy_btn = gr.Button("📋 Copy deck")
                    copy_status = gr.Markdown()

                with gr.Tab("🧠 Review"):
                    review_deck=gr.Dropdown([], label="Review deck — empty = all")
                    review_refresh=gr.Button("🌸 Start review")
                    review_card=gr.HTML()
                    review_card_state=gr.State(None)
                    with gr.Row():
                        again_btn=gr.Button("Again")
                        hard_btn=gr.Button("Hard")
                        good_btn=gr.Button("Good", variant="primary")
                        easy_btn=gr.Button("Easy")
                    review_status=gr.Markdown()
                with gr.Tab("🧪 Test"):
                    with gr.Row():
                        test_deck=gr.Dropdown([], label="Deck — empty = all")
                        test_count=gr.Dropdown(["5","6","7","8","9","10"], value="5", label="Questions")
                    test_start=gr.Button("🎲 Random test", variant="primary")
                    test_question=gr.Markdown()
                    test_answer=gr.Radio([], label="Choose the word")
                    test_submit=gr.Button("Submit")
                    test_feedback=gr.Markdown()
                    test_items=gr.State([]); test_index=gr.State(0)

                with gr.Tab("📊 Progress"):
                    progress_refresh = gr.Button("↻ Refresh progress")
                    progress = gr.HTML("<div class='empty'>Login to see progress.</div>")

        # ---------------- EXERCISES ----------------
        with gr.Tab("📝 Exercise Generator"):
            with gr.Row():
                ex_topic = gr.Dropdown(TOPICS, value="Derivative", label="📚 Topic")
                ex_diff = gr.Dropdown(["Easy","Medium","Hard"], value="Medium", label="⭐ Difficulty")
                ex_type = gr.Radio(["mcq","number"], value="mcq", label="Question type")
                ex_count = gr.Dropdown(["5","10","15","20"], value="10", label="Number")

            ex_generate = gr.Button("🌷 GENERATE", variant="primary")
            ex_question=gr.HTML()
            ex_choices=gr.Radio([], label="Answer", visible=True)
            ex_number=gr.Textbox(label="Answer", visible=False)
            ex_submit=gr.Button("✓ Submit")
            ex_feedback = gr.Markdown()
            ai_explain_btn = gr.Button("🤖 Explain my mistake with AI")
            ai_explain_output = gr.Markdown(elem_classes=["ai-output"])

            def toggle_type(t):
                return gr.update(visible=t=="mcq"), gr.update(visible=t=="number")

            ex_type.change(toggle_type, ex_type, [ex_choices, ex_number])

        # ---------------- CHATBOT ----------------
        with gr.Tab("💬 AI Math Tutor"):
            gr.Markdown("Ask a math problem or upload a photo of your worksheet/problem.")
            chatbot=gr.Chatbot(height=500, elem_classes=["ai-output"])
            chat_image=gr.Image(label="📷 Upload a math problem", type="filepath", sources=["upload"], elem_classes=["ai-image"])
            chat_input=gr.Textbox(label="Your question", placeholder="Explain this problem step by step...")
            with gr.Row():
                chat_send=gr.Button("Send", variant="primary")
                clear_chat=gr.Button("Clear")

    # ========================================================
    # EVENTS
    # ========================================================

    def do_login(u,p):
        msg, uid = login(u,p)
        if uid:
            ensure_default_decks(uid)
        choices = deck_choices(uid)
        sid = start_online_session(uid) if uid else None
        _, pname, pdob, ppic=get_profile(uid) if uid else ("","",None,None)
        return (msg,uid,gr.update(choices=choices,value=choices[0] if choices else None),gr.update(choices=choices,value=None),tracking_html(uid),dashboard_html(uid),sid,pname,pdob,profile_preview_html(ppic),gr.update(choices=choices,value=None))

    def do_register(u,p):
        msg, uid = register(u,p)
        if uid:
            ensure_default_decks(uid)
        choices = deck_choices(uid)
        sid = start_online_session(uid) if uid else None
        _, pname, pdob, ppic=get_profile(uid) if uid else ("","",None,None)
        return (msg,uid,gr.update(choices=choices,value=choices[0] if choices else None),gr.update(choices=choices,value=None),tracking_html(uid),dashboard_html(uid),sid,pname,pdob,profile_preview_html(ppic),gr.update(choices=choices,value=None))

    login_btn.click(
        do_login,
        [login_user,login_pass],
        [auth_status,user_state,deck_select,review_deck,progress,dashboard,online_session_state,profile_name,profile_dob,profile_preview,test_deck]
    )
    register_btn.click(
        do_register,
        [reg_user,reg_pass],
        [auth_status,user_state,deck_select,review_deck,progress,dashboard,online_session_state,profile_name,profile_dob,profile_preview,test_deck]
    )

    def do_logout(uid, sid):
        finish_online_session(uid, sid)
        return "🔒 Logged out.", None, gr.update(choices=[],value=None), gr.update(choices=[],value=None), "<div class='empty'>Login to see progress.</div>", "<div class='empty'>Login to see your learning dashboard.</div>", None, "", "", profile_preview_html(None), gr.update(choices=[],value=None)

    logout_btn.click(do_logout, [user_state,online_session_state], [auth_status,user_state,deck_select,review_deck,progress,dashboard,online_session_state,profile_name,profile_dob,profile_preview,test_deck])

    dashboard_refresh.click(dashboard_html, user_state, dashboard)
    profile_save.click(save_profile,[user_state,profile_name,profile_dob,profile_pic],[profile_status,profile_name,profile_dob,profile_preview])
    online_timer = gr.Timer(60)
    online_timer.tick(dashboard_html, user_state, dashboard)
    online_timer.tick(touch_online, [user_state, online_session_state], [])

    refresh_btn.click(lambda uid: gr.update(choices=deck_choices(uid)),user_state,deck_select)
    refresh_btn.click(lambda uid: gr.update(choices=deck_choices(uid)),user_state,test_deck)
    create_deck_btn.click(
        create_deck,
        [user_state,new_deck_name,new_deck_desc],
        [deck_status,deck_select]
    )
    add_card_btn.click(
        add_card,
        [user_state,deck_select,v_word,v_meaning,v_pron,v_example,v_notes,v_tags],
        [deck_status,card_list]
    )

    def show_cards(uid, deck):
        return refresh_deck_cards(uid, deck)[0]

    deck_select.change(
        show_cards,
        [user_state,deck_select],
        card_list
    )

    share_btn.click(share_deck, [user_state,deck_select], share_output)
    copy_btn.click(copy_shared_deck, [user_state,share_code], [copy_status,deck_select])

    review_refresh.click(refresh_review,[user_state,review_deck],[review_card,review_card_state])

    def rate(user_id, cid, rating, deck):
        msg, _ = review_card(user_id,cid,rating)
        card_html, new_id = review_next(user_id,deck,cid)
        return msg, card_html, new_id, tracking_html(user_id)

    for btn, rating in [
        (again_btn,"Again"),(hard_btn,"Hard"),(good_btn,"Good"),(easy_btn,"Easy")
    ]:
        btn.click(
            lambda uid,cid,deck,r=rating: rate(uid,cid,r,deck),
            [user_state,review_card_state,review_deck],
            [review_status,review_card,review_card_state,progress]
        )

    test_start.click(vocab_test_start,[user_state,test_deck,test_count],[test_question,test_items,test_index,test_answer,test_feedback])
    def submit_vocab_test(uid,items,idx,ans):
        return vocab_test_submit(uid,items,idx,ans)
    test_submit.click(submit_vocab_test,[user_state,test_items,test_index,test_answer],[test_question,test_index,test_answer,test_feedback])

    progress_refresh.click(tracking_html, user_state, progress)

    # Exercise state handling.
    def start_ex(uid, topic, diff, typ, count):
        question, items, feedback, idx = exercise_start(topic,diff,typ,count)
        if items and typ=="mcq":
            choices = items[0]["options"]
        else:
            choices = []
        return question, items, idx, gr.update(choices=choices,value=None,visible=typ=="mcq"), gr.update(value="",visible=typ=="number"), feedback

    ex_generate.click(
        start_ex,
        [user_state,ex_topic,ex_diff,ex_type,ex_count],
        [ex_question,exercise_state,exercise_index,ex_choices,ex_number,ex_feedback]
    )

    def submit_ex(uid, items, idx, typ, choice, number):
        answer = choice if typ=="mcq" else number
        feedback, items, _, new_idx = submit_exercise(uid,items,idx,answer)
        if new_idx < len(items):
            nxt = items[new_idx]
            choices = nxt["options"] if typ=="mcq" else []
            qhtml = render_exercise(nxt,new_idx,len(items))
        else:
            choices=[]
            qhtml="<div class='pink-box'><h2>🎉 Complete</h2></div>"
        return feedback, qhtml, new_idx, gr.update(choices=choices,value=None), ""

    ex_submit.click(
        submit_ex,
        [user_state,exercise_state,exercise_index,ex_type,ex_choices,ex_number],
        [ex_feedback,ex_question,exercise_index,ex_choices,ex_number]
    )

    def explain_current(items, idx):
        if not items:
            return "Generate an exercise first."
        idx = int(idx)
        if idx >= len(items):
            idx = len(items) - 1
        e = items[idx]
        return ai_explain(
            re.sub("<[^>]+>", "", e["question"]),
            e["answer"],
            e["explanation"]
        )

    ai_explain_btn.click(
        explain_current,
        [exercise_state,exercise_index],
        ai_explain_output
    )

    chat_send.click(chat,[user_state,chat_input,chat_image,chatbot],[chatbot,chat_input,chat_image])
    chat_input.submit(chat,[user_state,chat_input,chat_image,chatbot],[chatbot,chat_input,chat_image])
    clear_chat.click(lambda: [], outputs=chatbot)

    # Open/close the account modal from the dedicated Account tab.
    account_login_open.click(None, None, None, js="""()=>{const m=document.querySelector('.auth-modal');if(m)m.classList.remove('hidden');}""")
    auth_close_btn.click(None, None, None, js="""()=>{const m=document.querySelector('.auth-modal');if(m)m.classList.add('hidden');}""")


if __name__ == "__main__":
    print("🌸 67Math starting...")
    print("DB:", os.path.abspath(DB_PATH))
    print("AI:", "enabled" if OPENAI_API_KEY else "disabled")
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "7860"))
    app.launch(server_name=host, server_port=port)
