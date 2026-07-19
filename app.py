import csv, hashlib, io, json, os, re, secrets, sqlite3, urllib.request, uuid, zipfile
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import firebase_admin
from firebase_admin import auth
import click
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")); AUDIO_DIR = DATA_DIR / "audio"; DB_PATH = DATA_DIR / "index-inbox.sqlite3"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", ""); PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")
AUTH_PROVIDER = os.getenv("AUTH_PROVIDER", "firebase").strip().lower()
if AUTH_PROVIDER not in {"firebase", "local"}: raise RuntimeError("AUTH_PROVIDER must be 'firebase' or 'local'")
ALLOWED_EMAILS = {x.strip().lower() for x in os.getenv("ALLOWED_EMAILS", "").split(",") if x.strip()}
REQUIRE_VERIFIED_EMAIL = os.getenv("REQUIRE_VERIFIED_EMAIL", "false").lower() == "true"
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "true").lower() == "true"
AUTH_EXPECTED_ORIGIN = os.getenv("AUTH_EXPECTED_ORIGIN", "").rstrip("/")
AUTH_ORIGINS_VALUE = os.getenv("AUTH_ALLOWED_ORIGINS", "").strip() or AUTH_EXPECTED_ORIGIN
AUTH_ALLOWED_ORIGINS = {x.strip().rstrip("/") for x in AUTH_ORIGINS_VALUE.split(",") if x.strip()}
LOCAL_SETUP_TOKEN = os.getenv("LOCAL_SETUP_TOKEN", "")
AUTH_SESSION_DAYS = max(int(os.getenv("AUTH_SESSION_DAYS", "30")), 1)
AUTH_IDLE_DAYS = max(int(os.getenv("AUTH_IDLE_DAYS", "7")), 1)
AUTH_COOKIE = "__Host-index_session" if AUTH_COOKIE_SECURE else "index_session"
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("index-inbox-dummy-password")
MAX_AUDIO_BYTES = int(os.getenv("MAX_AUDIO_MB", "25")) * 1024 * 1024
BACKUP_HOOK_URL = os.getenv("BACKUP_HOOK_URL", "")
VALID_CATEGORIES = {"note", "task", "idea", "question"}
DATA_DIR.mkdir(parents=True, exist_ok=True); AUDIO_DIR.mkdir(parents=True, exist_ok=True)
app = Flask(__name__, static_folder=None); app.config["MAX_CONTENT_LENGTH"] = MAX_AUDIO_BYTES + 1024 * 1024
if AUTH_PROVIDER == "firebase" and PROJECT_ID and not firebase_admin._apps: firebase_admin.initialize_app(options={"projectId": PROJECT_ID})

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH); g.db.row_factory = sqlite3.Row; g.db.execute("PRAGMA journal_mode=WAL")
    return g.db

