import csv, hashlib, io, ipaddress, json, os, re, secrets, sqlite3, urllib.request, uuid, zipfile
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
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
TRUSTED_PROXY_HOPS = max(int(os.getenv("TRUSTED_PROXY_HOPS", "0")), 0)
try:TRUSTED_PROXY_NETWORKS=tuple(ipaddress.ip_network(value.strip(),strict=False) for value in os.getenv("TRUSTED_PROXY_CIDRS","").split(",") if value.strip())
except ValueError as error:raise RuntimeError(f"Invalid TRUSTED_PROXY_CIDRS: {error}") from error
if TRUSTED_PROXY_HOPS and not TRUSTED_PROXY_NETWORKS:raise RuntimeError("TRUSTED_PROXY_CIDRS is required when TRUSTED_PROXY_HOPS is greater than zero")
AUTH_COOKIE = "__Host-index_session" if AUTH_COOKIE_SECURE else "index_session"
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("index-inbox-dummy-password")
MAX_AUDIO_BYTES = int(os.getenv("MAX_AUDIO_MB", "25")) * 1024 * 1024
BACKUP_HOOK_URL = os.getenv("BACKUP_HOOK_URL", "")
VALID_CATEGORIES = {"note", "task", "idea", "question"}
CAPTURE_EVENT_KINDS = {"capture_standalone", "capture_grouped", "group_created", "group_exists",
  "group_unrecognized", "webhook_rejected", "ingest_error"}
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
        username TEXT NOT NULL, source_ip TEXT NOT NULL, peer_ip TEXT NOT NULL DEFAULT '', successful INTEGER NOT NULL DEFAULT 0);
      CREATE INDEX IF NOT EXISTS idx_login_attempts_lookup ON login_attempts(username,source_ip,attempted_at);
      CREATE TABLE IF NOT EXISTS note_groups (name TEXT PRIMARY KEY COLLATE NOCASE, display_name TEXT NOT NULL,
        created_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS note_group_aliases (alias TEXT PRIMARY KEY COLLATE NOCASE, group_name TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS group_suggestion_dismissals (entry_id TEXT NOT NULL, group_name TEXT NOT NULL,
        dismissed_at TEXT NOT NULL, PRIMARY KEY(entry_id,group_name));
    """)
    columns = {r[1] for r in con.execute("PRAGMA table_info(entries)")}
    additions = {"title":"TEXT NOT NULL DEFAULT ''", "category":"TEXT NOT NULL DEFAULT 'note'",
      "archived":"INTEGER NOT NULL DEFAULT 0", "source_key":"TEXT", "group_name":"TEXT"}
    for name, definition in additions.items():
        if name not in columns: con.execute(f"ALTER TABLE entries ADD COLUMN {name} {definition}")
    login_columns={r[1] for r in con.execute("PRAGMA table_info(login_attempts)")}
    if "peer_ip" not in login_columns:con.execute("ALTER TABLE login_attempts ADD COLUMN peer_ip TEXT NOT NULL DEFAULT ''")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_source_key ON entries(source_key) WHERE source_key IS NOT NULL")
    con.execute("INSERT OR IGNORE INTO note_group_aliases(alias,group_name) SELECT lower(display_name),display_name FROM note_groups")
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

def request_client_addresses():
    peer=request.remote_addr or ""
    if not TRUSTED_PROXY_HOPS:return peer,peer
    try:peer_address=ipaddress.ip_address(peer)
    except ValueError:return peer,peer
    if not any(peer_address in network for network in TRUSTED_PROXY_NETWORKS):return peer,peer
    cloudflare=request.headers.get("CF-Connecting-IP","").strip()
    if TRUSTED_PROXY_HOPS==1 and cloudflare:
        try:return str(ipaddress.ip_address(cloudflare)),peer
        except ValueError:return peer,peer
    forwarded=[part.strip() for part in request.headers.get("X-Forwarded-For","").split(",") if part.strip()]
    if len(forwarded)<TRUSTED_PROXY_HOPS:return peer,peer
    try:return str(ipaddress.ip_address(forwarded[-TRUSTED_PROXY_HOPS])),peer
    except ValueError:return peer,peer

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

def record_login_attempt(username,source_ip,successful,peer_ip=""):
    db().execute("INSERT INTO login_attempts(attempted_at,username,source_ip,successful,peer_ip) VALUES(?,?,?,?,?)",(now(),username,source_ip,int(successful),peer_ip))
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
    body=request.get_json(silent=True) or {}; supplied=str(body.get("setupToken","")); username=str(body.get("username","")).strip().lower()[:256]; password=str(body.get("password","")); confirmation=str(body.get("passwordConfirmation","")); source,peer=request_client_addresses()
    if login_limited("__setup__",source):return jsonify(error="Too many setup attempts; try again later"),429
    token_valid=bool(supplied) and secrets.compare_digest(supplied,LOCAL_SETUP_TOKEN)
    if not token_valid:record_login_attempt("__setup__",source,False,peer); return jsonify(error="Invalid setup token"),401
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
    record_login_attempt("__setup__",source,True,peer)
    user=connection.execute("SELECT * FROM local_users WHERE id=?",(cursor.lastrowid,)).fetchone()
    return create_local_session(user),201

@app.post("/auth/login")
def local_login():
    if AUTH_PROVIDER!="local":return jsonify(error="Local authentication is not enabled"),404
    if not request_origin_allowed():return jsonify(error="Invalid request origin"),403
    body=request.get_json(silent=True) or {}; username=str(body.get("username","")).strip().lower()[:256]; password=str(body.get("password","")); source,peer=request_client_addresses()
    if len(password)>1024:return jsonify(error="Invalid username or password"),401
    if login_limited(username,source):return jsonify(error="Too many login attempts; try again later"),429
    user=db().execute("SELECT * FROM local_users WHERE username=?",(username,)).fetchone(); password_matches=False
    try:password_matches=bool(user and PASSWORD_HASHER.verify(user["password_hash"],password))
    except (VerifyMismatchError,InvalidHashError):pass
    if not user:
        try:PASSWORD_HASHER.verify(DUMMY_PASSWORD_HASH,password)
        except VerifyMismatchError:pass
    valid=bool(user and user["enabled"] and password_matches)
    record_login_attempt(username,source,valid,peer)
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

def normalized_group_alias(value):
    value=re.sub(r"\s+"," ",str(value).strip().rstrip(".! ")).lower()
    return value if 1<=len(value)<=96 and re.fullmatch(r"[a-z0-9_-]+(?: [a-z0-9_-]+)*",value) else None

DIGIT_WORDS={"zero":"0","oh":"0","one":"1","two":"2","three":"3","four":"4","five":"5","six":"6","seven":"7","eight":"8","nine":"9"}
NUMBER_UNITS={word:int(value) for word,value in DIGIT_WORDS.items() if word!="oh"}; NUMBER_UNITS["oh"]=0
NUMBER_TENS={"ten":10,"eleven":11,"twelve":12,"thirteen":13,"fourteen":14,"fifteen":15,"sixteen":16,"seventeen":17,"eighteen":18,"nineteen":19,
  "twenty":20,"thirty":30,"forty":40,"fifty":50,"sixty":60,"seventy":70,"eighty":80,"ninety":90}

def parse_spoken_number(tokens):
    if not tokens:return None
    if len(tokens)>1 and all(token in DIGIT_WORDS for token in tokens):return int("".join(DIGIT_WORDS[token] for token in tokens))
    total=current=0
    for token in tokens:
        if token.isdigit():current+=int(token)
        elif token in NUMBER_UNITS:current+=NUMBER_UNITS[token]
        elif token in NUMBER_TENS:current+=NUMBER_TENS[token]
        elif token=="hundred":current=max(current,1)*100
        elif token=="thousand":total+=max(current,1)*1000; current=0
        else:return None
    return total+current

def number_words(value):
    value=int(value)
    if value<10:return next(word for word,number in NUMBER_UNITS.items() if number==value and word!="oh")
    if value<20:return next(word for word,number in NUMBER_TENS.items() if number==value)
    if value<100:
        tens=(value//10)*10; word=next(word for word,number in NUMBER_TENS.items() if number==tens)
        return word+(" "+number_words(value%10) if value%10 else "")
    if value<1000:return number_words(value//100)+" hundred"+(" "+number_words(value%100) if value%100 else "")
    if value<1_000_000:return number_words(value//1000)+" thousand"+(" "+number_words(value%1000) if value%1000 else "")
    return " ".join(number_words(int(digit)) for digit in str(value))

def group_identity(value):
    raw=re.sub(r"\s+"," ",str(value).strip().rstrip(".! ")).lower(); direct=normalized_group_name(raw)
    prefix_words=[]; number=None
    if direct:
        match=re.fullmatch(r"([A-Za-z_-]+)(\d+)",direct)
        if match:prefix_words=[match.group(1).lower()]; number=int(match.group(2))
        else:return direct,{raw,direct.lower()}
    else:
        tokens=raw.replace("-"," ").split(); number_at=next((i for i,token in enumerate(tokens) if token.isdigit() or token in NUMBER_UNITS or token in NUMBER_TENS or token in {"hundred","thousand"}),None)
        if number_at is None or number_at==0:return None,set()
        prefix_words=tokens[:number_at]; number=parse_spoken_number(tokens[number_at:])
        if number is None or not all(re.fullmatch(r"[a-z_]+",token) for token in prefix_words):return None,set()
        direct=normalized_group_name("".join(prefix_words)+str(number))
    if not direct:return None,set()
    prefix=" ".join(prefix_words); digit_words=" ".join(number_words(int(digit)) for digit in str(number))
    return direct,{raw,direct.lower(),f"{prefix} {number}",f"{prefix} {number_words(number)}",f"{prefix} {digit_words}"}

def create_group_command(text):
    match=re.fullmatch(r"\s*create\s+(.+?)\s*",text,re.IGNORECASE)
    if not match:return None
    identity=group_identity(match.group(1))
    return identity if identity[0] else None

def match_note_group(text):
    candidate=re.sub(r"^\s*add\s+to\s+","",text,count=1,flags=re.IGNORECASE)
    aliases=db().execute("""SELECT a.alias,g.display_name FROM note_group_aliases a JOIN note_groups g ON g.name=a.group_name
      WHERE g.archived=0 ORDER BY length(a.alias) DESC""").fetchall()
    for row in aliases:
        pattern=r"^\s*"+r"\s+".join(re.escape(part) for part in row["alias"].split())+r"(?:\s*[:.,-]\s*|\s+)(.+)$"
        match=re.match(pattern,candidate,re.IGNORECASE|re.DOTALL)
        if match:return row["display_name"],match.group(1).strip()
    return None,text

def leading_group_candidate(text):
    tokens=re.sub(r"^\s*add\s+to\s+","",str(text),count=1,flags=re.IGNORECASE).strip().split()
    best=None
    for size in range(1,min(len(tokens),8)+1):
        identity=group_identity(" ".join(tokens[:size]).rstrip(":,."))
        if identity[0] and re.search(r"\d",identity[0]):best=(identity[0]," ".join(tokens[size:]).lstrip(":.,- "))
    return best or (None,str(text))

def suggested_group_for(text):
    candidate,remainder=leading_group_candidate(text)
    match=re.fullmatch(r"([A-Z_-]+)(\d+)",candidate or "")
    if not match or not remainder:return None
    candidate_name,candidate_number=match.groups(); best=None
    for row in db().execute("SELECT display_name FROM note_groups WHERE archived=0"):
        target=re.fullmatch(r"([A-Z_-]+)(\d+)",row["display_name"])
        if not target or target.group(2)!=candidate_number or target.group(1)==candidate_name:continue
        score=SequenceMatcher(None,candidate_name,target.group(1)).ratio()
        if score>=0.8 and abs(len(candidate_name)-len(target.group(1)))<=2 and (best is None or score>best["score"]):
            best={"group":row["display_name"],"candidate":candidate,"suggestedText":remainder,"score":round(score,3)}
    return best

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
    group_command=create_group_command(transcription); unrecognized_group_command=bool(re.match(r"^\s*create\b",transcription,re.IGNORECASE)) and not group_command
    if group_command:
        group_to_create,aliases=group_command; cursor=db().execute("INSERT OR IGNORE INTO note_groups(name,display_name,created_at) VALUES(?,?,?)",(group_to_create,group_to_create,now()))
        db().executemany("INSERT OR IGNORE INTO note_group_aliases(alias,group_name) VALUES(?,?)",((alias,group_to_create) for alias in aliases)); db().commit()
        created=bool(cursor.rowcount); log_activity("info","group_created" if created else "group_exists",f"{'Created group' if created else 'Group already exists:'} {group_to_create}",group_to_create)
        return {"group":group_to_create,"groupCreated":created,"created":created,"duplicate":not created}
    group_name,transcription=match_note_group(transcription)
    audio_path=audio_mime=None
    if upload and upload.filename:
        suffix=Path(upload.filename).suffix.lower()[:10] or ".bin"; audio_path=f"{entry_id}{suffix}"; audio_mime=upload.mimetype or "application/octet-stream"; upload.save(AUDIO_DIR/audio_path)
    db().execute("""INSERT INTO entries(id,created_at,recorded_at,transcription,trigger_type,audio_path,audio_mime,payload_json,source_key,title,category,group_name)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(entry_id,now(),recorded,transcription,trigger,audio_path,audio_mime,json.dumps(payload,ensure_ascii=False),source_key,title,category,group_name)); db().commit()
    if unrecognized_group_command:
        log_activity("warning","group_unrecognized","Could not create a group from that command",entry_id)
    elif group_name:
        log_activity("info","capture_grouped",f"Added a note to {group_name}",entry_id)
    else:
        log_activity("info","capture_standalone","Added a standalone note",entry_id)
    return {"id":entry_id,"created":True,"duplicate":False,"group":group_name}

@app.get("/health")
def health():
    try: db().execute("SELECT 1"); return jsonify(ok=True)
    except sqlite3.Error: return jsonify(ok=False),503

@app.post("/webhook/index")
def ingest():
    if not webhook_authorized():
        source,peer=request_client_addresses(); log_activity("warning","webhook_rejected","Rejected a webhook with invalid authentication",json.dumps({"client":source,"peer":peer})); return jsonify(error="Invalid webhook secret"),401
    try:
        payload=payload_from_request(); upload=next((request.files[k] for k in request.files if request.files[k].filename),None)
        result=store_entry(payload,upload); return jsonify(ok=True,**result),(201 if result["created"] else 200)
    except Exception as error:
        log_activity("error","ingest_error","A webhook could not be stored",str(error)); return jsonify(error="Webhook ingestion failed"),500

@app.get("/api/groups")
@api_auth
def groups():return jsonify([dict(row) for row in db().execute("""SELECT g.display_name AS name,g.created_at,g.archived,count(e.id) AS entries
  FROM note_groups g LEFT JOIN entries e ON e.group_name=g.display_name GROUP BY g.name ORDER BY g.archived,g.display_name""")])

@app.patch("/api/groups/<name>")
@api_auth
def update_group(name):
    current=normalized_group_name(name); body=request.get_json(force=True); connection=db()
    row=connection.execute("SELECT * FROM note_groups WHERE name=?",(current,)).fetchone() if current else None
    if not row:return jsonify(error="Group not found"),404
    target=current; renamed=False
    if "name" in body:
        target=normalized_group_name(body["name"])
        if not target:return jsonify(error="Group names must be 1-32 letters, numbers, hyphens or underscores"),400
        if target!=current and connection.execute("SELECT 1 FROM note_groups WHERE name=?",(target,)).fetchone():return jsonify(error="A group with that name already exists"),409
        alias_owner=connection.execute("SELECT group_name FROM note_group_aliases WHERE alias=?",(target.lower(),)).fetchone()
        if alias_owner and alias_owner["group_name"].lower()!=row["display_name"].lower():return jsonify(error="That name conflicts with another group's alias"),409
    if "archived" in body and not isinstance(body["archived"],bool):return jsonify(error="archived must be true or false"),400
    archived=int(body["archived"]) if "archived" in body else row["archived"]
    if target==current and archived==row["archived"]:return jsonify(ok=True,name=row["display_name"],archived=bool(archived))
    try:
        connection.execute("BEGIN IMMEDIATE")
        if target!=current:
            connection.execute("UPDATE entries SET group_name=? WHERE group_name=?",(target,row["display_name"]))
            connection.execute("UPDATE note_group_aliases SET group_name=? WHERE group_name=?",(target,row["display_name"]))
            connection.execute("UPDATE group_suggestion_dismissals SET group_name=? WHERE group_name=?",(target,row["display_name"]))
            connection.execute("UPDATE note_groups SET name=?,display_name=? WHERE name=?",(target,target,current))
            connection.execute("INSERT OR IGNORE INTO note_group_aliases(alias,group_name) VALUES(?,?)",(target.lower(),target)); renamed=True
        connection.execute("UPDATE note_groups SET archived=? WHERE name=?",(archived,target)); connection.commit()
    except sqlite3.IntegrityError:connection.rollback(); return jsonify(error="Group name or alias conflicts with an existing group"),409
    if renamed:log_activity("info","group",f"Renamed group {row['display_name']} to {target}",target)
    if archived!=row["archived"]:log_activity("info","group",f"{'Archived' if archived else 'Reopened'} group {target}",target)
    return jsonify(ok=True,name=target,archived=bool(archived))

@app.get("/api/groups/<name>/aliases")
@api_auth
def group_aliases(name):
    name=normalized_group_name(name); row=db().execute("SELECT display_name FROM note_groups WHERE name=?",(name,)).fetchone() if name else None
    if not row:return jsonify(error="Group not found"),404
    return jsonify(group=row["display_name"],aliases=[item["alias"] for item in db().execute("SELECT alias FROM note_group_aliases WHERE group_name=? ORDER BY alias",(row["display_name"],))])

@app.post("/api/groups/<name>/aliases")
@api_auth
def add_group_alias(name):
    name=normalized_group_name(name); row=db().execute("SELECT display_name FROM note_groups WHERE name=?",(name,)).fetchone() if name else None
    if not row:return jsonify(error="Group not found"),404
    alias=normalized_group_alias((request.get_json(force=True) or {}).get("alias",""))
    if not alias:return jsonify(error="Aliases must be 1-96 letters, numbers, spaces, hyphens or underscores"),400
    owner=db().execute("SELECT group_name FROM note_group_aliases WHERE alias=?",(alias,)).fetchone()
    if owner and owner["group_name"].lower()!=row["display_name"].lower():return jsonify(error=f"Alias already belongs to {owner['group_name']}"),409
    created=not owner
    if created:db().execute("INSERT INTO note_group_aliases(alias,group_name) VALUES(?,?)",(alias,row["display_name"])); db().commit(); log_activity("info","group",f"Added alias '{alias}' to {row['display_name']}",row["display_name"])
    return jsonify(ok=True,alias=alias,created=created),(201 if created else 200)

@app.delete("/api/groups/<name>/aliases")
@api_auth
def delete_group_alias(name):
    name=normalized_group_name(name); row=db().execute("SELECT display_name FROM note_groups WHERE name=?",(name,)).fetchone() if name else None
    if not row:return jsonify(error="Group not found"),404
    alias=normalized_group_alias((request.get_json(force=True) or {}).get("alias",""))
    if not alias:return jsonify(error="Invalid alias"),400
    if alias==row["display_name"].lower():return jsonify(error="The canonical group name cannot be removed as an alias"),409
    cursor=db().execute("DELETE FROM note_group_aliases WHERE alias=? AND group_name=?",(alias,row["display_name"])); db().commit()
    if not cursor.rowcount:return jsonify(error="Alias not found"),404
    log_activity("info","group",f"Removed alias '{alias}' from {row['display_name']}",row["display_name"]); return jsonify(ok=True)

@app.delete("/api/groups/<name>")
@api_auth
def delete_group(name):
    name=normalized_group_name(name); row=db().execute("SELECT display_name FROM note_groups WHERE name=?",(name,)).fetchone() if name else None
    if not row:return jsonify(error="Group not found"),404
    count=db().execute("SELECT count(*) FROM entries WHERE group_name=?",(row["display_name"],)).fetchone()[0]
    if count and request.args.get("ungroup")!="true":return jsonify(error="Group contains entries",entries=count),409
    db().execute("UPDATE entries SET group_name=NULL WHERE group_name=?",(row["display_name"],)); db().execute("DELETE FROM note_group_aliases WHERE group_name=?",(row["display_name"],)); db().execute("DELETE FROM group_suggestion_dismissals WHERE group_name=?",(row["display_name"],)); db().execute("DELETE FROM note_groups WHERE name=?",(name,)); db().commit()
    log_activity("info","group",f"Removed group {row['display_name']}; preserved {count} entries",row["display_name"]); return jsonify(ok=True,ungrouped=count)

def find_group(name):
    canonical=normalized_group_name(name)
    return db().execute("SELECT display_name AS name,created_at,archived FROM note_groups WHERE name=?",(canonical,)).fetchone() if canonical else None

def group_entries(name):
    return [dict(row) for row in db().execute("SELECT * FROM entries WHERE group_name=? ORDER BY coalesce(recorded_at,created_at),created_at,id",(name,))]

@app.get("/api/groups/<name>/timeline")
@api_auth
def group_timeline(name):
    group=find_group(name)
    if not group:return jsonify(error="Group not found"),404
    return jsonify(group=dict(group),items=group_entries(group["name"]))

def suggestion_for_entry(entry):
    suggestion=suggested_group_for(entry["transcription"])
    if not suggestion:return None
    dismissed=db().execute("SELECT 1 FROM group_suggestion_dismissals WHERE entry_id=? AND group_name=?",(entry["id"],suggestion["group"])).fetchone()
    return None if dismissed else {"entryId":entry["id"],"transcription":entry["transcription"],"createdAt":entry["created_at"],**suggestion}

@app.get("/api/group-suggestions")
@api_auth
def group_suggestions():
    entries=db().execute("SELECT id,transcription,created_at FROM entries WHERE group_name IS NULL AND archived=0 ORDER BY created_at DESC LIMIT 200").fetchall()
    return jsonify([suggestion for entry in entries if (suggestion:=suggestion_for_entry(entry))][:50])

@app.post("/api/group-suggestions/<entry_id>/accept")
@api_auth
def accept_group_suggestion(entry_id):
    entry=db().execute("SELECT id,transcription,created_at FROM entries WHERE id=? AND group_name IS NULL",(entry_id,)).fetchone()
    suggestion=suggestion_for_entry(entry) if entry else None; requested=normalized_group_name((request.get_json(silent=True) or {}).get("group",""))
    if not suggestion:return jsonify(error="Suggestion not found"),404
    if requested!=suggestion["group"]:return jsonify(error="Suggestion no longer matches"),409
    db().execute("UPDATE entries SET group_name=?,transcription=? WHERE id=?",(suggestion["group"],suggestion["suggestedText"],entry_id)); db().commit()
    log_activity("info","group",f"Accepted suggestion for {suggestion['group']}",entry_id); return jsonify(ok=True,group=suggestion["group"])

@app.post("/api/group-suggestions/<entry_id>/dismiss")
@api_auth
def dismiss_group_suggestion(entry_id):
    entry=db().execute("SELECT id,transcription,created_at FROM entries WHERE id=? AND group_name IS NULL",(entry_id,)).fetchone()
    suggestion=suggestion_for_entry(entry) if entry else None; requested=normalized_group_name((request.get_json(silent=True) or {}).get("group",""))
    if not suggestion:return jsonify(error="Suggestion not found"),404
    if requested!=suggestion["group"]:return jsonify(error="Suggestion no longer matches"),409
    db().execute("INSERT OR REPLACE INTO group_suggestion_dismissals(entry_id,group_name,dismissed_at) VALUES(?,?,?)",(entry_id,suggestion["group"],now())); db().commit()
    log_activity("info","group",f"Dismissed suggestion for {suggestion['group']}",entry_id); return jsonify(ok=True)

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
    body=request.get_json(force=True); allowed={"starred","processed","archived","tags","transcription","title","category","group_name"}; updates={k:body[k] for k in body if k in allowed}
    if "category" in updates and updates["category"] not in VALID_CATEGORIES:return jsonify(error="Invalid category"),400
    if "group_name" in updates:
        requested=normalized_group_name(updates["group_name"]) if updates["group_name"] else None
        if requested:
            group=db().execute("SELECT display_name FROM note_groups WHERE name=? AND archived=0",(requested,)).fetchone()
            if not group:return jsonify(error="Group not found or archived"),400
            updates["group_name"]=group["display_name"]
        else:updates["group_name"]=None
    if not updates:return jsonify(error="No supported fields supplied"),400
    previous=db().execute("SELECT group_name FROM entries WHERE id=?",(entry_id,)).fetchone(); values=[int(v) if k in {"starred","processed","archived"} else (None if v is None else str(v)) for k,v in updates.items()]
    cur=db().execute(f"UPDATE entries SET {', '.join(k+'=?' for k in updates)} WHERE id=?",(*values,entry_id)); db().commit()
    if cur.rowcount and "group_name" in updates and previous["group_name"]!=updates["group_name"]:log_activity("info","group",f"Moved entry from {previous['group_name'] or 'standalone'} to {updates['group_name'] or 'standalone'}",entry_id)
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
    db().execute("DELETE FROM group_suggestion_dismissals WHERE entry_id=?",(entry_id,)); db().execute("DELETE FROM entries WHERE id=?",(entry_id,)); db().commit()
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
    try:
        payload=payload_from_request(); upload=next((request.files[k] for k in request.files if request.files[k].filename),None); result=store_entry(payload,upload,"manual"); return jsonify(ok=True,**result),(201 if result["created"] else 200)
    except Exception as error:
        log_activity("error","ingest_error","A manual capture could not be stored",str(error)); return jsonify(error="Manual capture failed"),500

@app.get("/api/activity")
@api_auth
def activity(): return jsonify([dict(r) for r in db().execute("SELECT * FROM activity ORDER BY id DESC LIMIT 100")])

@app.get("/api/changes")
@api_auth
def changes():
    latest=db().execute("SELECT coalesce(max(id),0) FROM activity").fetchone()[0]
    if "since" not in request.args:return jsonify(sequence=latest,events=[])
    try:since=max(int(request.args["since"]),0)
    except ValueError:return jsonify(error="since must be a non-negative integer"),400
    placeholders=",".join("?" for _ in CAPTURE_EVENT_KINDS)
    rows=db().execute(f"SELECT id,created_at,level,kind,message FROM activity WHERE id>? AND kind IN ({placeholders}) ORDER BY id LIMIT 50",(since,*sorted(CAPTURE_EVENT_KINDS))).fetchall()
    sequence=rows[-1]["id"] if len(rows)==50 else latest
    return jsonify(sequence=sequence,events=[dict(row) for row in rows])

@app.errorhandler(413)
def capture_too_large(_error):
    if request.path in {"/webhook/index","/api/manual"}:log_activity("error","ingest_error","A capture exceeded the upload size limit")
    return jsonify(error="Capture exceeds the configured upload size limit"),413

@app.get("/api/status")
@api_auth
def status():
    count=db().execute("SELECT count(*) FROM entries").fetchone()[0]; audio_count=db().execute("SELECT count(*) FROM entries WHERE audio_path IS NOT NULL").fetchone()[0]
    audio_bytes=sum(p.stat().st_size for p in AUDIO_DIR.iterdir() if p.is_file()); db_bytes=DB_PATH.stat().st_size if DB_PATH.exists() else 0
    return jsonify(entries=count,audioEntries=audio_count,audioBytes=audio_bytes,databaseBytes=db_bytes,lastBackupHook=BACKUP_HOOK_URL!="",trustedProxyHops=TRUSTED_PROXY_HOPS)

def export_rows(): return [dict(r) for r in db().execute("SELECT * FROM entries ORDER BY created_at DESC")]

def markdown_export(rows, title=None):
    heading=f"# {title}\n\n" if title else ""
    return heading+"\n\n".join(f"## {r['recorded_at'] or r['created_at']}\n\n{r['transcription']}\n\nCategory: {r['category']}\n\nTags: {r['tags']}" for r in rows)

def export_response(fmt, rows, basename, title=None):
    if fmt=="json":return Response(json.dumps(rows,indent=2,ensure_ascii=False),headers={"Content-Disposition":f"attachment; filename={basename}.json"},mimetype="application/json")
    if fmt=="markdown":return Response(markdown_export(rows,title),headers={"Content-Disposition":f"attachment; filename={basename}.md"},mimetype="text/markdown")
    if fmt=="zip":
        out=io.BytesIO()
        with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("entries.json",json.dumps(rows,indent=2,ensure_ascii=False)); archive.writestr("notes.md",markdown_export(rows,title))
            for row in rows:
                if row["audio_path"] and (AUDIO_DIR/row["audio_path"]).exists():archive.write(AUDIO_DIR/row["audio_path"],f"audio/{row['audio_path']}")
        out.seek(0);return send_file(out,mimetype="application/zip",as_attachment=True,download_name=f"{basename}.zip")
    return jsonify(error="Use json, markdown, or zip"),400

@app.get("/api/export/<fmt>")
@api_auth
def export(fmt):
    return export_response(fmt,export_rows(),"index-inbox")

@app.get("/api/groups/<name>/export/<fmt>")
@api_auth
def export_group(name,fmt):
    group=find_group(name)
    if not group:return jsonify(error="Group not found"),404
    canonical=group["name"]
    return export_response(fmt,group_entries(canonical),f"index-inbox-{canonical.lower()}",canonical)

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

@auth_cli.command("list-attempts")
def list_login_attempts():
    rows=db().execute("SELECT attempted_at,username,source_ip,peer_ip,successful FROM login_attempts ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:click.echo("No login attempts")
    for row in rows:click.echo(f"{row['attempted_at']}\t{row['username']}\tclient={row['source_ip']}\tpeer={row['peer_ip']}\t{'success' if row['successful'] else 'failure'}")

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

@app.cli.group("groups")
def groups_cli(): """Manage voice note groups."""

@groups_cli.command("list")
def list_note_groups():
    rows=db().execute("""SELECT g.display_name,count(e.id) AS entries FROM note_groups g LEFT JOIN entries e
      ON e.group_name=g.display_name GROUP BY g.name ORDER BY g.display_name""").fetchall()
    if not rows:click.echo("No note groups")
    for row in rows:click.echo(f"{row['display_name']}\t{row['entries']} entries")

@groups_cli.command("delete-empty")
@click.option("--name",prompt=True)
def delete_empty_note_group(name):
    name=normalized_group_name(name)
    if not name:raise click.ClickException("Invalid group name")
    row=db().execute("SELECT display_name FROM note_groups WHERE name=?",(name,)).fetchone()
    if not row:raise click.ClickException("Note group not found")
    if db().execute("SELECT count(*) FROM entries WHERE group_name=?",(row["display_name"],)).fetchone()[0]:raise click.ClickException("Group is not empty")
    db().execute("DELETE FROM note_group_aliases WHERE group_name=?",(row["display_name"],)); db().execute("DELETE FROM note_groups WHERE name=?",(name,)); db().commit(); click.echo(f"Deleted empty group {row['display_name']}")
@app.get("/")
def index():return send_from_directory("static","index.html")
@app.get("/<path:path>")
def static_files(path):return send_from_directory("static",path)
