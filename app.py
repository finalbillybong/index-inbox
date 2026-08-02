import csv, hashlib, io, ipaddress, json, os, re, secrets, sqlite3, tempfile, threading, time, urllib.request, uuid, zipfile
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reminders import parse_reminder

import firebase_admin
from firebase_admin import auth
import click
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")); AUDIO_DIR = DATA_DIR / "audio"; BACKUP_DIR=DATA_DIR / "backups"; DB_PATH = DATA_DIR / "index-inbox.sqlite3"
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
AUTH_DEVICE_DAYS = max(int(os.getenv("AUTH_DEVICE_DAYS", "90")), 1)
TRUSTED_PROXY_HOPS = max(int(os.getenv("TRUSTED_PROXY_HOPS", "0")), 0)
try:TRUSTED_PROXY_NETWORKS=tuple(ipaddress.ip_network(value.strip(),strict=False) for value in os.getenv("TRUSTED_PROXY_CIDRS","").split(",") if value.strip())
except ValueError as error:raise RuntimeError(f"Invalid TRUSTED_PROXY_CIDRS: {error}") from error
if TRUSTED_PROXY_HOPS and not TRUSTED_PROXY_NETWORKS:raise RuntimeError("TRUSTED_PROXY_CIDRS is required when TRUSTED_PROXY_HOPS is greater than zero")
AUTH_COOKIE = "__Host-index_session" if AUTH_COOKIE_SECURE else "index_session"
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("index-inbox-dummy-password")
MAX_AUDIO_BYTES = int(os.getenv("MAX_AUDIO_MB", "25")) * 1024 * 1024
BACKUP_HOOK_URL = os.getenv("BACKUP_HOOK_URL", "")
ANDROID_UPDATE_VERSION_CODE = max(int(os.getenv("ANDROID_UPDATE_VERSION_CODE", "0")), 0)
ANDROID_UPDATE_VERSION_NAME = os.getenv("ANDROID_UPDATE_VERSION_NAME", "").strip()
ANDROID_UPDATE_APK = Path(os.getenv("ANDROID_UPDATE_APK_PATH", str(DATA_DIR / "releases" / "index-inbox.apk")))
TRANSCRIPTION_ENABLED = os.getenv("TRANSCRIPTION_ENABLED", "true").lower() == "true"
TRANSCRIPTION_MODEL = os.getenv("TRANSCRIPTION_MODEL", "tiny.en").strip()
TRANSCRIPTION_LANGUAGE = os.getenv("TRANSCRIPTION_LANGUAGE", "en").strip() or None
TRANSCRIPTION_THREADS = max(int(os.getenv("TRANSCRIPTION_THREADS", "4")), 1)
TRANSCRIPTION_MODEL_DIR = DATA_DIR / "models"
INTERPRETATION_MODEL_URL = os.getenv("INTERPRETATION_MODEL_URL", "").strip().rstrip("/")
INTERPRETATION_MODEL_NAME = os.getenv("INTERPRETATION_MODEL_NAME", "").strip()
INTERPRETATION_MODEL_TIMEOUT = max(1,min(int(os.getenv("INTERPRETATION_MODEL_TIMEOUT", "8")),30))
REMINDER_TIMEZONE = os.getenv("REMINDER_TIMEZONE", "UTC").strip() or "UTC"
REMINDER_CLOCK_FORMAT = os.getenv("REMINDER_CLOCK_FORMAT", "24").strip()
if REMINDER_CLOCK_FORMAT not in {"12","24"}:raise RuntimeError("REMINDER_CLOCK_FORMAT must be 12 or 24")
try:REMINDER_ZONE=ZoneInfo(REMINDER_TIMEZONE)
except ZoneInfoNotFoundError as error:raise RuntimeError(f"Unknown REMINDER_TIMEZONE: {REMINDER_TIMEZONE}") from error
_TRANSCRIPTION_MODEL = None
_TRANSCRIPTION_LOCK = threading.Lock()
_INTERPRETATION_MODEL_STATUS={"state":"not_checked","message":"The model has not been called.","checkedAt":None}
_INTERPRETATION_MODEL_LOCK=threading.Lock()
VALID_CATEGORIES = {"note", "task", "idea", "question"}
CAPTURE_EVENT_KINDS = {"capture_standalone", "capture_grouped", "group_created", "group_exists",
  "group_unrecognized", "webhook_rejected", "ingest_error", "item_completed", "item_reopened",
  "collection_changed", "interpreted_operation", "interpreted_operation_undone"}
DATA_DIR.mkdir(parents=True, exist_ok=True); AUDIO_DIR.mkdir(parents=True, exist_ok=True); BACKUP_DIR.mkdir(parents=True,exist_ok=True); TRANSCRIPTION_MODEL_DIR.mkdir(parents=True,exist_ok=True)
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