@app.teardown_appcontext
def close_db(_error):
    connection = g.pop("db", None)
    if connection is not None: connection.close()

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
      CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, recorded_at TEXT,
        transcription TEXT NOT NULL DEFAULT '', trigger_type TEXT, audio_path TEXT, audio_mime TEXT,
        payload_json TEXT NOT NULL, starred INTEGER NOT NULL DEFAULT 0, processed INTEGER NOT NULL DEFAULT 0,
        tags TEXT NOT NULL DEFAULT '');
      CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
        level TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, details TEXT NOT NULL DEFAULT '');
      CREATE INDEX IF NOT EXISTS idx_entries_created ON entries(created_at DESC);
      CREATE TABLE IF NOT EXISTS local_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, session_version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, password_changed_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS local_sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
        session_version INTEGER NOT NULL, csrf_token TEXT NOT NULL, created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
        expires_at TEXT NOT NULL, FOREIGN KEY(user_id) REFERENCES local_users(id) ON DELETE CASCADE);
      CREATE TABLE IF NOT EXISTS login_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, attempted_at TEXT NOT NULL,
        username TEXT NOT NULL, source_ip TEXT NOT NULL, successful INTEGER NOT NULL DEFAULT 0);
      CREATE INDEX IF NOT EXISTS idx_login_attempts_lookup ON login_attempts(username,source_ip,attempted_at);
      CREATE TABLE IF NOT EXISTS note_groups (name TEXT PRIMARY KEY COLLATE NOCASE, display_name TEXT NOT NULL,
        created_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
    """)
    columns = {r[1] for r in con.execute("PRAGMA table_info(entries)")}
    additions = {"title":"TEXT NOT NULL DEFAULT ''", "category":"TEXT NOT NULL DEFAULT 'note'",
      "archived":"INTEGER NOT NULL DEFAULT 0", "source_key":"TEXT", "group_name":"TEXT"}
    for name, definition in additions.items():
        if name not in columns: con.execute(f"ALTER TABLE entries ADD COLUMN {name} {definition}")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_source_key ON entries(source_key) WHERE source_key IS NOT NULL")
    con.execute("UPDATE entries SET category='note' WHERE category='action'")
    con.commit(); con.close()
init_db()

def now(): return datetime.now(timezone.utc).isoformat()
def log_activity(level, kind, message, details=""):
    db().execute("INSERT INTO activity(created_at,level,kind,message,details) VALUES(?,?,?,?,?)", (now(),level,kind,message,details)); db().commit()

def request_origin_allowed():
    origin=request.headers.get("Origin")
    if not origin:return True
    allowed=AUTH_ALLOWED_ORIGINS or {request.host_url.rstrip("/")}
    return origin.rstrip("/") in allowed

def session_token_hash(token): return hashlib.sha256(token.encode()).hexdigest()

def local_user():
    token=request.cookies.get(AUTH_COOKIE,"")
    if not token:return None
    row=db().execute("""SELECT s.*,u.username,u.enabled,u.session_version AS current_version FROM local_sessions s
      JOIN local_users u ON u.id=s.user_id WHERE s.token_hash=?""",(session_token_hash(token),)).fetchone()
    current=now(); idle_cutoff=(datetime.now(timezone.utc)-timedelta(days=AUTH_IDLE_DAYS)).isoformat()
    if not row or not row["enabled"] or row["session_version"]!=row["current_version"] or row["expires_at"]<=current or row["last_seen_at"]<=idle_cutoff:
        if row:db().execute("DELETE FROM local_sessions WHERE token_hash=?",(session_token_hash(token),)); db().commit()
        return None
    db().execute("UPDATE local_sessions SET last_seen_at=? WHERE token_hash=?",(current,row["token_hash"])); db().commit()
    return row

def api_auth(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if AUTH_PROVIDER == "firebase":
            if not PROJECT_ID:return jsonify(error="FIREBASE_PROJECT_ID is not configured"),503
            header=request.headers.get("Authorization","")
            if not header.startswith("Bearer "):return jsonify(error="Missing Firebase bearer token"),401
            try:claims=auth.verify_id_token(header[7:],check_revoked=True)
            except Exception:return jsonify(error="Invalid or expired Firebase token"),401
            email=claims.get("email","").lower()
            if REQUIRE_VERIFIED_EMAIL and not claims.get("email_verified"):return jsonify(error="Email address is not verified"),403
            if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:return jsonify(error="This account is not allowed"),403
            g.user={"id":claims.get("uid"),"username":email,"provider":"firebase","claims":claims}
        else:
            session=local_user()
            if not session:return jsonify(error="Authentication required"),401
            if request.method in {"POST","PUT","PATCH","DELETE"}:
                supplied=request.headers.get("X-CSRF-Token","")
                if not request_origin_allowed() or not supplied or not secrets.compare_digest(supplied,session["csrf_token"]):return jsonify(error="Invalid CSRF token"),403
            g.user={"id":str(session["user_id"]),"username":session["username"],"provider":"local"}; g.local_session=session
        return fn(*args,**kwargs)
    return wrapped

def login_limited(username,source_ip):
    cutoff=(datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat()
    db().execute("DELETE FROM login_attempts WHERE attempted_at<?",((datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),)); db().commit()
    return db().execute("""SELECT count(*) FROM login_attempts WHERE successful=0 AND attempted_at>=?
      AND (username=? OR source_ip=?)""",(cutoff,username,source_ip)).fetchone()[0]>=5

def record_login_attempt(username,source_ip,successful):
    db().execute("INSERT INTO login_attempts(attempted_at,username,source_ip,successful) VALUES(?,?,?,?)",(now(),username,source_ip,int(successful)))
    if successful:db().execute("DELETE FROM login_attempts WHERE successful=0 AND (username=? OR source_ip=?)",(username,source_ip))
    db().commit()

def create_local_session(user):
    token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32); created=now(); expires=(datetime.now(timezone.utc)+timedelta(days=AUTH_SESSION_DAYS)).isoformat()
    db().execute("DELETE FROM local_sessions WHERE expires_at<=?",(created,)); db().execute("""INSERT INTO local_sessions
      (token_hash,user_id,session_version,csrf_token,created_at,last_seen_at,expires_at) VALUES(?,?,?,?,?,?,?)""",
      (session_token_hash(token),user["id"],user["session_version"],csrf,created,created,expires)); db().commit()
    stale=db().execute("SELECT token_hash FROM local_sessions WHERE user_id=? ORDER BY created_at DESC LIMIT -1 OFFSET 10",(user["id"],)).fetchall()
    if stale:db().executemany("DELETE FROM local_sessions WHERE token_hash=?",((row["token_hash"],) for row in stale)); db().commit()
    response=jsonify(authenticated=True,username=user["username"],csrfToken=csrf)
    response.set_cookie(AUTH_COOKIE,token,secure=AUTH_COOKIE_SECURE,httponly=True,samesite="Lax",path="/",max_age=AUTH_SESSION_DAYS*86400)
    return response

def local_setup_required():return db().execute("SELECT count(*) FROM local_users").fetchone()[0]==0

@app.post("/auth/setup")
def local_setup():
    if AUTH_PROVIDER!="local":return jsonify(error="Local authentication is not enabled"),404
    if not request_origin_allowed():return jsonify(error="Invalid request origin"),403
    if not local_setup_required():return jsonify(error="Initial setup is already complete"),409
    if not LOCAL_SETUP_TOKEN:return jsonify(error="Web setup is not enabled; create a user from the command line"),503
    body=request.get_json(silent=True) or {}; supplied=str(body.get("setupToken","")); username=str(body.get("username","")).strip().lower()[:256]; password=str(body.get("password","")); confirmation=str(body.get("passwordConfirmation","")); source=request.remote_addr or ""
    if login_limited("__setup__",source):return jsonify(error="Too many setup attempts; try again later"),429
    token_valid=bool(supplied) and secrets.compare_digest(supplied,LOCAL_SETUP_TOKEN)
    if not token_valid:record_login_attempt("__setup__",source,False); return jsonify(error="Invalid setup token"),401
    if not username:return jsonify(error="Username is required"),400
    if len(password)<12:return jsonify(error="Password must be at least 12 characters"),400
    if len(password)>1024:return jsonify(error="Password is too long"),400
    if password!=confirmation:return jsonify(error="Passwords do not match"),400
    connection=db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT count(*) FROM local_users").fetchone()[0]:connection.rollback(); return jsonify(error="Initial setup is already complete"),409
        stamp=now(); password_hash=PASSWORD_HASHER.hash(password)
        cursor=connection.execute("INSERT INTO local_users(username,password_hash,created_at,password_changed_at) VALUES(?,?,?,?)",(username,password_hash,stamp,stamp)); connection.commit()
    except sqlite3.IntegrityError:connection.rollback(); return jsonify(error="Unable to create owner account"),409
    record_login_attempt("__setup__",source,True)
    user=connection.execute("SELECT * FROM local_users WHERE id=?",(cursor.lastrowid,)).fetchone()
    return create_local_session(user),201

@app.post("/auth/login")
def local_login():
    if AUTH_PROVIDER!="local":return jsonify(error="Local authentication is not enabled"),404
    if not request_origin_allowed():return jsonify(error="Invalid request origin"),403
    body=request.get_json(silent=True) or {}; username=str(body.get("username","")).strip().lower()[:256]; password=str(body.get("password","")); source=request.remote_addr or ""
    if len(password)>1024:return jsonify(error="Invalid username or password"),401
    if login_limited(username,source):return jsonify(error="Too many login attempts; try again later"),429
    user=db().execute("SELECT * FROM local_users WHERE username=?",(username,)).fetchone(); password_matches=False
    try:password_matches=bool(user and PASSWORD_HASHER.verify(user["password_hash"],password))
    except (VerifyMismatchError,InvalidHashError):pass
    if not user:
        try:PASSWORD_HASHER.verify(DUMMY_PASSWORD_HASH,password)
        except VerifyMismatchError:pass
    valid=bool(user and user["enabled"] and password_matches)
    record_login_attempt(username,source,valid)
    if not valid:return jsonify(error="Invalid username or password"),401
    return create_local_session(user)

@app.get("/auth/session")
def local_session_status():
    if AUTH_PROVIDER!="local":return jsonify(authenticated=False,provider=AUTH_PROVIDER)
    session=local_user()
    if not session:return jsonify(authenticated=False,provider="local",setupRequired=local_setup_required(),setupAvailable=bool(LOCAL_SETUP_TOKEN)),401
    return jsonify(authenticated=True,provider="local",username=session["username"],csrfToken=session["csrf_token"])

@app.post("/auth/logout")
def local_logout():
    if AUTH_PROVIDER!="local":return jsonify(error="Local authentication is not enabled"),404
    session=local_user()
    if session:
        supplied=request.headers.get("X-CSRF-Token","")
        if not request_origin_allowed() or not supplied or not secrets.compare_digest(supplied,session["csrf_token"]):return jsonify(error="Invalid CSRF token"),403
        db().execute("DELETE FROM local_sessions WHERE token_hash=?",(session["token_hash"],)); db().commit()
    response=jsonify(ok=True); response.delete_cookie(AUTH_COOKIE,path="/",secure=AUTH_COOKIE_SECURE,httponly=True,samesite="Lax"); return response

def webhook_authorized():
    supplied = request.headers.get("X-Webhook-Secret", ""); bearer = request.headers.get("Authorization", "")
    if bearer.startswith("Bearer "): supplied = bearer[7:]
    supplied = request.args.get("token", supplied)
    return bool(WEBHOOK_SECRET) and secrets.compare_digest(supplied, WEBHOOK_SECRET)

def first(payload, names, default=""):
    for name in names:
        value = payload.get(name)
        if value is not None and value != "": return str(value)
    return default

def normalize_timestamp(value):
    if value is None: return None
    text = str(value).strip()
    try:
        number = float(text); seconds = number / 1000 if number >= 100_000_000_000 else number
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError): return text

def voice_category(text):
    aliases={"note":"note","idea":"idea","task":"task","todo":"task","to-do":"task","reminder":"task","question":"question"}
    match=re.match(r"^\s*(note|idea|task|todo|to-do|reminder|question)(?:\s*[:.,-]\s*|\s+)(.+)$",text,re.IGNORECASE|re.DOTALL)
    return (aliases[match.group(1).lower()],match.group(2).strip()) if match else ("note",text)

def normalized_group_name(value):
    value=str(value).strip()
    return value.upper() if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}",value) else None

DIGIT_WORDS={"zero":"0","oh":"0","one":"1","two":"2","three":"3","four":"4","five":"5","six":"6","seven":"7","eight":"8","nine":"9"}

def canonical_group_phrase(value):
    value=str(value).strip().rstrip(".!").strip(); direct=normalized_group_name(value)
    if direct:return direct
    tokens=value.lower().split(); number_at=next((i for i,token in enumerate(tokens) if token in DIGIT_WORDS or token.isdigit()),None)
    if number_at is None or number_at==0:return None
    prefix=tokens[:number_at]; numbers=tokens[number_at:]
    if not all(token.isalpha() and token not in DIGIT_WORDS for token in prefix):return None
    if not all(token in DIGIT_WORDS or token.isdigit() for token in numbers):return None
    return normalized_group_name("".join(prefix)+"".join(DIGIT_WORDS.get(token,token) for token in numbers))

def create_group_command(text):
    match=re.fullmatch(r"\s*create\s+(.+?)\s*",text,re.IGNORECASE)
    return canonical_group_phrase(match.group(1)) if match else None

def group_spoken_aliases(name):
    match=re.fullmatch(r"([A-Za-z_-]+)(\d+)",name)
    if not match:return []
    prefix=match.group(1); spoken=" ".join(next(word for word,digit in DIGIT_WORDS.items() if digit==value and word!="oh") for value in match.group(2))
    return [f"{prefix} {spoken}",f"{' '.join(prefix)} {spoken}"]

def match_note_group(text):
    candidate=re.sub(r"^\s*add\s+to\s+","",text,count=1,flags=re.IGNORECASE)
    groups=db().execute("SELECT display_name FROM note_groups WHERE archived=0 ORDER BY length(display_name) DESC").fetchall()
    for row in groups:
        aliases=[row["display_name"],*group_spoken_aliases(row["display_name"])]
        for alias in aliases:
            pattern=r"^\s*"+r"\s+".join(re.escape(part) for part in alias.split())+r"(?:\s*[:.,-]\s*|\s+)(.+)$"
            match=re.match(pattern,candidate,re.IGNORECASE|re.DOTALL)
            if match:return row["display_name"],match.group(1).strip()
    return None,text

def payload_from_request():
    payload = request.get_json(silent=True) or request.form.to_dict(flat=True)
    if not payload and request.data:
        try: payload = json.loads(request.data)
        except Exception: payload = {"raw": request.data.decode("utf-8", errors="replace")}
    return payload or {}

def store_entry(payload, upload=None, source="ring"):
    entry_id = str(uuid.uuid4()); recorded = normalize_timestamp(first(payload,("recorded_at","recordedAt","timestamp","created_at"),None))
    transcription = first(payload,("transcription","transcript","text","content","note")); trigger = first(payload,("trigger","trigger_type","triggerType","mode","click_type"),source)
    external_id = first(payload,("id","recordingId","recording_id","uuid","eventId"),"")
    basis = external_id or (json.dumps(payload,sort_keys=True,separators=(",",":")) if recorded else "")
    source_key = hashlib.sha256(f"{source}:{basis}".encode()).hexdigest() if basis else None
    if source_key:
        existing = db().execute("SELECT id FROM entries WHERE source_key=?",(source_key,)).fetchone()
        if existing: log_activity("info","duplicate","Duplicate webhook ignored",existing["id"]); return {"id":existing["id"],"created":False,"duplicate":True}
    title=first(payload,("title",),""); explicit_category=first(payload,("category",),"")
    category,cleaned=voice_category(transcription)
    if explicit_category in VALID_CATEGORIES: category=explicit_category
    elif source=="ring": transcription=cleaned
    group_to_create=create_group_command(transcription)
    if group_to_create:
        cursor=db().execute("INSERT OR IGNORE INTO note_groups(name,display_name,created_at) VALUES(?,?,?)",(group_to_create,group_to_create,now())); db().commit()
        created=bool(cursor.rowcount); log_activity("info","group",f"{'Created' if created else 'Group already exists'} {group_to_create}",group_to_create)
        return {"group":group_to_create,"groupCreated":created,"created":created,"duplicate":not created}
    group_name,transcription=match_note_group(transcription)
    audio_path=audio_mime=None
    if upload and upload.filename:
        suffix=Path(upload.filename).suffix.lower()[:10] or ".bin"; audio_path=f"{entry_id}{suffix}"; audio_mime=upload.mimetype or "application/octet-stream"; upload.save(AUDIO_DIR/audio_path)
    db().execute("""INSERT INTO entries(id,created_at,recorded_at,transcription,trigger_type,audio_path,audio_mime,payload_json,source_key,title,category,group_name)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(entry_id,now(),recorded,transcription,trigger,audio_path,audio_mime,json.dumps(payload,ensure_ascii=False),source_key,title,category,group_name)); db().commit()
    message=f"Captured {source} entry"+(f" for {group_name}" if group_name else "")
    log_activity("info","ingest",message,entry_id); return {"id":entry_id,"created":True,"duplicate":False,"group":group_name}

