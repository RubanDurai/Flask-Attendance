# app.py - Single-file Flask app with MongoDB, bcrypt, server-side sessions, per-user attendance
import os
import secrets
import json
from datetime import date, datetime, timedelta

from flask import Flask, render_template_string, request, jsonify, redirect, make_response, g
from pymongo import MongoClient, ASCENDING
from bson.objectid import ObjectId
import bcrypt

# ---------- Config ----------
MONGO_URI = os.getenv("MONGO_URI", ""mongodb+srv://tnbots:tnbots@cluster0.lkuiies.mongodb.net/?retryWrites=true&w=majority)
if not MONGO_URI:
    raise RuntimeError("MONGO_URI environment variable not set. Set it to your MongoDB connection string.")

DB_NAME = os.getenv("MONGO_DBNAME", "attendance_app")
SESSION_COOKIE_NAME = "session"   # HttpOnly cookie set on login
SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

# ---------- MongoDB client ----------
client = MongoClient(MONGO_URI)
db = client[DB_NAME]

# ensure indexes
db.users.create_index([("username", ASCENDING)], unique=True)
db.sessions.create_index([("token", ASCENDING)], unique=True)
db.attendance.create_index([("user_id", ASCENDING), ("day_iso", ASCENDING)], unique=True)

# ---------- Helpers: users / sessions / attendance ----------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

def create_user(username: str, name: str, password: str):
    username = username.strip().lower()
    name = name.strip()
    if not username or not name or not password:
        return False, "missing_fields"
    if db.users.find_one({"username": username}):
        return False, "username_taken"
    ph = hash_password(password)
    res = db.users.insert_one({"username": username, "name": name, "password_hash": ph, "created_at": datetime.utcnow()})
    return True, str(res.inserted_id)

def authenticate_user(username: str, password: str):
    if not username or not password:
        return None
    username = username.strip().lower()
    user = db.users.find_one({"username": username})
    if not user:
        return None
    if check_password(password, user.get("password_hash", "")):
        # return a sanitized user dict
        return {"_id": user["_id"], "username": user["username"], "name": user["name"]}
    return None

def create_session_for_user(user_id: ObjectId):
    token = secrets.token_urlsafe(32)
    db.sessions.insert_one({"token": token, "user_id": user_id, "created_at": datetime.utcnow()})
    return token

def get_user_by_session_token(token: str):
    if not token:
        return None
    s = db.sessions.find_one({"token": token})
    if not s:
        return None
    user = db.users.find_one({"_id": s["user_id"]})
    if not user:
        # orphaned session: remove it
        db.sessions.delete_one({"_id": s["_id"]})
        return None
    return {"_id": user["_id"], "username": user["username"], "name": user["name"]}

def delete_session(token: str):
    if token:
        db.sessions.delete_one({"token": token})

# Attendance helpers (per-user)
def upsert_attendance(user_id: ObjectId, day_iso: str, shift: str, status: str, ot_hours: float):
    try:
        db.attendance.update_one(
            {"user_id": user_id, "day_iso": day_iso},
            {"$set": {"shift": shift, "status": status, "ot_hours": float(ot_hours), "updated_at": datetime.utcnow()}},
            upsert=True
        )
        return True
    except Exception as e:
        return False

def get_attendance_for_user(user_id: ObjectId, start_iso: str, end_iso: str):
    cursor = db.attendance.find({"user_id": user_id, "day_iso": {"$gte": start_iso, "$lte": end_iso}})
    out = {}
    for r in cursor:
        out[r["day_iso"]] = {"shift": r.get("shift"), "status": r.get("status"), "ot_hours": r.get("ot_hours", 0)}
    return out

def get_attendance_record(user_id: ObjectId, day_iso: str):
    r = db.attendance.find_one({"user_id": user_id, "day_iso": day_iso})
    if not r:
        return None
    return {"shift": r.get("shift"), "status": r.get("status"), "ot_hours": r.get("ot_hours", 0)}

def delete_attendance_record(user_id: ObjectId, day_iso: str):
    db.attendance.delete_one({"user_id": user_id, "day_iso": day_iso})

def summary_for_user_in_range(user_id: ObjectId, start_iso: str, end_iso: str):
    cursor = db.attendance.find({"user_id": user_id, "day_iso": {"$gte": start_iso, "$lte": end_iso}})
    present = absent = 0
    ot_total = 0.0
    shift_dates = {}
    for r in cursor:
        st = r.get("status")
        if st == "Present":
            present += 1
            try:
                ot_total += float(r.get("ot_hours") or 0)
            except:
                pass
        elif st == "Absent":
            absent += 1
        sh = (r.get("shift") or "").strip()
        if st == "Present" and sh and sh != "GEN":
            try:
                d = datetime.fromisoformat(r["day_iso"]).day
                shift_dates.setdefault(sh, []).append(d)
            except:
                pass
    return {"present": present, "absent": absent, "ot_hours": round(ot_total, 1), "shift_dates": shift_dates}

def make_shift_line_html(shift_dates):
    ordered_shifts = ["FS", "SS", "NS", "GEN2"]
    shift_names = {"FS": "First Shift", "SS": "Second Shift", "NS": "Night Shift"}
    parts = []
    for s in ordered_shifts:
        if s in shift_dates and shift_dates[s]:
            parts.append(f"{shift_names.get(s,s)}: {', '.join(str(x) for x in sorted(set(shift_dates[s])))}")
    for s in sorted(shift_dates.keys()):
        if s not in ordered_shifts and shift_dates[s]:
            parts.append(f"{s}: {', '.join(str(x) for x in sorted(set(shift_dates[s])))}")
    return "<br>".join(parts)

# ---------- Decorator-like helper to require session ----------
def require_user_from_request():
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None, (jsonify({"error": "not_authenticated"}), 401)
    user = get_user_by_session_token(token)
    if not user:
        # clear cookie client-side by returning special response if used by browser
        resp = jsonify({"error": "invalid_session"})
        resp.delete_cookie(SESSION_COOKIE_NAME, path="/")
        return None, (resp, 401)
    return user, None

# ---------- INLINE HTML Templates ----------
# - LOGIN_HTML: uses /api/register and /api/login
# - MAIN_HTML: attendance UI (same structure you provided), with fetch requests using credentials: 'same-origin'
LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Login / Register</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;font-family:Poppins,system-ui,Segoe UI,Roboto,Helvetica,Arial}
body{background:#f9f9f9;display:flex;align-items:center;justify-content:center;height:100vh}
.container{width:360px;background:#fff;padding:28px;border-radius:12px;box-shadow:0 6px 24px rgba(0,0,0,0.08)}
.tabs{display:flex;border-radius:22px;overflow:hidden;border:1px solid #e6e6e6;margin-bottom:20px}
.tab{flex:1;padding:10px 0;text-align:center;cursor:pointer;font-weight:600;color:#444;background:#fff}
.tab.active{background:#ffcc33;color:#000}
.input-group{position:relative;margin:12px 0}
.input-group input{width:100%;padding:11px 14px 11px 40px;border:1px solid #d7d7d7;border-radius:8px;outline:none;font-size:14px}
.input-icon{position:absolute;left:10px;top:11px;width:18px;height:18px;opacity:0.95}
.btn{width:100%;padding:12px;border-radius:22px;border:none;background:#ffcc33;font-weight:700;cursor:pointer;margin-top:12px}
.btn:hover{background:#ffd84d}
.center{font-size:13px;margin-top:10px;text-align:center}
.link{color:#ffb400;text-decoration:none;cursor:pointer}
.small{font-size:12px;color:#666;margin-top:6px}
.err{color:#c00;font-size:13px;margin-top:8px;text-align:center}
</style>
</head>
<body>
<div class="container" role="main">
  <div class="tabs">
    <div id="tabLogin" class="tab active">Login</div>
    <div id="tabRegister" class="tab">Register</div>
  </div>

  <form id="loginForm" autocomplete="off" onsubmit="return false;">
    <div class="input-group">
      <input id="loginUser" placeholder="Username" />
    </div>
    <div class="input-group">
      <input id="loginPass" type="password" placeholder="Password" />
    </div>
    <button class="btn" id="btnLogin" type="button">Login</button>
    <div class="center small">No account? <span class="link" id="toRegister">Register</span></div>
    <div id="loginErr" class="err"></div>
  </form>

  <form id="registerForm" style="display:none" autocomplete="off" onsubmit="return false;">
    <div class="input-group">
      <input id="regName" placeholder="Full Name" />
    </div>
    <div class="input-group">
      <input id="regUser" placeholder="Choose username" />
    </div>
    <div class="input-group">
      <input id="regPass" type="password" placeholder="Choose password" />
    </div>
    <button class="btn" id="btnRegister" type="button">Sign Up</button>
    <div class="center small">Already registered? <span class="link" id="toLogin">Login</span></div>
    <div id="regErr" class="err"></div>
  </form>
</div>

<script>
const $ = id => document.getElementById(id);
const switchTab = (to) => {
  const loginOn = (to === 'login');
  $('tabLogin').classList.toggle('active', loginOn);
  $('tabRegister').classList.toggle('active', !loginOn);
  $('loginForm').style.display = loginOn ? '' : 'none';
  $('registerForm').style.display = loginOn ? 'none' : '';
};

document.addEventListener('DOMContentLoaded', () => {
  // If session cookie exists, go to /
  if (document.cookie.split('; ').find(row => row.startsWith('session='))) {
    window.location.href = '/';
    return;
  }

  $('tabLogin').onclick = () => switchTab('login');
  $('tabRegister').onclick = () => switchTab('register');
  $('toRegister').onclick = (e) => { e.preventDefault(); switchTab('register'); };
  $('toLogin').onclick = (e) => { e.preventDefault(); switchTab('login'); };

  $('btnRegister').onclick = async () => {
    $('regErr').textContent = '';
    const name = $('regName').value.trim();
    const username = $('regUser').value.trim();
    const password = $('regPass').value.trim();
    if (!name || !username || !password) { $('regErr').textContent = 'Please fill all fields'; return; }
    try {
      const res = await fetch('/api/register', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({name, username, password})
      });
      const j = await res.json();
      if (!res.ok) {
        $('regErr').textContent = j.error || 'Register failed';
      } else {
        alert('Account created. Please login.');
        switchTab('login');
      }
    } catch (e) { $('regErr').textContent = 'Network error'; }
  };

  $('btnLogin').onclick = async () => {
    $('loginErr').textContent = '';
    const username = $('loginUser').value.trim();
    const password = $('loginPass').value.trim();
    if (!username || !password) { $('loginErr').textContent = 'Please enter username and password'; return; }
    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({username, password})
      });
      const j = await res.json();
      if (!res.ok) {
        $('loginErr').textContent = j.error || 'Login failed';
      } else {
        window.location.href = '/';
      }
    } catch (e) { $('loginErr').textContent = 'Network error'; }
  };

  document.addEventListener('keydown', (e) => { if (e.key === 'Enter') { const regVisible = window.getComputedStyle($('registerForm')).display !== 'none'; if (regVisible) $('btnRegister').click(); else $('btnLogin').click(); } });
});
</script>
</body></html>
"""

# MAIN_HTML: (attendance UI). Uses Jinja variables injected from server.
MAIN_HTML = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Self Attendance</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
body{background:#fafbff;font-family:system-ui;margin:0;padding:0;}
.container-wrap{max-width:980px;margin:24px auto;padding:12px;}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.header .welcome{font-weight:700}
.header .actions{display:flex;gap:8px;align-items:center}
.calendar-card{position:relative;width:100%;max-width:739px;height:314px;margin:0 auto;
  background:url('http://176.9.41.10:8080/dl/690a8f9cac442ce7a2ee3114') no-repeat center;background-size:cover;
  border-radius:15px;box-shadow:0 6px 20px rgba(20,20,30,.06);display:flex;justify-content:center;align-items:flex-start;}
.month-nav{position:absolute;top:220px;left:50%;transform:translateX(-50%);display:flex;align-items:center;
  justify-content:space-between;background:rgba(255,255,255,0.25);backdrop-filter:blur(12px);border-radius:14px;
  padding:8px 20px;width:220px;box-shadow:0 3px 8px rgba(0,0,0,0.05);border:1px solid rgba(255,255,255,0.4);}
.nav-btn{color:#333;font-size:18px;text-decoration:none;transition:.2s;}
.nav-btn:hover{color:#000;transform:scale(1.1);}
.month-label{font-weight:700;font-size:18px;color:#111;}
.day-cell{height:92px;border:1px solid #f0f2f7;cursor:pointer;position:relative;padding:8px;background:white;transition:transform .06s;}
.day-cell:active{transform:scale(.997);}
.day-num{font-weight:700;font-size:14px;}
.present{background:#e9fbe9;}
.absent{background:#fff0f0;}
.sunday{background:#f2f2f2;color:#6b7280;}
.shift-label{font-size:12px;color:#6b7280;margin:0 2px;}
.status-pill{position:absolute;right:10px;top:18px;padding:4px 0;border-radius:999px;font-size:12px;background:white;border:1px solid #e6e9ef;min-width:20px;text-align:center;}
.status-pill:empty{display:none;}
.ot-badge{font-size:10px;color:#374151;margin-top:0;display:block;}
.today-border{outline:3px solid rgba(59,130,246,0.18);border-radius:8px;}
.summary-box{margin-top:18px;padding:10px;border-radius:10px;background:#f7f7f7;border:1px solid #eee;display:flex;flex-direction:column;gap:6px;}
.summary-top{font-weight:700;}
.summary-top span{margin-right:6px;}
.summary-shifts{font-weight:600;color:#374151;}
.table-responsive{margin-top:14px}
</style></head><body>
<div class="container-wrap">
  <div class="header">
    <div class="welcome">Hi, <span id="userName">{{ user_name }}</span></div>
    <div class="actions">
      <a class="btn btn-sm btn-outline-secondary" href="/api/logout">Logout</a>
    </div>
  </div>

  <div class="calendar-card">
    <div class="month-nav shadow-sm">
      <a class="nav-btn" href="/?month={{ month-1 if month>1 else 12 }}&year={{ year if month>1 else year-1 }}"><i class="fa fa-arrow-left"></i></a>
      <div class="month-label">{{ calendar.month_name[month] }} {{ year }}</div>
      <a class="nav-btn" href="/?month={{ month+1 if month<12 else 1 }}&year={{ year if month<12 else year+1 }}"><i class="fa fa-arrow-right"></i></a>
    </div>
  </div>

  <div class="table-responsive"><table class="table table-borderless">
    <thead><tr><th class="text-center">S</th><th class="text-center">M</th><th class="text-center">T</th><th class="text-center">W</th><th class="text-center">T</th><th class="text-center">F</th><th class="text-center">S</th></tr></thead>
    <tbody>
    {% for week in weeks %}
    <tr>
      {% for d in week %}
        {% if d %}
          {% set iso = d.isoformat() %}
          {% set rec = attendance.get(iso, {}) %}
          {% set today_flag = (d == today) %}
          {% set is_sunday = (d.weekday() == 6) %}
          <td class="p-0">
            <div class="day-cell{% if rec.get('status') == 'Present' %} present{% elif rec.get('status') == 'Absent' %} absent{% endif %}{% if is_sunday %} sunday{% endif %}{% if today_flag %} today-border{% endif %}" data-date="{{ iso }}">
              <div class="d-flex justify-content-between"><div class="day-num">{{ d.day }}</div><div class="status-pill">{{ rec.get('status','')[:1] if rec.get('status') else '' }}</div></div>
              <div style="position:absolute;bottom:8px;left:8px;right:8px;">
                <div class="ot-badge">{% if rec.get('ot_hours') %}OT: {{ rec.get('ot_hours') }}{% endif %}</div>
                <div class="shift-label">{% if rec.get('shift') and rec.get('shift') != 'GEN' %}{{ rec.get('shift') }}{% else %}GEN{% endif %}</div>
              </div>
            </div>
          </td>
        {% else %}<td class="p-0"></td>{% endif %}
      {% endfor %}
    </tr>{% endfor %}
    </tbody></table></div>

  <div class="summary-box" id="summaryBox">
    <div class="summary-top">
      🟢 <b>Present:</b> <span id="presentCount">{{ total_present }}</span> |
      🔴 <b>Absent:</b> <span id="absentCount">{{ total_absent }}</span> |
      🕒 <b>OT Hours:</b> <span id="otHoursTotal">{{ "%.1f"|format(total_ot_hours) }}</span>
    </div>
    <div class="summary-shifts" id="shiftLine">⚙️ <b>Shifts →</b><br>{{ shift_line|safe }}</div>
  </div>

  <!-- Modal -->
  <div class="modal fade" id="attModal" tabindex="-1">
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content p-3">
        <div class="modal-header border-0">
          <h5 class="modal-title">Add Attendance - <span id="modalDate"></span></h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">
          <div class="mb-3"><label class="form-label">Shift Type</label>
            <div class="d-flex gap-2 flex-wrap" id="shiftGroup">
              {% for s in ['GEN','FS','SS','NS','GEN2'] %}
              <button type="button" class="btn btn-outline-secondary btn-shift" data-shift="{{ s }}">{{ s }}</button>
              {% endfor %}
            </div>
          </div>
          <div class="mb-3"><label class="form-label">Attendance</label>
            <div class="d-flex gap-2">
              <button class="btn btn-outline-success flex-fill" id="markPresent">Present</button>
              <button class="btn btn-outline-danger flex-fill" id="markAbsent">Absent</button>
            </div>
          </div>
          <div class="mb-3"><label class="form-label">OT Hours</label>
            <input id="otHours" class="form-control" type="number" min="0" step="0.5" value="0">
          </div>
        </div>
        <div class="modal-footer border-0">
          <button class="btn btn-outline-warning" id="clearBtn">Clear</button>
          <button class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
          <button class="btn btn-primary" id="saveBtn">Save</button>
        </div>
      </div>
    </div>
  </div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
<script>
document.addEventListener('DOMContentLoaded', function(){
  const bsModal=new bootstrap.Modal(document.getElementById('attModal'));
  let currentDate=null,selectedShift='GEN',selectedStatus='Present';
  const shiftLine=document.getElementById('shiftLine');

  function setShiftActive(s){selectedShift=s;document.querySelectorAll('.btn-shift').forEach(b=>{const act=b.dataset.shift===s;b.classList.toggle('btn-primary',act);b.classList.toggle('btn-outline-secondary',!act);});}
  function setStatusActive(s){selectedStatus=s;const p=document.getElementById('markPresent'),a=document.getElementById('markAbsent');
    if(s==='Present'){p.classList.add('btn-success');p.classList.remove('btn-outline-success');a.classList.add('btn-outline-danger');a.classList.remove('btn-danger');}
    else{a.classList.add('btn-danger');a.classList.remove('btn-outline-danger');p.classList.add('btn-outline-success');p.classList.remove('btn-success');document.getElementById('otHours').value=0;}
  }

  document.querySelectorAll('.day-cell').forEach(el=>{
    el.addEventListener('click',async function(){
      currentDate=this.dataset.date;document.getElementById('modalDate').textContent=currentDate;
      setShiftActive('GEN');setStatusActive('Present');document.getElementById('otHours').value=0;
      try{
        const r=await fetch('/attendance/'+currentDate, { credentials: 'same-origin' });
        if(r.ok){
          const d=await r.json();
          if(d){ setShiftActive(d.shift||'GEN'); setStatusActive(d.status||'Present'); document.getElementById('otHours').value=d.ot_hours||0; }
        } else {
          // not logged in or error - redirect to login
          if(r.status === 401) window.location.href = '/login';
        }
      }catch(e){}
      bsModal.show();
    });
  });

  document.querySelectorAll('.btn-shift').forEach(b=>b.addEventListener('click',()=>setShiftActive(b.dataset.shift)));
  document.getElementById('markPresent').onclick=()=>setStatusActive('Present');
  document.getElementById('markAbsent').onclick=()=>setStatusActive('Absent');

  async function updateSummary(){
    const r=await fetch('/summary', { credentials: 'same-origin' });
    if(r.ok){
      const s=await r.json();
      document.getElementById('presentCount').textContent=s.present;
      document.getElementById('absentCount').textContent=s.absent;
      document.getElementById('otHoursTotal').textContent=s.ot_hours.toFixed(1);
      document.getElementById('shiftLine').innerHTML='⚙️ <b>Shifts →</b><br>'+ (s.shift_line_html || '');
    } else if (r.status === 401) {
      window.location.href = '/login';
    }
  }

  document.getElementById('saveBtn').onclick=async()=>{
    if(!currentDate)return;
    const otVal=selectedStatus==='Absent'?0:parseFloat(document.getElementById('otHours').value||0);
    const payload={date:currentDate,shift:selectedShift,status:selectedStatus,ot_hours:otVal};
    try{
      const res=await fetch('/attendance', { method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
      if(res.ok){
        bsModal.hide();
        const cell=document.querySelector('.day-cell[data-date="'+currentDate+'"]');
        if(cell){cell.classList.remove('present','absent');
          if(selectedStatus==='Present')cell.classList.add('present');
          else if(selectedStatus==='Absent')cell.classList.add('absent');
          cell.querySelector('.status-pill').textContent=selectedStatus[0];
          cell.querySelector('.ot-badge').textContent=otVal?('OT: '+otVal):'';
          cell.querySelector('.shift-label').textContent=selectedShift;}
        updateSummary();
      } else if (res.status === 401) {
        window.location.href = '/login';
      } else {
        const j = await res.json(); alert(j.error || 'Save failed');
      }
    }catch(e){alert('Save failed:'+e.message);}
  };

  document.getElementById('clearBtn').onclick=async()=>{
    if(!currentDate||!confirm('Clear attendance for '+currentDate+'?'))return;
    try{
      const r=await fetch('/attendance/'+currentDate, { method:'DELETE', credentials:'same-origin' });
      if(r.ok){
        bsModal.hide();
        const cell=document.querySelector('.day-cell[data-date="'+currentDate+'"]');
        if(cell){cell.classList.remove('present','absent');cell.querySelector('.status-pill').textContent='';cell.querySelector('.ot-badge').textContent='';cell.querySelector('.shift-label').textContent='GEN';}
        updateSummary();
      } else if (r.status === 401) {
        window.location.href = '/login';
      } else {
        const j = await r.json(); alert(j.error || 'Clear failed');
      }
    }catch(e){alert('Clear failed:'+e.message);}
  };
  setShiftActive('GEN');setStatusActive('Present');
});
</script></div></body></html>"""

# ---------- API endpoints ----------
@app.route("/api/register", methods=["POST"])
def api_register():
    payload = request.get_json(force=True)
    name = (payload.get("name") or "").strip()
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    ok, info = create_user(username, name, password)
    if not ok:
        if info == "username_taken":
            return jsonify({"error": "username_taken"}), 409
        return jsonify({"error": "invalid_data"}), 400
    return jsonify({"ok": True}), 201

@app.route("/api/login", methods=["POST"])
def api_login():
    payload = request.get_json(force=True)
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    user = authenticate_user(username, password)
    if not user:
        return jsonify({"error": "invalid_credentials"}), 401
    token = create_session_for_user(user["_id"])
    resp = jsonify({"ok": True})
    secure_flag = False
    if os.getenv("FLASK_ENV") == "production" or os.getenv("ENV") == "prod":
        secure_flag = True
    resp.set_cookie(SESSION_COOKIE_NAME, token, max_age=SESSION_COOKIE_MAX_AGE, httponly=True, samesite="Lax", secure=secure_flag, path="/")
    # also set a non-httponly display cookie for client UI
    resp.set_cookie("displayName", user["name"], max_age=SESSION_COOKIE_MAX_AGE, httponly=False, samesite="Lax", secure=secure_flag, path="/")
    return resp

@app.route("/api/logout")
def api_logout():
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        delete_session(token)
    resp = make_response(redirect("/login"))
    resp.delete_cookie(SESSION_COOKIE_NAME, path="/")
    resp.delete_cookie("displayName", path="/")
    return resp

# Attendance CRUD (uses session)
@app.route("/attendance/<day_iso>", methods=["GET"])
def api_get_attendance(day_iso):
    user, err = require_user_from_request()
    if err:
        return err
    rec = get_attendance_record(user["_id"], day_iso)
    return jsonify(rec or {}), 200

@app.route("/attendance/<day_iso>", methods=["DELETE"])
def api_delete_attendance(day_iso):
    user, err = require_user_from_request()
    if err:
        return err
    delete_attendance_record(user["_id"], day_iso)
    return jsonify({"ok": True})

@app.route("/attendance", methods=["POST"])
def api_save_attendance():
    user, err = require_user_from_request()
    if err:
        return err
    payload = request.get_json(force=True)
    day = payload.get("date")
    if not day:
        return jsonify({"error": "missing_date"}), 400
    shift = payload.get("shift")
    status = payload.get("status")
    try:
        ot = float(payload.get("ot_hours") or 0)
    except:
        ot = 0.0
    ok = upsert_attendance(user["_id"], day, shift, status, ot)
    if not ok:
        return jsonify({"error": "save_failed"}), 500
    return jsonify({"ok": True})

@app.route("/summary", methods=["GET"])
def api_summary():
    user, err = require_user_from_request()
    if err:
        return err
    today_dt = date.today()
    year = request.args.get("year", today_dt.year, type=int)
    month = request.args.get("month", today_dt.month, type=int)
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year
    start_date = date(prev_year, prev_month, 26)
    end_date = date(year, month, 25)
    s = summary_for_user_in_range(user["_id"], start_date.isoformat(), end_date.isoformat())
    s["shift_line_html"] = make_shift_line_html(s.get("shift_dates", {}))
    return jsonify(s)

# ---------- Pages ----------
@app.route("/login")
def page_login():
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token and get_user_by_session_token(token):
        return redirect("/")
    return render_template_string(LOGIN_HTML)

@app.route("/")
def page_index():
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user = get_user_by_session_token(token) if token else None
    if not user:
        return redirect("/login")

    today = date.today()
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)

    # compute cycle 26 prev -> 25 curr
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year

    start_date = date(prev_year, prev_month, 26)
    end_date = date(year, month, 25)
    raw_days = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
    first_weekday = raw_days[0].weekday()
    padding = (first_weekday + 1) % 7
    days = [None] * padding + raw_days
    weeks = [days[i:i+7] for i in range(0, len(days), 7)]

    # fetch attendance for this user in range
    attendance = get_attendance_for_user(user["_id"], start_date.isoformat(), end_date.isoformat())

    # summary counts & shift line
    total_present = total_absent = 0
    total_ot_hours = 0.0
    shift_dates = {}
    for iso, rec in attendance.items():
        try:
            d = datetime.fromisoformat(iso).date()
        except:
            continue
        st = rec.get("status")
        if st == "Present":
            total_present += 1
            try:
                total_ot_hours += float(rec.get("ot_hours") or 0)
            except:
                pass
        elif st == "Absent":
            total_absent += 1
        sh = (rec.get("shift") or "").strip()
        if st == "Present" and sh and sh != "GEN":
            shift_dates.setdefault(sh, []).append(d.day)

    shift_line = make_shift_line_html(shift_dates)
    total_ot_hours = round(total_ot_hours, 1)

    return render_template_string(MAIN_HTML,
                                  user_name=user["name"],
                                  year=year, month=month, weeks=weeks,
                                  attendance=attendance, calendar=__import__("calendar"),
                                  today=today,
                                  total_present=total_present, total_absent=total_absent,
                                  total_ot_hours=total_ot_hours, shift_line=shift_line)