def init_db(path=DB_PATH):
    con = sqlite3.connect(path)
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
      CREATE TABLE IF NOT EXISTS local_device_tokens (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
        session_version INTEGER NOT NULL, device_name TEXT NOT NULL, created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
        expires_at TEXT NOT NULL, FOREIGN KEY(user_id) REFERENCES local_users(id) ON DELETE CASCADE);
      CREATE INDEX IF NOT EXISTS idx_local_device_tokens_user ON local_device_tokens(user_id,created_at);
      CREATE TABLE IF NOT EXISTS login_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, attempted_at TEXT NOT NULL,
        username TEXT NOT NULL, source_ip TEXT NOT NULL, peer_ip TEXT NOT NULL DEFAULT '', successful INTEGER NOT NULL DEFAULT 0);
      CREATE INDEX IF NOT EXISTS idx_login_attempts_lookup ON login_attempts(username,source_ip,attempted_at);
      CREATE TABLE IF NOT EXISTS note_groups (name TEXT PRIMARY KEY COLLATE NOCASE, display_name TEXT NOT NULL,
        created_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS note_group_aliases (alias TEXT PRIMARY KEY COLLATE NOCASE, group_name TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS group_suggestion_dismissals (entry_id TEXT NOT NULL, group_name TEXT NOT NULL,
        dismissed_at TEXT NOT NULL, PRIMARY KEY(entry_id,group_name));
      CREATE TABLE IF NOT EXISTS backup_runs (id TEXT PRIMARY KEY,requested_at TEXT NOT NULL,completed_at TEXT,
        status TEXT NOT NULL,archive_name TEXT,archive_bytes INTEGER,error TEXT NOT NULL DEFAULT '');
      CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS interpreted_operations (id TEXT PRIMARY KEY,created_at TEXT NOT NULL,source TEXT NOT NULL,
        source_key TEXT UNIQUE,operation TEXT NOT NULL,confidence REAL NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL,
        target_id TEXT,result_json TEXT NOT NULL,proposed_json TEXT NOT NULL DEFAULT '{}',undo_kind TEXT,undo_payload TEXT,reversed_at TEXT);
    """)
    con.execute("BEGIN IMMEDIATE")
    columns = {r[1] for r in con.execute("PRAGMA table_info(entries)")}
    additions = {"title":"TEXT NOT NULL DEFAULT ''", "category":"TEXT NOT NULL DEFAULT 'note'",
      "archived":"INTEGER NOT NULL DEFAULT 0", "source_key":"TEXT", "group_name":"TEXT",
      "due_at":"TEXT","reminder_completed":"INTEGER NOT NULL DEFAULT 0",
      "reminder_notify_before_minutes":"INTEGER", "completed":"INTEGER NOT NULL DEFAULT 0"}
    for name, definition in additions.items():
        if name not in columns: con.execute(f"ALTER TABLE entries ADD COLUMN {name} {definition}")
    operation_columns={r[1] for r in con.execute("PRAGMA table_info(interpreted_operations)")}
    if "proposed_json" not in operation_columns:con.execute("ALTER TABLE interpreted_operations ADD COLUMN proposed_json TEXT NOT NULL DEFAULT '{}'")
    login_columns={r[1] for r in con.execute("PRAGMA table_info(login_attempts)")}
    if "peer_ip" not in login_columns:con.execute("ALTER TABLE login_attempts ADD COLUMN peer_ip TEXT NOT NULL DEFAULT ''")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_source_key ON entries(source_key) WHERE source_key IS NOT NULL")
    con.execute("INSERT OR IGNORE INTO note_group_aliases(alias,group_name) SELECT lower(display_name),display_name FROM note_groups")
    con.execute("UPDATE entries SET category='note' WHERE category='action'")
    con.execute("PRAGMA user_version=2")
    con.commit(); con.close()
init_db()

def now(): return datetime.now(timezone.utc).isoformat()
def log_activity(level, kind, message, details=""):
    db().execute("INSERT INTO activity(created_at,level,kind,message,details) VALUES(?,?,?,?,?)", (now(),level,kind,message,details)); db().commit()

def setting_bool(key,default=False):
    row=db().execute("SELECT value FROM app_settings WHERE key=?",(key,)).fetchone()
    return (row["value"].lower()=="true") if row else bool(default)

def set_setting_bool(key,value):
    db().execute("INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(key,"true" if value else "false",now()));db().commit()

AUTO_EXECUTION_THRESHOLD=0.95
AUTO_EXECUTION_OPERATIONS={"create_collection","add_to_collection","set_reminder"}

def automatic_execution_policy(interpretation):
    operation=interpretation["operation"]
    if interpretation["ambiguous"]:return False,"Ambiguous proposals are never executed automatically."
    if interpretation["requiresConfirmation"]:return False,"This operation always requires confirmation."
    if operation not in AUTO_EXECUTION_OPERATIONS:return False,"This operation is not on the non-destructive automatic allowlist."
    if interpretation["confidence"]<AUTO_EXECUTION_THRESHOLD:return False,f"Confidence is below the {AUTO_EXECUTION_THRESHOLD:.2f} automatic threshold."
    return True,f"Deterministic {operation} matched at {interpretation['confidence']:.2f}, meeting the {AUTO_EXECUTION_THRESHOLD:.2f} non-destructive threshold."

def interpretation_with_policy(interpretation):
    allowed,reason=automatic_execution_policy(interpretation)
    return {**interpretation,"autoExecutable":allowed,"autoExecutionReason":reason,"autoExecutionEnabled":setting_bool("automatic_execution",False)}

def operation_receipt(source,source_key,interpretation,reason,result,undo_kind=None,undo_payload=None,status="executed"):
    receipt_id=str(uuid.uuid4());target_id=result.get("id");details={"receiptId":receipt_id,"source":source,"targetId":target_id,"operation":interpretation["operation"],"outcome":status,"reversible":bool(undo_kind),"confirmable":status=="awaiting_confirmation","reason":reason,"confidence":interpretation["confidence"]}
    db().execute("""INSERT INTO interpreted_operations(id,created_at,source,source_key,operation,confidence,reason,status,target_id,result_json,proposed_json,undo_kind,undo_payload)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(receipt_id,now(),source,source_key,interpretation["operation"],interpretation["confidence"],reason,status,result.get("id") or result.get("group"),json.dumps(result),json.dumps(interpretation),undo_kind,json.dumps(undo_payload or {})));db().commit()
    label=interpretation["operation"].replace("_"," ")
    message=(f"Ring command needs confirmation — {reason}" if source=="ring" and status=="awaiting_confirmation"
      else f"Ring command needs review — {reason}" if source=="ring" and status=="saved_plain_safely"
      else f"Ring {label}: {status}" if source=="ring" else f"{label.capitalize()}: {status}")
    log_activity("warning" if status in {"saved_plain_safely","awaiting_confirmation"} else "info","interpreted_operation",message,json.dumps(details))
    return {**result,"operationReceiptId":receipt_id,"operationOutcome":status,"operationReversible":bool(undo_kind)}

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

def current_webhook_secret():
    row=db().execute("SELECT value FROM app_settings WHERE key='webhook_secret'").fetchone()
    return row["value"] if row else WEBHOOK_SECRET

def set_webhook_secret(value):
    db().execute("""INSERT INTO app_settings(key,value,updated_at) VALUES('webhook_secret',?,?)
      ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",(value,now()))
    db().commit()

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

def local_device_user():
    header=request.headers.get("Authorization","")
    if not header.startswith("Bearer "):return None
    token=header[7:].strip()
    if not token:return None
    token_hash=session_token_hash(token)
    row=db().execute("""SELECT t.*,u.username,u.enabled,u.session_version AS current_version FROM local_device_tokens t
      JOIN local_users u ON u.id=t.user_id WHERE t.token_hash=?""",(token_hash,)).fetchone()
    current=now()
    if not row or not row["enabled"] or row["session_version"]!=row["current_version"] or row["expires_at"]<=current:
        if row:db().execute("DELETE FROM local_device_tokens WHERE token_hash=?",(token_hash,)); db().commit()
        return None
    db().execute("UPDATE local_device_tokens SET last_seen_at=? WHERE token_hash=?",(current,token_hash)); db().commit()
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
            device=local_device_user()
            session=None if device else local_user()
            if not device and not session:return jsonify(error="Authentication required"),401
            if session and request.method in {"POST","PUT","PATCH","DELETE"}:
                supplied=request.headers.get("X-CSRF-Token","")
                if not request_origin_allowed() or not supplied or not secrets.compare_digest(supplied,session["csrf_token"]):return jsonify(error="Invalid CSRF token"),403
            identity=device or session
            g.user={"id":str(identity["user_id"]),"username":identity["username"],"provider":"local"}
            if device:g.local_device=device
            else:g.local_session=session
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

def authenticate_local_credentials(username,password,source,peer):
    if len(password)>1024:return None,jsonify(error="Invalid username or password"),401
    if login_limited(username,source):return None,jsonify(error="Too many login attempts; try again later"),429
    user=db().execute("SELECT * FROM local_users WHERE username=?",(username,)).fetchone(); password_matches=False
    try:password_matches=bool(user and PASSWORD_HASHER.verify(user["password_hash"],password))
    except (VerifyMismatchError,InvalidHashError):pass
    if not user:
        try:PASSWORD_HASHER.verify(DUMMY_PASSWORD_HASH,password)
        except VerifyMismatchError:pass
    valid=bool(user and user["enabled"] and password_matches)
    record_login_attempt(username,source,valid,peer)
    if not valid:return None,jsonify(error="Invalid username or password"),401
    return user,None,None

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
    user,error,status=authenticate_local_credentials(username,password,source,peer)
    if error:return error,status
    return create_local_session(user)

@app.post("/auth/device/login")
def local_device_login():
    if AUTH_PROVIDER!="local":return jsonify(error="Local authentication is not enabled"),404
    body=request.get_json(silent=True) or {}; username=str(body.get("username","")).strip().lower()[:256]; password=str(body.get("password","")); device_name=str(body.get("deviceName","")).strip()[:100]
    if not device_name:return jsonify(error="Device name is required"),400
    source,peer=request_client_addresses()
    user,error,status=authenticate_local_credentials(username,password,source,peer)
    if error:return error,status
    token=secrets.token_urlsafe(32); created=now(); expires=(datetime.now(timezone.utc)+timedelta(days=AUTH_DEVICE_DAYS)).isoformat()
    connection=db(); connection.execute("DELETE FROM local_device_tokens WHERE expires_at<=?",(created,))
    connection.execute("""INSERT INTO local_device_tokens
      (token_hash,user_id,session_version,device_name,created_at,last_seen_at,expires_at) VALUES(?,?,?,?,?,?,?)""",
      (session_token_hash(token),user["id"],user["session_version"],device_name,created,created,expires))
    stale=connection.execute("SELECT token_hash FROM local_device_tokens WHERE user_id=? ORDER BY created_at DESC LIMIT -1 OFFSET 10",(user["id"],)).fetchall()
    if stale:connection.executemany("DELETE FROM local_device_tokens WHERE token_hash=?",((row["token_hash"],) for row in stale))
    connection.commit()
    return jsonify(authenticated=True,username=user["username"],token=token,deviceName=device_name,expiresAt=expires),201

@app.get("/auth/device/session")
def local_device_session():
    if AUTH_PROVIDER!="local":return jsonify(authenticated=False,provider=AUTH_PROVIDER),401
    device=local_device_user()
    if not device:return jsonify(authenticated=False,provider="local"),401
    return jsonify(authenticated=True,provider="local",username=device["username"],deviceName=device["device_name"],expiresAt=device["expires_at"])

@app.post("/auth/device/logout")
def local_device_logout():
    if AUTH_PROVIDER!="local":return jsonify(error="Local authentication is not enabled"),404
    device=local_device_user()
    if not device:return jsonify(error="Authentication required"),401
    db().execute("DELETE FROM local_device_tokens WHERE token_hash=?",(device["token_hash"],)); db().commit()
    return jsonify(ok=True)

@app.get("/auth/devices")
@api_auth
def local_devices():
    if AUTH_PROVIDER!="local":return jsonify(error="Local authentication is not enabled"),404
    current=getattr(g,"local_device",None)
    if not current:return jsonify(error="Native device authentication required"),403
    rows=db().execute("""SELECT device_name,created_at,last_seen_at,expires_at,token_hash
      FROM local_device_tokens WHERE user_id=? ORDER BY last_seen_at DESC""",(current["user_id"],)).fetchall()
    return jsonify([{"deviceName":row["device_name"],"createdAt":row["created_at"],"lastSeenAt":row["last_seen_at"],
      "expiresAt":row["expires_at"],"current":secrets.compare_digest(row["token_hash"],current["token_hash"])} for row in rows])

@app.post("/auth/devices/revoke-others")
@api_auth
def revoke_other_devices():
    if AUTH_PROVIDER!="local":return jsonify(error="Local authentication is not enabled"),404
    current=getattr(g,"local_device",None)
    if not current:return jsonify(error="Native device authentication required"),403
    cursor=db().execute("DELETE FROM local_device_tokens WHERE user_id=? AND token_hash<>?",(current["user_id"],current["token_hash"])); db().commit()
    return jsonify(ok=True,revoked=cursor.rowcount)

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
    if not supplied and bearer.startswith("Bearer "): supplied = bearer[7:]
    supplied = request.args.get("token", supplied)
    configured=current_webhook_secret()
    return bool(configured) and secrets.compare_digest(supplied, configured)

def confirm_integration_password(body):
    if AUTH_PROVIDER!="local":return None
    password=str(body.get("password",""))
    source,peer=request_client_addresses()
    user,error,status=authenticate_local_credentials(g.user["username"],password,source,peer)
    if error:return error,status
    if str(user["id"])!=g.user["id"]:return jsonify(error="Password confirmation failed"),403
    return None

@app.get("/api/integrations/index-ring")
@api_auth
def index_ring_integration():
    configured=current_webhook_secret()
    return jsonify(webhookPath="/webhook/index",webhookUrl=request.url_root.rstrip("/")+"/webhook/index",configured=bool(configured),
      maskedSecret=("••••"+configured[-4:]) if configured else "",
      requiresPassword=AUTH_PROVIDER=="local")

@app.post("/api/integrations/index-ring/reveal")
@api_auth
def reveal_index_ring_secret():
    body=request.get_json(silent=True) or {}
    confirmation=confirm_integration_password(body)
    if confirmation:return confirmation
    configured=current_webhook_secret()
    if not configured:return jsonify(error="Webhook secret is not configured"),503
    response=jsonify(secret=configured);response.headers["Cache-Control"]="no-store";return response

@app.post("/api/integrations/index-ring/rotate")
@api_auth
def rotate_index_ring_secret():
    body=request.get_json(silent=True) or {}
    confirmation=confirm_integration_password(body)
    if confirmation:return confirmation
    value=secrets.token_urlsafe(32)
    set_webhook_secret(value)
    log_activity("info","webhook_secret_rotated","Index Ring webhook secret rotated")
    response=jsonify(secret=value);response.headers["Cache-Control"]="no-store";return response

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

INTERPRETATION_VERSION="1.0"

def interpretation_result(operation,arguments,confidence,explanation,ambiguous=False,requires_confirmation=False):
    return {"version":INTERPRETATION_VERSION,"operation":operation,"arguments":arguments,"confidence":round(float(confidence),2),
      "explanation":explanation,"ambiguous":bool(ambiguous),"requiresConfirmation":bool(requires_confirmation)}

def completion_candidates(query):
    query=str(query).strip().rstrip(".!?")
    if not query:return []
    exact=db().execute("""SELECT id,title,transcription FROM entries WHERE archived=0 AND completed=0
      AND (lower(title)=lower(?) OR lower(transcription)=lower(?)) ORDER BY created_at DESC LIMIT 10""",(query,query)).fetchall()
    rows=exact or db().execute("""SELECT id,title,transcription FROM entries WHERE archived=0 AND completed=0
      AND (title LIKE ? OR transcription LIKE ?) ORDER BY created_at DESC LIMIT 10""",(f"%{query}%",f"%{query}%")).fetchall()
    return [{"id":row["id"],"label":row["title"] or row["transcription"][:120]} for row in rows]

def interpret_capture_deterministic(text,reference=None,requested_collection=""):
    text=str(text or "").strip()
    if not text:return interpretation_result("create_item",{"text":""},0.0,"The capture is empty.",True,True)
    command=create_group_command(text)
    if command:
        name,aliases=command
        return interpretation_result("create_collection",{"name":name,"aliases":sorted(aliases)},1.0,f"Create Collection {name}.")
    if re.match(r"^\s*create\b",text,re.IGNORECASE):
        return interpretation_result("create_item",{"text":text},0.35,"The create command does not contain a valid Collection name.",True,True)

    complete_match=(re.fullmatch(r"\s*(?:complete|finish)\s+(.+?)\s*",text,re.IGNORECASE)
      or re.fullmatch(r"\s*mark\s+(.+?)\s+(?:as\s+)?complete\s*",text,re.IGNORECASE)
      or re.fullmatch(r"\s*(.+?)\s+is\s+done\s*",text,re.IGNORECASE))
    if complete_match:
        query=complete_match.group(1).strip(); candidates=completion_candidates(query)
        if len(candidates)==1:return interpretation_result("complete_item",{"itemId":candidates[0]["id"],"query":query,"candidates":candidates},0.98,f"Complete the one open Item matching “{query}”.",False,True)
        explanation=(f"More than one open Item matches “{query}”." if candidates else f"No open Item matches “{query}”.")
        return interpretation_result("complete_item",{"query":query,"candidates":candidates},0.45 if candidates else 0.2,explanation,True,True)

    search_match=re.fullmatch(r"\s*(?:find|search(?:\s+for)?|show\s+me)\s+(.+?)\s*",text,re.IGNORECASE)
    if search_match:
        query=search_match.group(1).strip().rstrip(".!?")
        return interpretation_result("search_items",{"query":query},0.96,f"Search Items for “{query}”.")

    group_name,group_text=match_note_group(text)
    if requested_collection:
        canonical=normalized_group_name(requested_collection)
        collection=db().execute("SELECT display_name FROM note_groups WHERE name=? AND archived=0",(canonical,)).fetchone() if canonical else None
        if not collection:return interpretation_result("add_to_collection",{"text":text,"collectionName":str(requested_collection)},0.0,"The requested Collection does not exist or is archived.",True,True)
        group_name=collection["display_name"];group_text=text
    reminder=parse_reminder(group_text,reference,REMINDER_ZONE,REMINDER_CLOCK_FORMAT)
    if reminder:
        arguments={"text":reminder["text"],"dueAt":reminder["due_at"],"notifyBeforeMinutes":reminder.get("notify_before_minutes")}
        if group_name:arguments["collectionName"]=group_name
        return interpretation_result("set_reminder",arguments,0.99,"Create an Item with the requested reminder time.")
    if group_name:
        return interpretation_result("add_to_collection",{"text":group_text,"collectionName":group_name},0.99,f"Add an Item to Collection {group_name}.")
    return interpretation_result("create_item",{"text":text},1.0,"Create a standalone Item.")

MODEL_OPERATIONS={"create_collection","add_to_collection","set_reminder","search_items"}
MODEL_OUTPUT_OPERATIONS=MODEL_OPERATIONS|{"no_match"}

def interpretation_model_configured():return bool(INTERPRETATION_MODEL_URL and INTERPRETATION_MODEL_NAME)

def set_interpretation_model_status(state,message):
    global _INTERPRETATION_MODEL_STATUS
    _INTERPRETATION_MODEL_STATUS={"state":state,"message":str(message)[:240],"checkedAt":now()}

def validate_model_proposal(value,text,reference):
    if not isinstance(value,dict) or value.get("operation") not in MODEL_OPERATIONS:raise ValueError("The model returned an unsupported operation")
    operation=value["operation"]
    explanation=f"Self-hosted model proposal: {str(value.get('explanation') or operation).strip()[:180]}"
    if operation=="create_collection":
        name=normalized_group_name(value.get("name",""))
        if not name:raise ValueError("The model proposed an invalid Collection name")
        return interpretation_result(operation,{"name":name,"aliases":[name.lower()]},0.7,explanation,False,True)
    if operation=="add_to_collection":
        group=find_group(value.get("collectionName",""));item_text=str(value.get("text") or text).strip()
        if not group or not item_text:raise ValueError("The model did not identify one existing Collection")
        return interpretation_result(operation,{"text":item_text,"collectionName":group["name"]},0.7,explanation,False,True)
    if operation=="set_reminder":
        due=normalize_timestamp(value.get("dueAt"));item_text=str(value.get("text") or text).strip()
        if not due or datetime.fromisoformat(due.replace("Z","+00:00"))<=datetime.now(timezone.utc):raise ValueError("The model proposed an invalid or past reminder time")
        arguments={"text":item_text,"dueAt":due,"notifyBeforeMinutes":None}
        if value.get("collectionName"):
            group=find_group(value["collectionName"])
            if not group:raise ValueError("The model proposed an unknown Collection")
            arguments["collectionName"]=group["name"]
        return interpretation_result(operation,arguments,0.7,explanation,False,True)
    query=str(value.get("query") or "").strip()
    if not query:raise ValueError("The model search proposal had no query")
    return interpretation_result("search_items",{"query":query},0.7,explanation,False,True)

def model_interpret_capture(text,reference):
    collections=[row[0] for row in db().execute("SELECT display_name FROM note_groups WHERE archived=0 ORDER BY display_name LIMIT 100")]
    schema={"type":"object","properties":{"operation":{"type":"string","enum":sorted(MODEL_OUTPUT_OPERATIONS)},"text":{"type":"string"},"collectionName":{"type":"string"},"dueAt":{"type":"string"},"query":{"type":"string"},"name":{"type":"string"},"explanation":{"type":"string"}},"required":["operation","explanation"]}
    prompt=("Classify one Index Inbox capture. Never invent a Collection or Item. Return no_match when uncertain. "
      f"Current UTC time: {(reference or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()}. Existing Collections: {json.dumps(collections)}. Capture: {json.dumps(text)}")
    body=json.dumps({"model":INTERPRETATION_MODEL_NAME,"messages":[{"role":"system","content":"Return only the requested JSON interpretation. You propose; the application validates and confirms."},{"role":"user","content":prompt}],"stream":False,"format":schema,"options":{"temperature":0}}).encode()
    request_value=urllib.request.Request(f"{INTERPRETATION_MODEL_URL}/api/chat",data=body,headers={"Content-Type":"application/json"},method="POST")
    with _INTERPRETATION_MODEL_LOCK:
        try:
            with urllib.request.urlopen(request_value,timeout=INTERPRETATION_MODEL_TIMEOUT) as response:payload=json.loads(response.read(1024*1024))
            content=payload.get("message",{}).get("content","");proposal=validate_model_proposal(json.loads(content),text,reference)
            set_interpretation_model_status("available",f"{INTERPRETATION_MODEL_NAME} returned a valid proposal.");return proposal
        except Exception as error:
            set_interpretation_model_status("unavailable",f"Model fallback failed safely: {type(error).__name__}");return None

def interpret_capture(text,reference=None,requested_collection=""):
    deterministic=interpret_capture_deterministic(text,reference,requested_collection)
    should_try=(setting_bool("interpretation_model_enabled",False) and interpretation_model_configured() and not requested_collection
      and (deterministic["ambiguous"] or deterministic["operation"]=="create_item"))
    if not should_try:return {**deterministic,"interpretationSource":"deterministic"}
    proposed=model_interpret_capture(str(text or ""),reference)
    return {**(proposed or deterministic),"interpretationSource":"model" if proposed else "deterministic_fallback"}

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

def transcribe_audio(path):
    global _TRANSCRIPTION_MODEL
    if not TRANSCRIPTION_ENABLED:raise RuntimeError("Local transcription is disabled")
    with _TRANSCRIPTION_LOCK:
        if _TRANSCRIPTION_MODEL is None:
            from faster_whisper import WhisperModel
            _TRANSCRIPTION_MODEL=WhisperModel(TRANSCRIPTION_MODEL,device="cpu",compute_type="int8",cpu_threads=TRANSCRIPTION_THREADS,download_root=str(TRANSCRIPTION_MODEL_DIR))
        segments,info=_TRANSCRIPTION_MODEL.transcribe(str(path),language=TRANSCRIPTION_LANGUAGE,beam_size=1,vad_filter=True,condition_on_previous_text=False)
        text=" ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
    if not text:raise ValueError("No speech was detected in the recording")
    return {"transcription":text,"language":getattr(info,"language",TRANSCRIPTION_LANGUAGE),"duration":getattr(info,"duration",None)}

def transcribe_upload(upload):
    if not upload or not upload.filename:raise ValueError("An audio file is required")
    if upload.mimetype and not upload.mimetype.startswith("audio/"):raise ValueError("The uploaded file is not recognized as audio")
    suffix=Path(upload.filename).suffix.lower()[:10] or ".webm"
    handle=tempfile.NamedTemporaryFile(prefix="index-transcribe-",suffix=suffix,delete=False); path=Path(handle.name); handle.close()
    try:
        upload.stream.seek(0); upload.save(path); upload.stream.seek(0)
        if not path.stat().st_size:raise ValueError("The audio recording is empty")
        return transcribe_audio(path)
    finally:path.unlink(missing_ok=True)

def existing_source_entry(payload,source):
    external_id=first(payload,("id","recordingId","recording_id","uuid","eventId"),"")
    if not external_id:return None
    source_key=hashlib.sha256(f"{source}:{external_id}".encode()).hexdigest()
    return db().execute("SELECT id FROM entries WHERE source_key=?",(source_key,)).fetchone()

def existing_operation_receipt(payload,source):
    external_id=first(payload,("id","recordingId","recording_id","uuid","eventId"),"")
    if not external_id:return None
    source_key=hashlib.sha256(f"{source}:{external_id}".encode()).hexdigest()
    return db().execute("SELECT id,result_json,status,undo_kind FROM interpreted_operations WHERE source_key=?",(source_key,)).fetchone()

def store_entry(payload, upload=None, source="ring", interpretation_action_override=None):
    entry_id = str(uuid.uuid4()); recorded = normalize_timestamp(first(payload,("recorded_at","recordedAt","timestamp","created_at"),None))
    transcription = first(payload,("transcription","transcript","text","content","note")); trigger = first(payload,("trigger","trigger_type","triggerType","mode","click_type"),source)
    external_id = first(payload,("id","recordingId","recording_id","uuid","eventId"),"")
    if not external_id and source=="ring" and upload and upload.filename:external_id=Path(upload.filename).stem
    basis = external_id or (json.dumps(payload,sort_keys=True,separators=(",",":")) if recorded else "")
    source_key = hashlib.sha256(f"{source}:{basis}".encode()).hexdigest() if basis else None
    if source_key:
        receipt=db().execute("SELECT id,result_json,status,undo_kind FROM interpreted_operations WHERE source_key=?",(source_key,)).fetchone()
        if receipt:
            result=json.loads(receipt["result_json"]);log_activity("info","duplicate","Duplicate interpreted operation ignored",result.get("id") or result.get("group") or "")
            return {**result,"created":False,"duplicate":True,"operationReceiptId":receipt["id"],"operationOutcome":receipt["status"],"operationReversible":bool(receipt["undo_kind"])}
        existing = db().execute("SELECT id FROM entries WHERE source_key=?",(source_key,)).fetchone()
        if existing: log_activity("info","duplicate","Duplicate webhook ignored",existing["id"]); return {"id":existing["id"],"created":False,"duplicate":True}
    title=first(payload,("title",),""); explicit_category=first(payload,("category",),"")
    category,cleaned=voice_category(transcription)
    if explicit_category in VALID_CATEGORIES: category=explicit_category
    elif source=="ring": transcription=cleaned
    reminder_reference=None
    if recorded:
        try:reminder_reference=datetime.fromisoformat(str(recorded).replace("Z","+00:00"))
        except ValueError:pass
    requested_collection=first(payload,("collection_name","collectionName","group_name","groupName"),"")
    interpretation_action=str(interpretation_action_override or first(payload,("interpretationAction","interpretation_action"),"")).strip().lower()
    if interpretation_action not in {"","accept","confirm","plain","auto"}:raise ValueError("Invalid interpretation action")
    interpretation=(interpretation_result("create_item",{"text":transcription},1.0,"Save as a plain Item.")
      if interpretation_action=="plain" else interpret_capture(transcription,reminder_reference,requested_collection))
    proposed_interpretation=interpretation;receipt_reason=interpretation["explanation"];receipt_status="executed"
    if interpretation_action=="auto":
        allowed,policy_reason=automatic_execution_policy(interpretation);enabled=setting_bool("automatic_execution",False);receipt_reason=policy_reason
        if not (enabled and allowed):
            if not enabled:receipt_reason="Automatic execution is disabled; the unattended command was saved as a plain Item."
            receipt_status=("awaiting_confirmation" if enabled and source=="ring" and interpretation["operation"]=="complete_item" and not interpretation["ambiguous"] and interpretation["arguments"].get("itemId") else "saved_plain_safely")
            interpretation=interpretation_result("create_item",{"text":transcription},1.0,"Automatic execution was not allowed; saved as a plain Item.")
    if source=="manual" and interpretation_action and interpretation["requiresConfirmation"] and interpretation_action!="confirm":
        error=ValueError("Confirm the proposed operation or save as a plain Item")
        error.interpretation=interpretation
        raise error
    if source=="manual" and interpretation_action=="confirm" and interpretation["ambiguous"]:
        error=ValueError("The proposed operation is still ambiguous. Edit the capture or save it as a plain Item")
        error.interpretation=interpretation
        raise error
    if source=="manual" and not interpretation_action and interpretation["requiresConfirmation"]:
        interpretation=interpretation_result("create_item",{"text":transcription},1.0,"Save as a plain Item.")
    unrecognized_group_command=interpretation["operation"]=="create_item" and interpretation["ambiguous"] and bool(re.match(r"^\s*create\b",transcription,re.IGNORECASE))
    if interpretation["operation"]=="create_collection":
        group_to_create=interpretation["arguments"]["name"];aliases=interpretation["arguments"]["aliases"]; cursor=db().execute("INSERT OR IGNORE INTO note_groups(name,display_name,created_at) VALUES(?,?,?)",(group_to_create,group_to_create,now()))
        db().executemany("INSERT OR IGNORE INTO note_group_aliases(alias,group_name) VALUES(?,?)",((alias,group_to_create) for alias in aliases)); db().commit()
        created=bool(cursor.rowcount)
        if interpretation_action!="auto":log_activity("info","group_created" if created else "group_exists",f"{'Created Collection' if created else 'Collection already exists:'} {group_to_create}",group_to_create)
        result={"group":group_to_create,"groupCreated":created,"created":created,"duplicate":not created}
        return operation_receipt(source,source_key,proposed_interpretation,receipt_reason,result,"remove_collection" if created else None,{"name":group_to_create}) if interpretation_action else result
    if interpretation["operation"]=="complete_item":
        item_id=interpretation["arguments"].get("itemId")
        if not item_id:raise ValueError("Choose one matching Item before completing it")
        cursor=db().execute("UPDATE entries SET completed=1 WHERE id=? AND archived=0 AND completed=0",(item_id,));db().commit()
        if not cursor.rowcount:raise ValueError("The matching Item is no longer available")
        log_activity("info","item_completed",f"Completed Item matching {interpretation['arguments']['query']}",item_id)
        result={"id":item_id,"created":False,"duplicate":False,"operation":"complete_item"}
        return operation_receipt(source,source_key,proposed_interpretation,receipt_reason,result,"reopen_item",{"id":item_id})
    if requested_collection and interpretation["ambiguous"]:raise ValueError("Collection not found or archived")
    group_name=interpretation["arguments"].get("collectionName")
    if interpretation["operation"] in {"add_to_collection","set_reminder"}:transcription=interpretation["arguments"]["text"]
    due_at=normalize_timestamp(first(payload,("due_at","dueAt"),None))
    notify_before=first(payload,("reminder_notify_before_minutes","reminderNotifyBeforeMinutes"),None)
    if interpretation["operation"]=="set_reminder" and not due_at:
        due_at=interpretation["arguments"]["dueAt"]; notify_before=interpretation["arguments"].get("notifyBeforeMinutes"); category="task"
    try:notify_before=max(1,min(int(notify_before),10080)) if notify_before not in (None,"") else None
    except (TypeError,ValueError):notify_before=None
    audio_path=audio_mime=None
    if upload and upload.filename:
        suffix=Path(upload.filename).suffix.lower()[:10] or ".bin"; audio_path=f"{entry_id}{suffix}"; audio_mime=upload.mimetype or "application/octet-stream"; upload.save(AUDIO_DIR/audio_path)
    db().execute("""INSERT INTO entries(id,created_at,recorded_at,transcription,trigger_type,audio_path,audio_mime,payload_json,source_key,title,category,group_name,due_at,reminder_notify_before_minutes)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(entry_id,now(),recorded,transcription,trigger,audio_path,audio_mime,json.dumps(payload,ensure_ascii=False),source_key,title,category,group_name,due_at,notify_before)); db().commit()
    if unrecognized_group_command and interpretation_action!="auto":
        log_activity("warning","group_unrecognized","Could not create a Collection from that command",entry_id)
    elif group_name and interpretation_action!="auto":
        log_activity("info","capture_grouped",f"Added an Item to {group_name}",entry_id)
    elif interpretation_action!="auto" or (proposed_interpretation["operation"]=="create_item" and not proposed_interpretation["ambiguous"]):
        log_activity("info","capture_standalone","Added a standalone Item",entry_id)
    result={"id":entry_id,"created":True,"duplicate":False,"group":group_name}
    if (interpretation_action=="auto" and (proposed_interpretation["operation"]!="create_item" or proposed_interpretation["ambiguous"])) or (interpretation_action and interpretation_action!="auto" and proposed_interpretation["operation"]!="create_item"):
        return operation_receipt(source,source_key,proposed_interpretation,receipt_reason,result,"remove_item",{"id":entry_id},receipt_status)
    return result

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
        trigger=request.headers.get("X-Index-Trigger","").strip()
        if trigger:payload["indexTrigger"]=trigger
        if upload and upload.filename:payload["recordingId"]=Path(upload.filename).stem
        result=store_entry(payload,upload,"ring","auto"); return jsonify(ok=True,**result),(201 if result["created"] else 200)
    except Exception as error:
        log_activity("error","ingest_error","A webhook could not be stored",str(error)); return jsonify(error="Webhook ingestion failed"),500

@app.get("/api/groups")
@app.get("/api/collections")
@api_auth
def groups():return jsonify([dict(row) for row in db().execute("""SELECT g.display_name AS name,g.created_at,g.archived,count(e.id) AS entries
  FROM note_groups g LEFT JOIN entries e ON e.group_name=g.display_name GROUP BY g.name ORDER BY g.archived,g.display_name""")])

@app.post("/api/collections")
@api_auth
def create_collection():
    name=normalized_group_name((request.get_json(silent=True) or {}).get("name",""))
    if not name:return jsonify(error="Collection names must be 1-32 letters, numbers, hyphens or underscores"),400
    created=now()
    try:
        db().execute("INSERT INTO note_groups(name,display_name,created_at) VALUES(?,?,?)",(name,name,created))
        db().execute("INSERT INTO note_group_aliases(alias,group_name) VALUES(?,?)",(name.lower(),name));db().commit()
    except sqlite3.IntegrityError:return jsonify(error="A Collection with that name already exists"),409
    log_activity("info","collection_changed",f"Created Collection {name}",name)
    return jsonify(name=name,created_at=created,archived=0,entries=0),201

@app.patch("/api/groups/<name>")
@app.patch("/api/collections/<name>")
@api_auth
def update_group(name):
    current=normalized_group_name(name); body=request.get_json(force=True); connection=db()
    row=connection.execute("SELECT * FROM note_groups WHERE name=?",(current,)).fetchone() if current else None
    if not row:return jsonify(error="Collection not found"),404
    target=current; renamed=False
    if "name" in body:
        target=normalized_group_name(body["name"])
        if not target:return jsonify(error="Collection names must be 1-32 letters, numbers, hyphens or underscores"),400
        if target!=current and connection.execute("SELECT 1 FROM note_groups WHERE name=?",(target,)).fetchone():return jsonify(error="A Collection with that name already exists"),409
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
    except sqlite3.IntegrityError:connection.rollback(); return jsonify(error="Collection name or alias conflicts with an existing Collection"),409
    if renamed:log_activity("info","collection_changed",f"Renamed Collection {row['display_name']} to {target}",target)
    if archived!=row["archived"]:log_activity("info","collection_changed",f"{'Archived' if archived else 'Reopened'} Collection {target}",target)
    return jsonify(ok=True,name=target,archived=bool(archived))

@app.get("/api/groups/<name>/aliases")
@app.get("/api/collections/<name>/aliases")
@api_auth
def group_aliases(name):
    name=normalized_group_name(name); row=db().execute("SELECT display_name FROM note_groups WHERE name=?",(name,)).fetchone() if name else None
    if not row:return jsonify(error="Group not found"),404
    return jsonify(group=row["display_name"],aliases=[item["alias"] for item in db().execute("SELECT alias FROM note_group_aliases WHERE group_name=? ORDER BY alias",(row["display_name"],))])

@app.post("/api/groups/<name>/aliases")
@app.post("/api/collections/<name>/aliases")
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
@app.delete("/api/collections/<name>/aliases")
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
@app.delete("/api/collections/<name>")
@api_auth
def delete_group(name):
    name=normalized_group_name(name); row=db().execute("SELECT display_name FROM note_groups WHERE name=?",(name,)).fetchone() if name else None
    if not row:return jsonify(error="Group not found"),404
    count=db().execute("SELECT count(*) FROM entries WHERE group_name=?",(row["display_name"],)).fetchone()[0]
    if count and request.args.get("ungroup")!="true":return jsonify(error="Group contains entries",entries=count),409
    db().execute("UPDATE entries SET group_name=NULL WHERE group_name=?",(row["display_name"],)); db().execute("DELETE FROM note_group_aliases WHERE group_name=?",(row["display_name"],)); db().execute("DELETE FROM group_suggestion_dismissals WHERE group_name=?",(row["display_name"],)); db().execute("DELETE FROM note_groups WHERE name=?",(name,)); db().commit()
    log_activity("info","collection_changed",f"Removed Collection {row['display_name']}; preserved {count} Items",row["display_name"]); return jsonify(ok=True,ungrouped=count)

def find_group(name):
    canonical=normalized_group_name(name)
    return db().execute("SELECT display_name AS name,created_at,archived FROM note_groups WHERE name=?",(canonical,)).fetchone() if canonical else None

def group_entries(name):
    return [dict(row) for row in db().execute("SELECT * FROM entries WHERE group_name=? ORDER BY coalesce(recorded_at,created_at),created_at,id",(name,))]

@app.get("/api/groups/<name>/timeline")
@app.get("/api/collections/<name>/timeline")
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
@app.get("/api/collection-suggestions")
@api_auth
def group_suggestions():
    entries=db().execute("SELECT id,transcription,created_at FROM entries WHERE group_name IS NULL AND archived=0 ORDER BY created_at DESC LIMIT 200").fetchall()
    return jsonify([suggestion for entry in entries if (suggestion:=suggestion_for_entry(entry))][:50])

@app.post("/api/group-suggestions/<entry_id>/accept")
@app.post("/api/collection-suggestions/<entry_id>/accept")
@api_auth
def accept_group_suggestion(entry_id):
    entry=db().execute("SELECT id,transcription,created_at FROM entries WHERE id=? AND group_name IS NULL",(entry_id,)).fetchone()
    suggestion=suggestion_for_entry(entry) if entry else None; requested=normalized_group_name((request.get_json(silent=True) or {}).get("group",""))
    if not suggestion:return jsonify(error="Suggestion not found"),404
    if requested!=suggestion["group"]:return jsonify(error="Suggestion no longer matches"),409
    db().execute("UPDATE entries SET group_name=?,transcription=? WHERE id=?",(suggestion["group"],suggestion["suggestedText"],entry_id)); db().commit()
    log_activity("info","group",f"Accepted suggestion for {suggestion['group']}",entry_id); return jsonify(ok=True,group=suggestion["group"])

@app.post("/api/group-suggestions/<entry_id>/dismiss")
@app.post("/api/collection-suggestions/<entry_id>/dismiss")
@api_auth
def dismiss_group_suggestion(entry_id):
    entry=db().execute("SELECT id,transcription,created_at FROM entries WHERE id=? AND group_name IS NULL",(entry_id,)).fetchone()
    suggestion=suggestion_for_entry(entry) if entry else None; requested=normalized_group_name((request.get_json(silent=True) or {}).get("group",""))
    if not suggestion:return jsonify(error="Suggestion not found"),404
    if requested!=suggestion["group"]:return jsonify(error="Suggestion no longer matches"),409
    db().execute("INSERT OR REPLACE INTO group_suggestion_dismissals(entry_id,group_name,dismissed_at) VALUES(?,?,?)",(entry_id,suggestion["group"],now())); db().commit()
    log_activity("info","group",f"Dismissed suggestion for {suggestion['group']}",entry_id); return jsonify(ok=True)

def entry_dict(row, collection_vocabulary=False):
    item=dict(row)
    if collection_vocabulary:item["collection_name"]=item["group_name"]
    return item

@app.get("/api/entries")
@app.get("/api/items")
@api_auth
def entries():
    where=[]; values=[]
    q=request.args.get("q","").strip()
    if q: where.append("(transcription LIKE ? OR title LIKE ? OR tags LIKE ? OR group_name LIKE ?)"); values += [f"%{q}%"]*4
    collection_name=request.args.get("collection_name")
    if collection_name not in (None,""):where.append("group_name=?");values.append(collection_name)
    for field in ("category","processed","completed","starred","archived","group_name"):
        if request.args.get(field) not in (None,""): where.append(f"{field}=?"); values.append(request.args[field])
    view=request.args.get("view","")
    if view=="reminders":where.append("due_at IS NOT NULL AND reminder_completed=0")
    elif view=="today":
        local_tomorrow=datetime.now(REMINDER_ZONE).date()+timedelta(days=1)
        tomorrow=datetime(local_tomorrow.year,local_tomorrow.month,local_tomorrow.day,tzinfo=REMINDER_ZONE).astimezone(timezone.utc).isoformat()
        where.append("due_at IS NOT NULL AND due_at<? AND reminder_completed=0");values.append(tomorrow)
    page=max(int(request.args.get("page",1)),1); limit=min(max(int(request.args.get("limit",50)),1),200); clause=" WHERE "+" AND ".join(where) if where else ""
    total=db().execute("SELECT count(*) FROM entries"+clause,values).fetchone()[0]
    order="due_at,created_at" if view in {"today","reminders"} else "created_at DESC"
    rows=db().execute("SELECT * FROM entries"+clause+f" ORDER BY {order} LIMIT ? OFFSET ?",(*values,limit,(page-1)*limit)).fetchall()
    collection_vocabulary=request.path=="/api/items"
    return jsonify(items=[entry_dict(r,collection_vocabulary) for r in rows],page=page,limit=limit,total=total,pages=max(1,(total+limit-1)//limit))

@app.patch("/api/entries/<entry_id>")
@app.patch("/api/items/<entry_id>")
@api_auth
def update_entry(entry_id):
    body=request.get_json(force=True)
    if "collection_name" in body and "group_name" not in body:body["group_name"]=body["collection_name"]
    allowed={"starred","processed","completed","archived","tags","transcription","title","category","group_name","due_at","reminder_completed","reminder_notify_before_minutes"}; updates={k:body[k] for k in body if k in allowed}
    if "category" in updates and updates["category"] not in VALID_CATEGORIES:return jsonify(error="Invalid category"),400
    if "group_name" in updates:
        requested=normalized_group_name(updates["group_name"]) if updates["group_name"] else None
        if requested:
            group=db().execute("SELECT display_name FROM note_groups WHERE name=? AND archived=0",(requested,)).fetchone()
            if not group:return jsonify(error="Group not found or archived"),400
            updates["group_name"]=group["display_name"]
        else:updates["group_name"]=None
    if not updates:return jsonify(error="No supported fields supplied"),400
    if "due_at" in updates:
        if updates["due_at"] in (None,""):updates["due_at"]=None
        else:
            try:
                parsed=datetime.fromisoformat(str(updates["due_at"]).replace("Z","+00:00"))
                if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=REMINDER_ZONE)
                updates["due_at"]=parsed.astimezone(timezone.utc).isoformat()
            except ValueError:return jsonify(error="Invalid reminder time"),400
    if "reminder_notify_before_minutes" in updates:
        if updates["reminder_notify_before_minutes"] in (None,""):updates["reminder_notify_before_minutes"]=None
        else:
            try:
                lead=int(updates["reminder_notify_before_minutes"])
                updates["reminder_notify_before_minutes"]=min(lead,10080) if lead>0 else None
            except (TypeError,ValueError):return jsonify(error="Invalid reminder lead time"),400
    previous=db().execute("SELECT group_name,completed FROM entries WHERE id=?",(entry_id,)).fetchone(); values=[int(v) if k in {"starred","processed","completed","archived","reminder_completed","reminder_notify_before_minutes"} and v is not None else (None if v is None else str(v)) for k,v in updates.items()]
    cur=db().execute(f"UPDATE entries SET {', '.join(k+'=?' for k in updates)} WHERE id=?",(*values,entry_id)); db().commit()
    if cur.rowcount and "group_name" in updates and previous["group_name"]!=updates["group_name"]:log_activity("info","collection_changed",f"Moved item from {previous['group_name'] or 'standalone'} to {updates['group_name'] or 'standalone'}",entry_id)
    if cur.rowcount and "completed" in updates and previous["completed"]!=int(updates["completed"]):log_activity("info","item_completed" if updates["completed"] else "item_reopened","Completed item" if updates["completed"] else "Reopened item",entry_id)
    return (jsonify(ok=True) if cur.rowcount else (jsonify(error="Not found"),404))

@app.post("/api/entries/bulk")
@app.post("/api/items/bulk")
@api_auth
def bulk():
    body=request.get_json(force=True); ids=[str(x) for x in body.get("ids",[])][:500]; action=body.get("action")
    mapping={"archive":("archived",1),"restore":("archived",0),"process":("processed",1),"unprocess":("processed",0),"complete":("completed",1),"reopen":("completed",0),"star":("starred",1),"unstar":("starred",0)}
    if not ids or action not in mapping:return jsonify(error="Invalid bulk request"),400
    field,value=mapping[action]; marks=",".join("?"*len(ids)); cur=db().execute(f"UPDATE entries SET {field}=? WHERE id IN ({marks})",(value,*ids)); db().commit()
    if action in {"complete","reopen"}:
        for entry_id in ids:log_activity("info","item_completed" if value else "item_reopened","Completed item" if value else "Reopened item",entry_id)
    return jsonify(ok=True,updated=cur.rowcount)

def remove_entry(entry_id):
    row=db().execute("SELECT audio_path FROM entries WHERE id=?",(entry_id,)).fetchone()
    if not row:return False
    db().execute("DELETE FROM group_suggestion_dismissals WHERE entry_id=?",(entry_id,)); db().execute("DELETE FROM entries WHERE id=?",(entry_id,)); db().commit()
    if row["audio_path"]:
        try:(AUDIO_DIR/row["audio_path"]).unlink(missing_ok=True)
        except OSError as error:log_activity("warning","cleanup","Audio cleanup failed",str(error))
    return True

@app.delete("/api/entries/<entry_id>")
@app.delete("/api/items/<entry_id>")
@api_auth
def delete_entry(entry_id): return jsonify(ok=True) if remove_entry(entry_id) else (jsonify(error="Not found"),404)

@app.delete("/api/entries")
@app.delete("/api/items")
@api_auth
def delete_bulk():
    ids=[str(x) for x in (request.get_json(force=True).get("ids") or [])][:500]; return jsonify(ok=True,deleted=sum(remove_entry(x) for x in ids))

@app.get("/api/entries/<entry_id>/audio")
@app.get("/api/items/<entry_id>/audio")
@api_auth
def audio(entry_id):
    row=db().execute("SELECT audio_path,audio_mime FROM entries WHERE id=?",(entry_id,)).fetchone()
    if not row or not row["audio_path"]:return jsonify(error="Audio not found"),404
    return send_file(AUDIO_DIR/row["audio_path"],mimetype=row["audio_mime"],download_name=row["audio_path"])

@app.post("/api/transcribe")
@api_auth
def transcribe():
    try:
        upload=next((request.files[key] for key in request.files if request.files[key].filename),None)
        return jsonify(ok=True,**transcribe_upload(upload))
    except (ValueError,RuntimeError) as error:return jsonify(error=str(error)),400
    except Exception as error:
        log_activity("error","transcription_error","A manual recording could not be transcribed",str(error)); return jsonify(error="Local transcription failed. Check the server logs."),500

@app.post("/api/manual")
@api_auth
def manual():
    try:
        payload=payload_from_request(); upload=next((request.files[k] for k in request.files if request.files[k].filename),None)
        receipt=existing_operation_receipt(payload,"manual")
        if receipt:
            result=json.loads(receipt["result_json"]);log_activity("info","duplicate","Duplicate interpreted operation ignored",result.get("id") or result.get("group") or "")
            return jsonify({"ok":True,**result,"created":False,"duplicate":True,"operationReceiptId":receipt["id"],"operationOutcome":receipt["status"],"operationReversible":bool(receipt["undo_kind"])}),200
        existing=existing_source_entry(payload,"manual")
        if existing:
            log_activity("info","duplicate","Duplicate manual capture ignored",existing["id"])
            return jsonify(ok=True,id=existing["id"],created=False,duplicate=True),200
        if upload and not first(payload,("transcription","transcript","text","content","note")):payload["transcription"]=transcribe_upload(upload)["transcription"]
        result=store_entry(payload,upload,"manual"); return jsonify(ok=True,**result),(201 if result["created"] else 200)
    except ValueError as error:
        body={"error":str(error)}
        if hasattr(error,"interpretation"):body["interpretation"]=error.interpretation
        return jsonify(body),409
    except Exception as error:
        log_activity("error","ingest_error","A manual capture could not be stored",str(error)); return jsonify(error="Manual capture failed"),500

@app.post("/api/interpret")
@api_auth
def interpret_dry_run():
    body=request.get_json(silent=True) or {}; reference=None
    if body.get("referenceAt"):
        try:reference=datetime.fromisoformat(str(body["referenceAt"]).replace("Z","+00:00"))
        except ValueError:return jsonify(error="Invalid reference time"),400
    return jsonify(interpretation_with_policy(interpret_capture(body.get("text",""),reference,body.get("collectionName",body.get("groupName","")))))

@app.get("/api/activity")
@api_auth
def activity():
    rows=[dict(r) for r in db().execute("SELECT * FROM activity ORDER BY id DESC LIMIT 100")]
    for row in rows:
        try:details=json.loads(row["details"] or "{}")
        except (TypeError,json.JSONDecodeError):continue
        receipt_id=details.get("receiptId")
        if receipt_id:
            receipt=db().execute("SELECT status,reversed_at,undo_kind FROM interpreted_operations WHERE id=?",(receipt_id,)).fetchone()
            details["confirmable"]=bool(receipt and receipt["status"]=="awaiting_confirmation")
            details["reversible"]=bool(receipt and receipt["undo_kind"] and not receipt["reversed_at"]);row["details"]=json.dumps(details)
    return jsonify(rows)

@app.get("/api/automation")
@api_auth
def automation_settings():
    return jsonify(enabled=setting_bool("automatic_execution",False),threshold=AUTO_EXECUTION_THRESHOLD,
      operations=sorted(AUTO_EXECUTION_OPERATIONS),safety="Only deterministic, non-destructive, single-match operations can run automatically.")

@app.patch("/api/automation")
@api_auth
def update_automation_settings():
    body=request.get_json(silent=True) or {}
    if not isinstance(body.get("enabled"),bool):return jsonify(error="enabled must be true or false"),400
    set_setting_bool("automatic_execution",body["enabled"])
    log_activity("info","automation_setting",f"Automatic execution {'enabled' if body['enabled'] else 'disabled'}")
    return automation_settings()

def model_settings_response():
    return jsonify(enabled=setting_bool("interpretation_model_enabled",False),configured=interpretation_model_configured(),
      name=INTERPRETATION_MODEL_NAME,url=INTERPRETATION_MODEL_URL,timeoutSeconds=INTERPRETATION_MODEL_TIMEOUT,**_INTERPRETATION_MODEL_STATUS)

@app.get("/api/model")
@api_auth
def interpretation_model_settings():return model_settings_response()

@app.patch("/api/model")
@api_auth
def update_interpretation_model_settings():
    body=request.get_json(silent=True) or {}
    if not isinstance(body.get("enabled"),bool):return jsonify(error="enabled must be true or false"),400
    if body["enabled"] and not interpretation_model_configured():return jsonify(error="Configure INTERPRETATION_MODEL_URL and INTERPRETATION_MODEL_NAME on the server first"),409
    set_setting_bool("interpretation_model_enabled",body["enabled"])
    log_activity("info","model_setting",f"Self-hosted interpretation model {'enabled' if body['enabled'] else 'disabled'}")
    return model_settings_response()

@app.post("/api/model/test")
@api_auth
def test_interpretation_model():
    if not interpretation_model_configured():return jsonify(error="The self-hosted model is not configured"),409
    try:
        with urllib.request.urlopen(f"{INTERPRETATION_MODEL_URL}/api/tags",timeout=INTERPRETATION_MODEL_TIMEOUT) as response:payload=json.loads(response.read(1024*1024))
        names={model.get("name") for model in payload.get("models",[]) if isinstance(model,dict)}
        if INTERPRETATION_MODEL_NAME not in names and not any(str(name).split(":")[0]==INTERPRETATION_MODEL_NAME.split(":")[0] for name in names):raise ValueError("Configured model is not installed")
        set_interpretation_model_status("available",f"Connected to {INTERPRETATION_MODEL_NAME}.");return model_settings_response()
    except Exception as error:
        set_interpretation_model_status("unavailable",f"Connection test failed: {type(error).__name__}")
        return model_settings_response(),503

@app.post("/api/operations/<receipt_id>/confirm")
@api_auth
def confirm_interpreted_operation(receipt_id):
    receipt=db().execute("SELECT * FROM interpreted_operations WHERE id=?",(receipt_id,)).fetchone()
    if not receipt:return jsonify(error="Operation receipt not found"),404
    if receipt["status"]!="awaiting_confirmation":return jsonify(error="Operation is not awaiting confirmation"),409
    proposed=json.loads(receipt["proposed_json"] or "{}");result=json.loads(receipt["result_json"] or "{}")
    if receipt["source"]!="ring" or proposed.get("operation")!="complete_item":return jsonify(error="This deferred operation cannot be confirmed"),409
    target_id=proposed.get("arguments",{}).get("itemId");command_id=result.get("id")
    cursor=db().execute("UPDATE entries SET completed=1 WHERE id=? AND archived=0 AND completed=0",(target_id,))
    if not cursor.rowcount:db().rollback();return jsonify(error="The matching Item is no longer available"),409
    if command_id:db().execute("UPDATE entries SET archived=1,processed=1,completed=1 WHERE id=?",(command_id,))
    undo={"id":target_id,"commandId":command_id}
    db().execute("UPDATE interpreted_operations SET status='executed',target_id=?,undo_kind='undo_ring_completion',undo_payload=? WHERE id=?",(target_id,json.dumps(undo),receipt_id));db().commit()
    details={"receiptId":receipt_id,"source":"ring","targetId":target_id,"operation":"complete_item","outcome":"executed","confirmable":False,"reversible":True,"reason":"Confirmed by the user.","confidence":receipt["confidence"]}
    log_activity("info","interpreted_operation", "Ring complete item: confirmed",json.dumps(details))
    return jsonify(ok=True,receiptId=receipt_id,status="executed",targetId=target_id)

@app.post("/api/operations/<receipt_id>/undo")
@api_auth
def undo_interpreted_operation(receipt_id):
    receipt=db().execute("SELECT * FROM interpreted_operations WHERE id=?",(receipt_id,)).fetchone()
    if not receipt:return jsonify(error="Operation receipt not found"),404
    if receipt["reversed_at"]:return jsonify(error="Operation has already been undone"),409
    payload=json.loads(receipt["undo_payload"] or "{}");kind=receipt["undo_kind"]
    if not kind:return jsonify(error="This operation cannot be undone"),409
    if kind=="remove_item":
        if not remove_entry(payload.get("id","")):return jsonify(error="The created Item is no longer available"),409
    elif kind=="reopen_item":
        cursor=db().execute("UPDATE entries SET completed=0 WHERE id=? AND completed=1",(payload.get("id"),));db().commit()
        if not cursor.rowcount:return jsonify(error="The completed Item is no longer available to reopen"),409
    elif kind=="remove_collection":
        group=find_group(payload.get("name",""))
        if not group:return jsonify(error="The created Collection no longer exists"),409
        if db().execute("SELECT 1 FROM entries WHERE group_name=? LIMIT 1",(group["name"],)).fetchone():return jsonify(error="The Collection now contains Items and cannot be removed automatically"),409
        db().execute("DELETE FROM note_group_aliases WHERE group_name=?",(group["name"],));db().execute("DELETE FROM note_groups WHERE name=?",(normalized_group_name(group["name"]),));db().commit()
    elif kind=="undo_ring_completion":
        cursor=db().execute("UPDATE entries SET completed=0 WHERE id=? AND completed=1",(payload.get("id"),))
        if not cursor.rowcount:db().rollback();return jsonify(error="The completed Item is no longer available to reopen"),409
        if payload.get("commandId"):db().execute("UPDATE entries SET archived=0,processed=0,completed=0 WHERE id=?",(payload["commandId"],))
        db().commit()
    else:return jsonify(error="Unknown recovery action"),409
    reversed=now();db().execute("UPDATE interpreted_operations SET status='undone',reversed_at=? WHERE id=?",(reversed,receipt_id));db().commit()
    log_activity("info","interpreted_operation_undone",f"Undid {receipt['operation'].replace('_',' ')}",json.dumps({"receiptId":receipt_id,"operation":receipt["operation"],"outcome":"undone","reversible":False}))
    return jsonify(ok=True,receiptId=receipt_id,status="undone")

@app.get("/api/changes")
@api_auth
def changes():
    latest=db().execute("SELECT coalesce(max(id),0) FROM activity").fetchone()[0]
    if "since" not in request.args:return jsonify(sequence=latest,events=[])
    try:since=max(int(request.args["since"]),0)
    except ValueError:return jsonify(error="since must be a non-negative integer"),400
    return jsonify(change_feed_since(since,latest))

def change_feed_since(since,latest=None):
    if latest is None:latest=db().execute("SELECT coalesce(max(id),0) FROM activity").fetchone()[0]
    placeholders=",".join("?" for _ in CAPTURE_EVENT_KINDS)
    rows=db().execute(f"""SELECT id,created_at,level,kind,message,
      CASE WHEN kind IN ('capture_standalone','capture_grouped','group_unrecognized','item_completed','item_reopened','collection_changed') THEN details ELSE '' END AS details
      FROM activity WHERE id>? AND kind IN ({placeholders}) ORDER BY id LIMIT 50""",(since,*sorted(CAPTURE_EVENT_KINDS))).fetchall()
    sequence=rows[-1]["id"] if len(rows)==50 else latest
    return {"sequence":sequence,"events":[dict(row) for row in rows]}

@app.get("/api/changes/wait")
@api_auth
def wait_for_changes():
    try:since=max(int(request.args.get("since","0")),0); timeout=min(max(float(request.args.get("timeout","25")),0),30)
    except ValueError:return jsonify(error="since and timeout must be non-negative numbers"),400
    deadline=time.monotonic()+timeout
    while True:
        feed=change_feed_since(since)
        if feed["events"] or time.monotonic()>=deadline:return jsonify(feed)
        time.sleep(min(0.5,max(deadline-time.monotonic(),0)))

@app.errorhandler(413)
def capture_too_large(_error):
    if request.path in {"/webhook/index","/api/manual","/api/transcribe"}:log_activity("error","ingest_error","A capture exceeded the upload size limit")
    return jsonify(error="Capture exceeds the configured upload size limit"),413

@app.get("/api/status")
@api_auth
def status():
    count=db().execute("SELECT count(*) FROM entries").fetchone()[0]; audio_count=db().execute("SELECT count(*) FROM entries WHERE audio_path IS NOT NULL").fetchone()[0]
    audio_bytes=sum(p.stat().st_size for p in AUDIO_DIR.iterdir() if p.is_file()); db_bytes=DB_PATH.stat().st_size if DB_PATH.exists() else 0
    backup=db().execute("SELECT * FROM backup_runs ORDER BY requested_at DESC LIMIT 1").fetchone(); latest=db().execute("SELECT * FROM backup_runs WHERE status='success' ORDER BY completed_at DESC LIMIT 1").fetchone()
    return jsonify(entries=count,audioEntries=audio_count,audioBytes=audio_bytes,databaseBytes=db_bytes,lastBackupHook=BACKUP_HOOK_URL!="",trustedProxyHops=TRUSTED_PROXY_HOPS,lastBackup=dict(backup) if backup else None,latestVerifiedBackup=dict(latest) if latest else None,transcriptionEnabled=TRANSCRIPTION_ENABLED,transcriptionModel=TRANSCRIPTION_MODEL)

def export_item(row):
    item=dict(row);item["collection_name"]=item["group_name"];return item

def export_rows(): return [export_item(r) for r in db().execute("SELECT * FROM entries ORDER BY created_at DESC")]

def markdown_export(rows, title=None):
    heading=f"# {title}\n\n" if title else ""
    return heading+"\n\n".join(f"## {r['recorded_at'] or r['created_at']}\n\n{r['transcription']}\n\nCategory: {r['category']}\n\nCollection: {r.get('collection_name') or r.get('group_name') or 'Standalone'}\n\nCompleted: {'yes' if r.get('completed') else 'no'}\n\nProcessed: {'yes' if r.get('processed') else 'no'}\n\nTags: {r['tags']}" for r in rows)

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
@app.get("/api/items/export/<fmt>")
@api_auth
def export(fmt):
    return export_response(fmt,export_rows(),"index-inbox")

@app.get("/api/groups/<name>/export/<fmt>")
@app.get("/api/collections/<name>/export/<fmt>")
@api_auth
def export_group(name,fmt):
    group=find_group(name)
    if not group:return jsonify(error="Group not found"),404
    canonical=group["name"]
    return export_response(fmt,[export_item(row) for row in group_entries(canonical)],f"index-inbox-{canonical.lower()}",canonical)

@app.post("/api/maintenance/retention")
@api_auth
def retention():
    days=max(int((request.get_json(silent=True) or {}).get("audioDays",30)),1); cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    rows=db().execute("SELECT id,audio_path FROM entries WHERE audio_path IS NOT NULL AND created_at<?",(cutoff,)).fetchall(); removed=0
    for row in rows:
        (AUDIO_DIR/row["audio_path"]).unlink(missing_ok=True); db().execute("UPDATE entries SET audio_path=NULL,audio_mime=NULL WHERE id=?",(row["id"],)); removed+=1
    db().commit(); log_activity("info","retention",f"Removed {removed} old audio files"); return jsonify(ok=True,removed=removed)

def file_digest(path):
    digest=hashlib.sha256()
    with open(path,"rb") as source:
        for chunk in iter(lambda:source.read(1024*1024),b""):digest.update(chunk)
    return digest.hexdigest(),path.stat().st_size

@app.get("/api/android-update")
@api_auth
def android_update():
    if not ANDROID_UPDATE_VERSION_CODE or not ANDROID_UPDATE_VERSION_NAME or not ANDROID_UPDATE_APK.is_file():
        return jsonify(available=False)
    digest,size=file_digest(ANDROID_UPDATE_APK)
    return jsonify(available=True,versionCode=ANDROID_UPDATE_VERSION_CODE,versionName=ANDROID_UPDATE_VERSION_NAME,bytes=size,sha256=digest)

@app.get("/api/android-update/apk")
@api_auth
def android_update_apk():
    if not ANDROID_UPDATE_APK.is_file():return jsonify(error="Android update is not configured"),404
    return send_file(ANDROID_UPDATE_APK,as_attachment=True,download_name="index-inbox.apk",mimetype="application/vnd.android.package-archive")

def create_backup_archive():
    run_id=str(uuid.uuid4()); requested=now(); archive_name=f"index-inbox-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{run_id[:8]}.zip"
    db().execute("INSERT INTO backup_runs(id,requested_at,status,archive_name) VALUES(?,?,?,?)",(run_id,requested,"running",archive_name)); db().commit()
    snapshot_path=None; temporary_archive=None
    try:
        snapshot=tempfile.NamedTemporaryFile(prefix="index-inbox-snapshot-",suffix=".sqlite3",delete=False); snapshot_path=Path(snapshot.name); snapshot.close()
        destination=sqlite3.connect(snapshot_path); db().backup(destination)
        completed=now(); destination.execute("UPDATE backup_runs SET status='success',completed_at=?,archive_name=?,error='' WHERE id=?",(completed,archive_name,run_id)); destination.commit()
        check=destination.execute("PRAGMA quick_check").fetchone()[0]
        if check!="ok":raise RuntimeError(f"SQLite snapshot integrity check failed: {check}")
        entry_count=destination.execute("SELECT count(*) FROM entries").fetchone()[0]
        audio_rows=destination.execute("SELECT audio_path FROM entries WHERE audio_path IS NOT NULL ORDER BY audio_path").fetchall()
        completed_items=destination.execute("SELECT count(*) FROM entries WHERE completed=1").fetchone()[0]
        collections=destination.execute("SELECT count(*) FROM note_groups").fetchone()[0]
        schema_version=destination.execute("PRAGMA user_version").fetchone()[0]
        destination.close()
        files={}; digest,size=file_digest(snapshot_path); files["index-inbox.sqlite3"]={"sha256":digest,"bytes":size}
        audio_paths=[]
        for row in audio_rows:
            source=AUDIO_DIR/row[0]
            if not source.is_file():raise RuntimeError(f"Stored audio file is missing: {row[0]}")
            digest,size=file_digest(source); archive_path=f"audio/{row[0]}"; files[archive_path]={"sha256":digest,"bytes":size}; audio_paths.append((source,archive_path))
        manifest={"version":1,"schemaVersion":schema_version,"createdAt":completed,"runId":run_id,"entries":entry_count,"completedItems":completed_items,"collections":collections,"audioEntries":len(audio_paths),"files":files}
        handle=tempfile.NamedTemporaryFile(prefix=".backup-",suffix=".zip",dir=BACKUP_DIR,delete=False); temporary_archive=Path(handle.name); handle.close()
        with zipfile.ZipFile(temporary_archive,"w",zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot_path,"index-inbox.sqlite3")
            for source,archive_path in audio_paths:archive.write(source,archive_path)
            archive.writestr("manifest.json",json.dumps(manifest,indent=2,sort_keys=True))
        final_path=BACKUP_DIR/archive_name; temporary_archive.replace(final_path); archive_bytes=final_path.stat().st_size
        db().execute("UPDATE backup_runs SET status='success',completed_at=?,archive_bytes=?,error='' WHERE id=?",(completed,archive_bytes,run_id)); db().commit()
        archives=sorted(BACKUP_DIR.glob("index-inbox-*.zip"),key=lambda path:path.stat().st_mtime,reverse=True)
        for old in archives[5:]:old.unlink(missing_ok=True)
        log_activity("info","backup",f"Created verified backup {archive_name}",run_id); return dict(db().execute("SELECT * FROM backup_runs WHERE id=?",(run_id,)).fetchone())
    except Exception as error:
        db().execute("UPDATE backup_runs SET status='failed',completed_at=?,error=? WHERE id=?",(now(),str(error)[:1000],run_id)); db().commit(); log_activity("error","backup","Local backup failed",str(error)); raise
    finally:
        if snapshot_path:snapshot_path.unlink(missing_ok=True)
        if temporary_archive:temporary_archive.unlink(missing_ok=True)

def verify_backup_archive(path):
    with zipfile.ZipFile(path) as archive:
        try:manifest=json.loads(archive.read("manifest.json"))
        except (KeyError,json.JSONDecodeError) as error:raise ValueError("Backup manifest is missing or invalid") from error
        if manifest.get("version")!=1 or not isinstance(manifest.get("files"),dict):raise ValueError("Unsupported backup manifest")
        expected={"manifest.json",*manifest["files"]}
        if set(archive.namelist())!=expected:raise ValueError("Backup contains missing or unexpected files")
        for name,metadata in manifest["files"].items():
            digest=hashlib.sha256(); size=0
            with archive.open(name) as source:
                for chunk in iter(lambda:source.read(1024*1024),b""):digest.update(chunk); size+=len(chunk)
            if digest.hexdigest()!=metadata.get("sha256") or size!=metadata.get("bytes"):raise ValueError(f"Backup checksum failed for {name}")
        with tempfile.TemporaryDirectory(prefix="index-inbox-verify-") as directory:
            snapshot=Path(directory)/"index-inbox.sqlite3"; snapshot.write_bytes(archive.read("index-inbox.sqlite3")); connection=sqlite3.connect(snapshot)
            check=connection.execute("PRAGMA quick_check").fetchone()[0]; entries=connection.execute("SELECT count(*) FROM entries").fetchone()[0]; audio_entries=connection.execute("SELECT count(*) FROM entries WHERE audio_path IS NOT NULL").fetchone()[0]
            columns={row[1] for row in connection.execute("PRAGMA table_info(entries)")};completed_items=connection.execute("SELECT count(*) FROM entries WHERE completed=1").fetchone()[0] if "completed" in columns else 0
            collections=connection.execute("SELECT count(*) FROM note_groups").fetchone()[0];schema_version=connection.execute("PRAGMA user_version").fetchone()[0];connection.close()
        if check!="ok" or entries!=manifest.get("entries") or audio_entries!=manifest.get("audioEntries") or ("completedItems" in manifest and completed_items!=manifest["completedItems"]) or ("collections" in manifest and collections!=manifest["collections"]):raise ValueError("Backup database contents do not match the manifest")
        return {"ok":True,"runId":manifest["runId"],"entries":entries,"completedItems":completed_items,"collections":collections,"audioEntries":audio_entries,"schemaVersion":schema_version,"createdAt":manifest["createdAt"]}

@app.post("/api/backups")
@api_auth
def create_backup():
    try:return jsonify(ok=True,backup=create_backup_archive()),201
    except Exception:return jsonify(error="Backup creation failed; inspect Recent activity"),500

@app.get("/api/backups/latest")
@api_auth
def download_latest_backup():
    row=db().execute("SELECT archive_name FROM backup_runs WHERE status='success' ORDER BY completed_at DESC LIMIT 1").fetchone()
    if not row or not (BACKUP_DIR/row["archive_name"]).is_file():return jsonify(error="No local backup is available"),404
    return send_file(BACKUP_DIR/row["archive_name"],mimetype="application/zip",as_attachment=True,download_name=row["archive_name"])

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
    db().execute("UPDATE local_users SET password_hash=?,password_changed_at=?,session_version=session_version+1 WHERE id=?",(PASSWORD_HASHER.hash(password),now(),user["id"])); db().execute("DELETE FROM local_sessions WHERE user_id=?",(user["id"],)); db().execute("DELETE FROM local_device_tokens WHERE user_id=?",(user["id"],)); db().commit(); click.echo(f"Password changed and all browser and native app sessions revoked for {username}")

@auth_cli.command("revoke-sessions")
@click.option("--username",prompt=True)
def revoke_local_sessions(username):
    username=username.strip().lower(); user=db().execute("SELECT id FROM local_users WHERE username=?",(username,)).fetchone()
    if not user:raise click.ClickException("Local user not found")
    db().execute("UPDATE local_users SET session_version=session_version+1 WHERE id=?",(user["id"],)); db().execute("DELETE FROM local_sessions WHERE user_id=?",(user["id"],)); db().execute("DELETE FROM local_device_tokens WHERE user_id=?",(user["id"],)); db().commit(); click.echo(f"All browser and native app sessions revoked for {username}")

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

@app.cli.group("backup")
def backup_cli():"""Create and verify local backup archives."""

@backup_cli.command("create")
def backup_create_cli():
    result=create_backup_archive(); click.echo(str(BACKUP_DIR/result["archive_name"]))

@backup_cli.command("verify")
@click.argument("archive",type=click.Path(exists=True,dir_okay=False,path_type=Path))
def backup_verify_cli(archive):
    try:result=verify_backup_archive(archive)
    except (ValueError,zipfile.BadZipFile) as error:raise click.ClickException(str(error))
    click.echo(f"Verified {archive}: {result['entries']} entries, {result['audioEntries']} audio files, created {result['createdAt']}")
@app.get("/")
def index():return send_from_directory("static","index.html")
@app.get("/<path:path>")
def static_files(path):return send_from_directory("static",path)