@app.get("/health")
def health():
    try: db().execute("SELECT 1"); return jsonify(ok=True)
    except sqlite3.Error: return jsonify(ok=False),503

@app.post("/webhook/index")
def ingest():
    if not webhook_authorized():
        log_activity("warning","auth","Rejected webhook authentication",request.remote_addr or ""); return jsonify(error="Invalid webhook secret"),401
    payload=payload_from_request(); upload=next((request.files[k] for k in request.files if request.files[k].filename),None)
    result=store_entry(payload,upload); return jsonify(ok=True,**result),(201 if result["created"] else 200)

@app.get("/api/groups")
@api_auth
def groups():return jsonify([dict(row) for row in db().execute("""SELECT g.display_name AS name,g.created_at,g.archived,count(e.id) AS entries
  FROM note_groups g LEFT JOIN entries e ON e.group_name=g.display_name GROUP BY g.name ORDER BY g.archived,g.display_name""")])

@app.get("/api/entries")
@api_auth
def entries():
    where=[]; values=[]
    q=request.args.get("q","").strip()
    if q: where.append("(transcription LIKE ? OR title LIKE ? OR tags LIKE ?)"); values += [f"%{q}%"]*3
    for field in ("category","processed","starred","archived","group_name"):
        if request.args.get(field) not in (None,""): where.append(f"{field}=?"); values.append(request.args[field])
    page=max(int(request.args.get("page",1)),1); limit=min(max(int(request.args.get("limit",50)),1),200); clause=" WHERE "+" AND ".join(where) if where else ""
    total=db().execute("SELECT count(*) FROM entries"+clause,values).fetchone()[0]
    rows=db().execute("SELECT * FROM entries"+clause+" ORDER BY created_at DESC LIMIT ? OFFSET ?",(*values,limit,(page-1)*limit)).fetchall()
    return jsonify(items=[dict(r) for r in rows],page=page,limit=limit,total=total,pages=max(1,(total+limit-1)//limit))

@app.patch("/api/entries/<entry_id>")
@api_auth
def update_entry(entry_id):
    body=request.get_json(force=True); allowed={"starred","processed","archived","tags","transcription","title","category"}; updates={k:body[k] for k in body if k in allowed}
    if "category" in updates and updates["category"] not in VALID_CATEGORIES:return jsonify(error="Invalid category"),400
    if not updates:return jsonify(error="No supported fields supplied"),400
    values=[int(v) if k in {"starred","processed","archived"} else str(v) for k,v in updates.items()]
    cur=db().execute(f"UPDATE entries SET {', '.join(k+'=?' for k in updates)} WHERE id=?",(*values,entry_id)); db().commit()
    return (jsonify(ok=True) if cur.rowcount else (jsonify(error="Not found"),404))

@app.post("/api/entries/bulk")
@api_auth
def bulk():
    body=request.get_json(force=True); ids=[str(x) for x in body.get("ids",[])][:500]; action=body.get("action")
    mapping={"archive":("archived",1),"restore":("archived",0),"process":("processed",1),"unprocess":("processed",0),"star":("starred",1),"unstar":("starred",0)}
    if not ids or action not in mapping:return jsonify(error="Invalid bulk request"),400
    field,value=mapping[action]; marks=",".join("?"*len(ids)); cur=db().execute(f"UPDATE entries SET {field}=? WHERE id IN ({marks})",(value,*ids)); db().commit(); return jsonify(ok=True,updated=cur.rowcount)

def remove_entry(entry_id):
    row=db().execute("SELECT audio_path FROM entries WHERE id=?",(entry_id,)).fetchone()
    if not row:return False
    db().execute("DELETE FROM entries WHERE id=?",(entry_id,)); db().commit()
    if row["audio_path"]:
        try:(AUDIO_DIR/row["audio_path"]).unlink(missing_ok=True)
        except OSError as error:log_activity("warning","cleanup","Audio cleanup failed",str(error))
    return True

@app.delete("/api/entries/<entry_id>")
@api_auth
def delete_entry(entry_id): return jsonify(ok=True) if remove_entry(entry_id) else (jsonify(error="Not found"),404)

@app.delete("/api/entries")
@api_auth
def delete_bulk():
    ids=[str(x) for x in (request.get_json(force=True).get("ids") or [])][:500]; return jsonify(ok=True,deleted=sum(remove_entry(x) for x in ids))

@app.get("/api/entries/<entry_id>/audio")
@api_auth
def audio(entry_id):
    row=db().execute("SELECT audio_path,audio_mime FROM entries WHERE id=?",(entry_id,)).fetchone()
    if not row or not row["audio_path"]:return jsonify(error="Audio not found"),404
    return send_file(AUDIO_DIR/row["audio_path"],mimetype=row["audio_mime"],download_name=row["audio_path"])

@app.post("/api/manual")
@api_auth
def manual():
    payload=payload_from_request(); upload=next((request.files[k] for k in request.files if request.files[k].filename),None); result=store_entry(payload,upload,"manual"); return jsonify(ok=True,**result),(201 if result["created"] else 200)

@app.get("/api/activity")
@api_auth
def activity(): return jsonify([dict(r) for r in db().execute("SELECT * FROM activity ORDER BY id DESC LIMIT 100")])

@app.get("/api/changes")
@api_auth
def changes():return jsonify(sequence=db().execute("SELECT coalesce(max(id),0) FROM activity").fetchone()[0])

@app.get("/api/status")
@api_auth
def status():
    count=db().execute("SELECT count(*) FROM entries").fetchone()[0]; audio_count=db().execute("SELECT count(*) FROM entries WHERE audio_path IS NOT NULL").fetchone()[0]
    audio_bytes=sum(p.stat().st_size for p in AUDIO_DIR.iterdir() if p.is_file()); db_bytes=DB_PATH.stat().st_size if DB_PATH.exists() else 0
    return jsonify(entries=count,audioEntries=audio_count,audioBytes=audio_bytes,databaseBytes=db_bytes,lastBackupHook=BACKUP_HOOK_URL!="")

def export_rows(): return [dict(r) for r in db().execute("SELECT * FROM entries ORDER BY created_at DESC")]

@app.get("/api/export/<fmt>")
@api_auth
def export(fmt):
    rows=export_rows()
    if fmt=="json": return Response(json.dumps(rows,indent=2,ensure_ascii=False),headers={"Content-Disposition":"attachment; filename=index-inbox.json"},mimetype="application/json")
    if fmt=="markdown":
        text="\n\n".join(f"## {r['recorded_at'] or r['created_at']}\n\n{r['transcription']}\n\nTags: {r['tags']}" for r in rows)
        return Response(text,headers={"Content-Disposition":"attachment; filename=index-inbox.md"},mimetype="text/markdown")
    if fmt=="zip":
        out=io.BytesIO()
        with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
            z.writestr("entries.json",json.dumps(rows,indent=2,ensure_ascii=False))
            for r in rows:
                if r["audio_path"] and (AUDIO_DIR/r["audio_path"]).exists():z.write(AUDIO_DIR/r["audio_path"],f"audio/{r['audio_path']}")
        out.seek(0); return send_file(out,mimetype="application/zip",as_attachment=True,download_name="index-inbox.zip")
    return jsonify(error="Use json, markdown, or zip"),400

@app.post("/api/maintenance/retention")
@api_auth
def retention():
    days=max(int((request.get_json(silent=True) or {}).get("audioDays",30)),1); cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    rows=db().execute("SELECT id,audio_path FROM entries WHERE audio_path IS NOT NULL AND created_at<?",(cutoff,)).fetchall(); removed=0
    for row in rows:
        (AUDIO_DIR/row["audio_path"]).unlink(missing_ok=True); db().execute("UPDATE entries SET audio_path=NULL,audio_mime=NULL WHERE id=?",(row["id"],)); removed+=1
    db().commit(); log_activity("info","retention",f"Removed {removed} old audio files"); return jsonify(ok=True,removed=removed)

@app.post("/api/backup-hook")
@api_auth
def backup_hook():
    if not BACKUP_HOOK_URL:return jsonify(error="BACKUP_HOOK_URL is not configured"),400
    try:
        req=urllib.request.Request(BACKUP_HOOK_URL,data=json.dumps({"event":"index-inbox.backup","at":now()}).encode(),headers={"Content-Type":"application/json"}); urllib.request.urlopen(req,timeout=10).read(); log_activity("info","backup","Backup hook triggered"); return jsonify(ok=True)
    except Exception as error:log_activity("error","backup","Backup hook failed",str(error)); return jsonify(error=str(error)),502

@app.get("/config.js")
def config_js():
    config={"authProvider":AUTH_PROVIDER}
    if AUTH_PROVIDER=="firebase":config["firebase"]={"apiKey":os.getenv("FIREBASE_API_KEY",""),"authDomain":os.getenv("FIREBASE_AUTH_DOMAIN",""),"projectId":PROJECT_ID}
    return Response(f"window.INDEX_INBOX_CONFIG={json.dumps(config)};",mimetype="application/javascript",headers={"Cache-Control":"no-store"})

@app.after_request
def private_api_cache(response):
    if request.path.startswith(("/api/","/auth/")):response.headers["Cache-Control"]="private, no-store"
    return response

@app.cli.group("auth")
def auth_cli(): """Manage local Index Inbox accounts."""

@auth_cli.command("create-user")
@click.option("--username",prompt=True)
def create_local_user(username):
    username=username.strip().lower()
    if not username:raise click.ClickException("Username cannot be empty")
    password=click.prompt("Password",hide_input=True,confirmation_prompt=True)
    if len(password)<12:raise click.ClickException("Password must be at least 12 characters")
    stamp=now()
    try:db().execute("INSERT INTO local_users(username,password_hash,created_at,password_changed_at) VALUES(?,?,?,?)",(username,PASSWORD_HASHER.hash(password),stamp,stamp)); db().commit()
    except sqlite3.IntegrityError:raise click.ClickException("That username already exists")
    click.echo(f"Created local user {username}")

@auth_cli.command("change-password")
@click.option("--username",prompt=True)
def change_local_password(username):
    username=username.strip().lower(); user=db().execute("SELECT id FROM local_users WHERE username=?",(username,)).fetchone()
    if not user:raise click.ClickException("Local user not found")
    password=click.prompt("New password",hide_input=True,confirmation_prompt=True)
    if len(password)<12:raise click.ClickException("Password must be at least 12 characters")
    db().execute("UPDATE local_users SET password_hash=?,password_changed_at=?,session_version=session_version+1 WHERE id=?",(PASSWORD_HASHER.hash(password),now(),user["id"])); db().execute("DELETE FROM local_sessions WHERE user_id=?",(user["id"],)); db().commit(); click.echo(f"Password changed and sessions revoked for {username}")

@auth_cli.command("revoke-sessions")
@click.option("--username",prompt=True)
def revoke_local_sessions(username):
    username=username.strip().lower(); user=db().execute("SELECT id FROM local_users WHERE username=?",(username,)).fetchone()
    if not user:raise click.ClickException("Local user not found")
    db().execute("UPDATE local_users SET session_version=session_version+1 WHERE id=?",(user["id"],)); db().execute("DELETE FROM local_sessions WHERE user_id=?",(user["id"],)); db().commit(); click.echo(f"Sessions revoked for {username}")

@auth_cli.command("list-users")
def list_local_users():
    rows=db().execute("SELECT username,enabled,created_at FROM local_users ORDER BY username").fetchall()
    if not rows:click.echo("No local users")
    for row in rows:click.echo(f"{row['username']}\t{'enabled' if row['enabled'] else 'disabled'}\t{row['created_at']}")

@auth_cli.command("disable-user")
@click.option("--username",prompt=True)
def disable_local_user(username):
    username=username.strip().lower(); user=db().execute("SELECT id,enabled FROM local_users WHERE username=?",(username,)).fetchone()
    if not user:raise click.ClickException("Local user not found")
    if user["enabled"] and db().execute("SELECT count(*) FROM local_users WHERE enabled=1").fetchone()[0]<=1:raise click.ClickException("Cannot disable the final enabled local user")
    db().execute("UPDATE local_users SET enabled=0,session_version=session_version+1 WHERE id=?",(user["id"],)); db().execute("DELETE FROM local_sessions WHERE user_id=?",(user["id"],)); db().commit(); click.echo(f"Disabled {username} and revoked its sessions")

@auth_cli.command("enable-user")
@click.option("--username",prompt=True)
def enable_local_user(username):
    username=username.strip().lower(); cur=db().execute("UPDATE local_users SET enabled=1 WHERE username=?",(username,)); db().commit()
    if not cur.rowcount:raise click.ClickException("Local user not found")
    click.echo(f"Enabled {username}")
@app.get("/")
def index():return send_from_directory("static","index.html")
@app.get("/<path:path>")
def static_files(path):return send_from_directory("static",path)
