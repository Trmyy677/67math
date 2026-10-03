import os, uuid, tempfile
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request, Response, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import core

BASE = Path(__file__).parent
app = FastAPI(title="67Math", version="1.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

SESSIONS = {}

def user_id(request: Request):
    token = request.cookies.get("67math_session")
    uid = SESSIONS.get(token)
    return int(uid) if uid else None

def require_user(request: Request):
    uid = user_id(request)
    if not uid:
        raise HTTPException(401, "Login required")
    core.ensure_default_decks(uid)
    return uid

def rowdict(row):
    return dict(row) if row else None

def deck_id(value):
    try: return int(value)
    except: return None

class Credentials(BaseModel):
    username: str
    password: str
class DeckIn(BaseModel):
    name: str
    description: str = ""
class CardIn(BaseModel):
    deck_id: int
    word: str
    meaning: str = ""
    pronunciation: str = ""
    example: str = ""
    notes: str = ""
    tags: str = ""
class ReviewIn(BaseModel):
    card_id: int
    rating: str
class TestStart(BaseModel):
    deck_id: Optional[int] = None
    count: int = 5
class TestAnswer(BaseModel):
    item: dict
    answer: str
class ExerciseStart(BaseModel):
    topic: str
    difficulty: str
    qtype: str
    count: int = 10
class ExerciseAnswer(BaseModel):
    item: dict
    answer: str
class ProfileIn(BaseModel):
    full_name: str = ""
    dob: str = ""

@app.get("/", response_class=HTMLResponse)
def home():
    return (BASE / "static" / "index.html").read_text(encoding="utf-8")

@app.get("/api/oxyz", response_class=HTMLResponse)
def oxyz():
    return HTMLResponse(core.OXYZ_HTML)

@app.get("/api/me")
def me(request: Request):
    uid = user_id(request)
    if not uid: return {"logged_in": False}
    core.ensure_default_decks(uid)
    username, full_name, dob, pic = core.get_profile(uid)
    return {"logged_in": True, "id": uid, "username": username, "full_name": full_name, "dob": dob, "profile_pic": pic}

@app.post("/api/register")
def register(data: Credentials, response: Response):
    msg, uid = core.register(data.username, data.password)
    if not uid: return JSONResponse({"ok":False,"message":msg}, status_code=400)
    token=uuid.uuid4().hex; SESSIONS[token]=uid
    response.set_cookie("67math_session", token, httponly=True, samesite="lax", max_age=60*60*24*30)
    core.ensure_default_decks(uid)
    return {"ok":True,"message":msg}

@app.post("/api/login")
def login(data: Credentials, response: Response):
    msg, uid = core.login(data.username, data.password)
    if not uid: return JSONResponse({"ok":False,"message":msg}, status_code=401)
    token=uuid.uuid4().hex; SESSIONS[token]=uid
    response.set_cookie("67math_session", token, httponly=True, samesite="lax", max_age=60*60*24*30)
    core.ensure_default_decks(uid)
    return {"ok":True,"message":msg}

@app.post("/api/logout")
def logout(request: Request, response: Response):
    token=request.cookies.get("67math_session")
    if token: SESSIONS.pop(token,None)
    response.delete_cookie("67math_session")
    return {"ok":True}

@app.get("/api/dashboard")
def dashboard(request: Request):
    uid=require_user(request)
    return {"html":core.dashboard_html(uid), "progress_html":core.tracking_html(uid)}

@app.post("/api/profile")
async def profile(request: Request, full_name: str = Form(""), dob: str = Form(""), picture: UploadFile | None = File(None)):
    uid=require_user(request)
    pic_data=None
    if picture and picture.filename:
        raw=await picture.read()
        if len(raw)>2_000_000: raise HTTPException(400,"Profile picture must be under 2 MB")
        ext=Path(picture.filename).suffix.lower()
        mime={".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",".webp":"image/webp"}.get(ext,"image/png")
        import base64
        pic_data=f"data:{mime};base64,"+base64.b64encode(raw).decode()
    con=core.db()
    if pic_data:
        con.execute("UPDATE users SET full_name=?,dob=?,profile_pic=? WHERE id=?",(full_name.strip()[:120],dob.strip()[:30],pic_data,uid))
    else:
        con.execute("UPDATE users SET full_name=?,dob=? WHERE id=?",(full_name.strip()[:120],dob.strip()[:30],uid))
    con.commit(); con.close()
    return {"ok":True, **(await me(request))}

@app.get("/api/decks")
def decks(request: Request):
    uid=require_user(request)
    con=core.db(); rows=con.execute("SELECT id,name,description,share_code,created_at FROM decks WHERE owner_id=? ORDER BY name",(uid,)).fetchall(); con.close()
    return {"decks":[rowdict(r) for r in rows]}

@app.post("/api/decks")
def create_deck(request: Request, data: DeckIn):
    uid=require_user(request)
    name=data.name.strip()
    if not name: raise HTTPException(400,"Deck name is required")
    con=core.db(); code=core.secrets.token_urlsafe(9)
    cur=con.execute("INSERT INTO decks(owner_id,name,description,share_code,created_at) VALUES(?,?,?,?,?)",(uid,name[:80],data.description[:500],code,core.now())); con.commit(); did=cur.lastrowid; con.close()
    return {"ok":True,"id":did}

@app.get("/api/decks/{did}/cards")
def cards(request: Request,did:int):
    uid=require_user(request); con=core.db(); owner=con.execute("SELECT owner_id FROM decks WHERE id=?",(did,)).fetchone()
    if not owner or owner["owner_id"]!=uid: raise HTTPException(404,"Deck not found")
    rows=con.execute("SELECT id,word,meaning,pronunciation,example,notes,tags,due_date,interval_days,repetitions FROM cards WHERE deck_id=? ORDER BY id DESC",(did,)).fetchall(); con.close()
    return {"cards":[rowdict(r) for r in rows]}

@app.post("/api/cards")
def add_card_api(request: Request,data:CardIn):
    uid=require_user(request); con=core.db(); owner=con.execute("SELECT owner_id FROM decks WHERE id=?",(data.deck_id,)).fetchone()
    if not owner or owner["owner_id"]!=uid: raise HTTPException(404,"Deck not found")
    if not data.word.strip(): raise HTTPException(400,"Word is required")
    con.execute("INSERT INTO cards(deck_id,word,meaning,pronunciation,example,notes,tags,due_date,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(data.deck_id,data.word.strip(),data.meaning.strip(),data.pronunciation.strip(),data.example.strip(),data.notes.strip(),data.tags.strip(),core.today(),core.now())); con.commit(); con.close()
    return {"ok":True}

@app.post("/api/decks/{did}/share")
def share(request:Request,did:int):
    uid=require_user(request); con=core.db(); r=con.execute("SELECT name,share_code FROM decks WHERE id=? AND owner_id=?",(did,uid)).fetchone(); con.close()
    if not r: raise HTTPException(404,"Deck not found")
    return {"name":r["name"],"share_code":r["share_code"]}

@app.post("/api/decks/copy")
def copy_deck(request:Request, body:dict):
    uid=require_user(request); code=str(body.get("share_code","")).strip(); con=core.db(); src=con.execute("SELECT * FROM decks WHERE share_code=?",(code,)).fetchone()
    if not src: con.close(); raise HTTPException(404,"Share code not found")
    cur=con.execute("INSERT INTO decks(owner_id,name,description,share_code,created_at) VALUES(?,?,?,?,?)",(uid,src["name"]+" (copy)",src["description"],core.secrets.token_urlsafe(9),core.now())); new=cur.lastrowid
    for c in con.execute("SELECT * FROM cards WHERE deck_id=?",(src["id"],)).fetchall():
        con.execute("INSERT INTO cards(deck_id,word,meaning,pronunciation,example,notes,tags,ease,interval_days,repetitions,due_date,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(new,c["word"],c["meaning"],c["pronunciation"],c["example"],c["notes"],c["tags"],2.5,0,0,core.today(),core.now()))
    con.commit(); con.close(); return {"ok":True}

@app.get("/api/review")
def review(request:Request,deck_id:Optional[int]=None):
    uid=require_user(request); rows=core.get_due_cards(uid,deck_id); card=rowdict(rows[0]) if rows else None
    return {"card":card,"count":len(rows)}

@app.post("/api/review")
def review_api(request:Request,data:ReviewIn):
    uid=require_user(request); msg,_=core.review_card(uid,data.card_id,data.rating); rows=core.get_due_cards(uid); return {"message":msg,"card":rowdict(rows[0]) if rows else None,"count":len(rows)}

@app.post("/api/test/start")
def test_start(request:Request,data:TestStart):
    uid=require_user(request); n=max(5,min(10,int(data.count))); con=core.db()
    if data.deck_id: rows=con.execute("SELECT * FROM cards WHERE deck_id=?",(data.deck_id,)).fetchall()
    else: rows=con.execute("SELECT c.* FROM cards c JOIN decks d ON d.id=c.deck_id WHERE d.owner_id=?",(uid,)).fetchall()
    con.close(); rows=list(rows)
    if not rows: raise HTTPException(400,"No vocabulary available")
    chosen=core.random.sample(rows,min(n,len(rows))); words=[r["word"] for r in rows]; items=[]
    for r in chosen:
        distract=core.random.sample([w for w in words if w!=r["word"]],min(3,max(0,len(words)-1))); opts=distract+[r["word"]]; core.random.shuffle(opts); items.append({"id":r["id"],"word":r["word"],"meaning":r["meaning"],"options":opts})
    return {"items":items,"index":0,"question":items[0]}

@app.post("/api/test/answer")
def test_answer(request:Request,data:TestAnswer):
    correct=data.answer.strip().lower()==str(data.item.get("word","")).strip().lower(); return {"correct":correct,"correct_answer":data.item.get("word")}

@app.post("/api/exercises/start")
def ex_start(request:Request,data:ExerciseStart):
    require_user(request); n=max(1,min(20,int(data.count))); items=core.make_exercise_set(data.topic,data.difficulty,data.qtype,n); return {"items":items,"index":0}

@app.post("/api/exercises/answer")
def ex_answer(request:Request,data:ExerciseAnswer):
    uid=require_user(request); item=data.item; given=str(data.answer or "").strip(); x=core.normalize_numeric(given); expected=item.get("numeric_answer"); correct=x is not None and abs(x-float(expected))<1e-9
    con=core.db(); con.execute("INSERT INTO exercise_attempts(user_id,topic,difficulty,question_type,correct,answer,expected,created_at) VALUES(?,?,?,?,?,?,?,?)",(uid,item.get("topic",""),item.get("difficulty",""),item.get("type",""),int(correct),given,item.get("answer",""),core.now())); con.commit(); con.close()
    return {"correct":correct,"answer":item.get("answer"),"explanation":item.get("explanation")}

@app.post("/api/online")
def online(request:Request,body:dict):
    uid=require_user(request); sid=body.get("session_id")
    if not sid: sid=core.start_online_session(uid)
    else: core.touch_online(uid,sid)
    return {"session_id":sid}

@app.post("/api/ai")
async def ai(request:Request,message:str=Form(""),image:UploadFile|None=File(None),history:str=Form("[]")):
    uid=require_user(request)
    image_path=None
    if image and image.filename:
        raw=await image.read()
        if len(raw)>8_000_000: raise HTTPException(400,"Image too large")
        suffix=Path(image.filename).suffix or ".png"
        f=tempfile.NamedTemporaryFile(delete=False,suffix=suffix); f.write(raw); f.close(); image_path=f.name
    try:
        import json
        hist=json.loads(history or "[]")
        out,_,_=core.chat(uid,message,image_path,hist)
        return {"history":out,"reply":out[-1][1] if out else ""}
    finally:
        if image_path:
            try: os.unlink(image_path)
            except: pass

@app.get("/api/topics")
def topics():
    return {"topics":core.TOPICS,"difficulties":["Easy","Medium","Hard"]}
