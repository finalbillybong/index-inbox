import csv, hashlib, io, json, os, re, secrets, sqlite3, urllib.request, uuid, zipfile
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import firebase_admin
from firebase_admin import auth
from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")); AUDIO_DIR = DATA_DIR / "audio"; DB_PATH = DATA_DIR / "index-inbox.sqlite3"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", ""); PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")
ALLOWED_EMAILS = {x.strip().lower() for x in os.getenv("ALLOWED_EMAILS", "").split(",") if x.strip()}
REQUIRE_VERIFIED_EMAIL = os.getenv("REQUIRE_VERIFIED_EMAIL", "false").lower() == "true"
MAX_AUDIO_BYTES = int(os.getenv("MAX_AUDIO_MB", "25")) * 1024 * 1024
BACKUP_HOOK_URL = os.getenv("BACKUP_HOOK_URL", "")
VALID_CATEGORIES = {"note", "task", "idea", "question"}
DATA_DIR.mkdir(parents=True, exist_ok=True); AUDIO_DIR.mkdir(parents=True, exist_ok=True)
app = Flask(__name__, static_folder=None); app.config["MAX_CONTENT_LENGTH"] = MAX_AUDIO_BYTES + 1024 * 1024
if PROJECT_ID and not firebase_admin._apps: firebase_admin.initialize_app(options={"projectId": PROJECT_ID})

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
    """)
    columns = {r[1] for r in con.execute("PRAGMA table_info(entries)")}
    additions = {"title":"TEXT NOT NULL DEFAULT ''", "category":"TEXT NOT NULL DEFAULT 'note'",
      "archived":"INTEGER NOT NULL DEFAULT 0", "source_key":"TEXT"}
    for name, definition in additions.items():
        if name not in columns: con.execute(f"ALTER TABLE entries ADD COLUMN {name} {definition}")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_source_key ON entries(source_key) WHERE source_key IS NOT NULL")
    con.execute("UPDATE entries SET category='note' WHERE category='action'")
    con.commit(); con.close()
init_db()

def now(): return datetime.now(timezone.utc).isoformat()
def log_activity(level, kind, message, details=""):
    db().execute("INSERT INTO activity(created_at,level,kind,message,details) VALUES(?,?,?,?,?)", (now(),level,kind,message,details)); db().commit()

def api_auth(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not PROJECT_ID: return jsonify(error="FIREBASE_PROJECT_ID is not configured"), 503
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "): return jsonify(error="Missing Firebase bearer token"), 401
        try: claims = auth.verify_id_token(header[7:], check_revoked=True)
        except Exception: return jsonify(error="Invalid or expired Firebase token"), 401
        email = claims.get("email", "").lower()
        if REQUIRE_VERIFIED_EMAIL and not claims.get("email_verified"): return jsonify(error="Email address is not verified"), 403
        if ALLOWED_EMAILS and email not in ALLOWED_EMAILS: return jsonify(error="This account is not allowed"), 403
        g.user = claims; return fn(*args, **kwargs)
    return wrapped

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
        if existing: log_activity("info","duplicate","Duplicate webhook ignored",existing["id"]); return existing["id"], False
    audio_path=audio_mime=None
    if upload and upload.filename:
        suffix=Path(upload.filename).suffix.lower()[:10] or ".bin"; audio_path=f"{entry_id}{suffix}"; audio_mime=upload.mimetype or "application/octet-stream"; upload.save(AUDIO_DIR/audio_path)
    title=first(payload,("title",),""); explicit_category=first(payload,("category",),"")
    category,cleaned=voice_category(transcription)
    if explicit_category in VALID_CATEGORIES: category=explicit_category
    elif source=="ring": transcription=cleaned
    db().execute("""INSERT INTO entries(id,created_at,recorded_at,transcription,trigger_type,audio_path,audio_mime,payload_json,source_key,title,category)
      VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(entry_id,now(),recorded,transcription,trigger,audio_path,audio_mime,json.dumps(payload,ensure_ascii=False),source_key,title,category)); db().commit()
    log_activity("info","ingest",f"Captured {source} entry",entry_id); return entry_id, True

@app.get("/health")
def health():
    try: db().execute("SELECT 1"); return jsonify(ok=True)
    except sqlite3.Error: return jsonify(ok=False),503

@app.post("/webhook/index")
def ingest():
    if not webhook_authorized():
        log_activity("warning","auth","Rejected webhook authentication",request.remote_addr or ""); return jsonify(error="Invalid webhook secret"),401
    payload=payload_from_request(); upload=next((request.files[k] for k in request.files if request.files[k].filename),None)
    entry_id,created=store_entry(payload,upload); return jsonify(ok=True,id=entry_id,duplicate=not created),(201 if created else 200)

@app.get("/api/entries")
@api_auth
def entries():
    where=[]; values=[]
    q=request.args.get("q","").strip()
    if q: where.append("(transcription LIKE ? OR title LIKE ? OR tags LIKE ?)"); values += [f"%{q}%"]*3
    for field in ("category","processed","starred","archived"):
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
    payload=payload_from_request(); upload=next((request.files[k] for k in request.files if request.files[k].filename),None); entry_id,_=store_entry(payload,upload,"manual"); return jsonify(ok=True,id=entry_id),201

@app.get("/api/activity")
@api_auth
def activity(): return jsonify([dict(r) for r in db().execute("SELECT * FROM activity ORDER BY id DESC LIMIT 100")])

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
    config={"apiKey":os.getenv("FIREBASE_API_KEY",""),"authDomain":os.getenv("FIREBASE_AUTH_DOMAIN",""),"projectId":PROJECT_ID}; return Response(f"window.FIREBASE_CONFIG={json.dumps(config)};",mimetype="application/javascript")
@app.get("/")
def index():return send_from_directory("static","index.html")
@app.get("/<path:path>")
def static_files(path):return send_from_directory("static",path)
