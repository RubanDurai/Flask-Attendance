from flask import Flask, render_template_string, request, redirect, jsonify, make_response, url_for
from datetime import date, datetime, timedelta
import calendar, json, time
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash
import jwt  # PyJWT
from functools import wraps

# ---------------------------
# CONFIG
# ---------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = "change-me-to-a-secure-random-string"  # <<-- change for production
JWT_ALGORITHM = "HS256"
JWT_EXP_SECONDS = 24 * 3600  # 24 hours
RESET_EXP_SECONDS = 15 * 60  # 15 minutes for password reset tokens
DATA_FILE = Path("/tmp/attendance.json")

# ---------------------------
# Storage (Vercel-safe) - initialize if missing
# File structure:
# { "alice": { "name": "...", "password": "<hash>", "is_admin": false,
#              "attendance": {...}, "reset_token": {"token": "...", "exp": 123456} } }
# ---------------------------
if not DATA_FILE.exists():
    DATA_FILE.write_text(json.dumps({}, indent=2))

def read_data():
    try:
        return json.loads(DATA_FILE.read_text())
    except Exception:
        return {}

def write_data(d):
    DATA_FILE.write_text(json.dumps(d, indent=2))

# ---------------------------
# Keep your helpers
# ---------------------------
def make_shift_line(shift_dates):
    ordered_shifts = ["FS", "SS", "NS", "GEN2"]
    shift_names = {"FS": "First Shift", "SS": "Second Shift", "NS": "Night Shift"}
    parts = []
    for s in ordered_shifts:
        if s in shift_dates and shift_dates[s]:
            label = shift_names.get(s, s)
            parts.append(f"{label}: {', '.join(str(x) for x in sorted(set(shift_dates[s])))}")
    for s in sorted(shift_dates.keys()):
        if s not in ordered_shifts and shift_dates[s]:
            label = shift_names.get(s, s)
            parts.append(f"{label}: {', '.join(str(x) for x in sorted(set(shift_dates[s])))}")
    return "<br>".join(parts)

# ---------------------------
# Templates (slightly adjusted to use server-set name + token auth)
# ---------------------------
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
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
.note{font-size:12px;color:#333;margin-top:8px}
</style>
</head>
<body>
<div class="container" role="main">
  <div class="tabs">
    <div id="tabLogin" class="tab active">Login</div>
    <div id="tabRegister" class="tab">Register</div>
  </div>

  <!-- LOGIN -->
  <form id="loginForm" autocomplete="off" onsubmit="return false;">
    <div class="input-group">
      <svg class="input-icon" viewBox="0 0 24 24" fill="#ffb400"><path d="M12 12c2.7 0 8 1.34 8 4v4H4v-4c0-2.66 5.3-4 8-4zM12 10a4 4 0 110-8 4 4 0 010 8z"/></svg>
      <input id="loginUser" placeholder="Username" />
    </div>
    <div class="input-group">
      <svg class="input-icon" viewBox="0 0 24 24" fill="#ffb400"><path d="M12 17a2 2 0 100-4 2 2 0 000 4zm6-6V8a6 6 0 10-12 0v3H4v10h16V11h-2z"/></svg>
      <input id="loginPass" type="password" placeholder="Password" />
    </div>
    <button class="btn" id="btnLogin" type="button">Login</button>
    <div class="center small">No account? <span class="link" id="toRegister">Register</span></div>
    <div class="center note"><a href="#" id="forgotLink">Forgot password?</a></div>
  </form>

  <!-- REGISTER -->
  <form id="registerForm" style="display:none" autocomplete="off" onsubmit="return false;">
    <div class="input-group">
      <svg class="input-icon" viewBox="0 0 24 24" fill="#ffb400"><path d="M12 12c2.7 0 8 1.34 8 4v4H4v-4c0-2.66 5.3-4 8-4zM12 10a4 4 0 110-8 4 4 0 010 8z"/></svg>
      <input id="regName" placeholder="Full Name" />
    </div>
    <div class="input-group">
      <svg class="input-icon" viewBox="0 0 24 24" fill="#ffb400"><path d="M12 12c2.7 0 8 1.34 8 4v4H4v-4c0-2.66 5.3-4 8-4zM12 10a4 4 0 110-8 4 4 0 010 8z"/></svg>
      <input id="regUser" placeholder="Choose username" />
    </div>
    <div class="input-group">
      <svg class="input-icon" viewBox="0 0 24 24" fill="#ffb400"><path d="M12 17a2 2 0 100-4 2 2 0 000 4zm6-6V8a6 6 0 10-12 0v3H4v10h16V11h-2z"/></svg>
      <input id="regPass" type="password" placeholder="Choose password" />
    </div>
    <div style="display:flex;gap:8px;align-items:center;margin-top:8px">
      <label style="font-size:13px"><input id="isAdmin" type="checkbox" /> Make admin</label>
    </div>
    <button class="btn" id="btnRegister" type="button">Sign Up</button>
    <div class="center small">Already registered? <span class="link" id="toLogin">Login</span></div>
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
  $('tabLogin').addEventListener('click', () => switchTab('login'));
  $('tabRegister').addEventListener('click', () => switchTab('register'));
  $('toRegister').addEventListener('click', (e) => { e.preventDefault(); switchTab('register'); });
  $('toLogin').addEventListener('click', (e) => { e.preventDefault(); switchTab('login'); });

  $('btnRegister').addEventListener('click', async () => {
    const name = $('regName').value.trim();
    const user = $('regUser').value.trim();
    const pass = $('regPass').value.trim();
    const admin = $('isAdmin').checked;
    if (!name || !user || !pass) { alert('Please fill all fields'); return; }
    try {
      const res = await fetch('/api/register', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,user,pass,admin})});
      const j = await res.json();
      if (!res.ok) { alert(j.error || 'Register failed'); return; }
      alert('Account created successfully! Please log in.');
      $('regName').value='';$('regUser').value='';$('regPass').value='';$('isAdmin').checked=false;
      switchTab('login');
    } catch(e){ alert('Register failed: '+e.message); }
  });

  $('btnLogin').addEventListener('click', async () => {
    const user = $('loginUser').value.trim();
    const pass = $('loginPass').value.trim();
    if (!user || !pass) { alert('Please enter credentials'); return; }
    try {
      const res = await fetch('/api/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user,pass})});
      const j = await res.json();
      if (!res.ok) { alert(j.error || 'Login failed'); return; }
      // server set HttpOnly token cookie; just redirect
      window.location = '/';
    } catch(e){ alert('Login failed: '+e.message); }
  });

  $('forgotLink').addEventListener('click', async (e) => {
    e.preventDefault();
    const user = prompt('Enter your username for password reset:');
    if (!user) return;
    try {
      const res = await fetch('/api/forgot', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user})});
      const j = await res.json();
      if (!res.ok) { alert(j.error || 'Failed'); return; }
      // NOTE: in production you'd email this link. Here we show it so you can test.
      alert('Password reset link (for testing):\\n' + j.reset_link);
    } catch(e){ alert('Failed: '+e.message); }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const regVisible = window.getComputedStyle($('registerForm')).display !== 'none';
      if (regVisible) $('btnRegister').click();
      else $('btnLogin').click();
    }
  });
});
</script>
</body>
</html>
"""

MAIN_HTML = """
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Self Attendance</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
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
    <div class="welcome">Hi, <span id="userName">{{ current_name }}</span></div>
    <div class="actions">
      {% if is_admin %}
        <a class="btn btn-sm btn-outline-primary" href="/admin">Admin</a>
      {% endif %}
      <a class="btn btn-sm btn-outline-secondary" href="/logout">Logout</a>
    </div>
  </div>

  <div class="calendar-card">
    <div class="month-nav shadow-sm">
      <a class="nav-btn" href="/?month={{ month-1 if month>1 else 12 }}&year={{ year if month>1 else year-1 }}"><i class="fa-solid fa-arrow-left"></i></a>
      <div class="month-label">{{ calendar.month_name[month] }} {{ year }}</div>
      <a class="nav-btn" href="/?month={{ month+1 if month<12 else 1 }}&year={{ year if month<12 else year+1 }}"><i class="fa-solid fa-arrow-right"></i></a>
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
      try{const r=await fetch('/attendance/'+currentDate);if(r.ok){const d=await r.json();if(d){setShiftActive(d.shift||'GEN');setStatusActive(d.status||'Present');document.getElementById('otHours').value=d.ot_hours||0;}}}catch(e){}
      bsModal.show();
    });
  });

  document.querySelectorAll('.btn-shift').forEach(b=>b.addEventListener('click',()=>setShiftActive(b.dataset.shift)));
  document.getElementById('markPresent').onclick=()=>setStatusActive('Present');
  document.getElementById('markAbsent').onclick=()=>setStatusActive('Absent');

  async function updateSummary(){
    const r=await fetch('/summary');
    if(r.ok){
      const s=await r.json();
      document.getElementById('presentCount').textContent=s.present;
      document.getElementById('absentCount').textContent=s.absent;
      document.getElementById('otHoursTotal').textContent=s.ot_hours.toFixed(1);
      shiftLine.innerHTML='⚙️ <b>Shifts →</b><br>'+s.shift_line;
    }
  }

  document.getElementById('saveBtn').onclick=async()=>{
    if(!currentDate)return;
    const otVal=selectedStatus==='Absent'?0:parseFloat(document.getElementById('otHours').value||0);
    const payload={date:currentDate,shift:selectedShift,status:selectedStatus,ot_hours:otVal};
    try{
      const res=await fetch('/attendance',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
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
      } else {
        const j = await res.json().catch(()=>({error:'Save failed'}));
        alert(j.error || 'Save failed');
      }
    }catch(e){alert('Save failed:'+e.message);}
  };

  document.getElementById('clearBtn').onclick=async()=>{
    if(!currentDate||!confirm('Clear attendance for '+currentDate+'?'))return;
    try{
      const r=await fetch('/attendance/'+currentDate,{method:'DELETE'});
      if(r.ok){
        bsModal.hide();
        const cell=document.querySelector('.day-cell[data-date="'+currentDate+'"]');
        if(cell){cell.classList.remove('present','absent');cell.querySelector('.status-pill').textContent='';cell.querySelector('.ot-badge').textContent='';cell.querySelector('.shift-label').textContent='GEN';}
        updateSummary();
      } else {
        const j = await r.json().catch(()=>({error:'Clear failed'}));
        alert(j.error || 'Clear failed');
      }
    }catch(e){alert('Clear failed:'+e.message);}
  };
  setShiftActive('GEN');setStatusActive('Present');
});
</script></div></body></html>
"""

# ---------------------------
# ADMIN HTML (simple)
# ---------------------------
ADMIN_HTML = """
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin Dashboard</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
</head><body class="p-3">
<div class="container">
  <h3>Admin Dashboard</h3>
  <p>Signed in as <b>{{ current_name }}</b> (<a href="/">Back to app</a>)</p>
  <div id="usersWrap"></div>
</div>
<script>
async function loadUsers(){
  const r = await fetch('/api/admin/users');
  if (!r.ok) { document.getElementById('usersWrap').innerText='Failed to load users'; return; }
  const j = await r.json();
  const wrap = document.getElementById('usersWrap');
  wrap.innerHTML = '';
  const tbl = document.createElement('table'); tbl.className='table';
  const thead = document.createElement('thead'); thead.innerHTML='<tr><th>User</th><th>Name</th><th>Admin</th><th>Actions</th></tr>';
  tbl.appendChild(thead);
  const tbody = document.createElement('tbody');
  for (const u of j.users){
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${u.username}</td><td>${u.name||''}</td><td>${u.is_admin? 'Yes':'No'}</td>
      <td>
        <button class="btn btn-sm btn-primary" onclick="view('${u.username}')">View</button>
        <button class="btn btn-sm btn-danger" onclick="del('${u.username}')">Delete</button>
      </td>`;
    tbody.appendChild(tr);
  }
  tbl.appendChild(tbody);
  wrap.appendChild(tbl);
}

async function view(username){
  const r = await fetch('/api/admin/user/' + encodeURIComponent(username));
  if (!r.ok){ alert('Failed'); return; }
  const j = await r.json();
  alert(JSON.stringify(j, null, 2));
}

async function del(username){
  if (!confirm('Delete user '+username+' ?')) return;
  const r = await fetch('/api/admin/user/' + encodeURIComponent(username), {method:'DELETE'});
  if (r.ok) { alert('Deleted'); loadUsers(); }
  else { const j = await r.json().catch(()=>({error:'failed'})); alert(j.error||'Failed'); }
}

loadUsers();
</script>
</body></html>
"""

# ---------------------------
# Utilities: JWT helpers and auth decorator
# ---------------------------
def create_jwt(payload, exp_seconds=JWT_EXP_SECONDS):
    payload = payload.copy()
    payload["exp"] = int(time.time()) + int(exp_seconds)
    return jwt.encode(payload, app.config["SECRET_KEY"], algorithm=JWT_ALGORITHM)

def decode_jwt(token):
    try:
        return jwt.decode(token, app.config["SECRET_KEY"], algorithms=[JWT_ALGORITHM])
    except Exception:
        return None

def get_token_from_request():
    # Try cookie first, then Authorization header
    token = request.cookies.get("token")
    if token:
        return token
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.split(" ", 1)[1]
    return None

def require_auth(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        token = get_token_from_request()
        if not token:
            return jsonify({"error":"Authentication required"}), 401
        payload = decode_jwt(token)
        if not payload:
            return jsonify({"error":"Invalid or expired token"}), 401
        username = payload.get("sub")
        data = read_data()
        if username not in data:
            return jsonify({"error":"Invalid session"}), 401
        # attach user info to request context via kwargs
        kwargs["_auth_user"] = username
        kwargs["_auth_payload"] = payload
        return f(*args, **kwargs)
    return wrapped

def require_admin(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        token = get_token_from_request()
        if not token:
            return jsonify({"error":"Authentication required"}), 401
        payload = decode_jwt(token)
        if not payload:
            return jsonify({"error":"Invalid or expired token"}), 401
        username = payload.get("sub")
        data = read_data()
        user = data.get(username)
        if not user or not user.get("is_admin"):
            return jsonify({"error":"Admin access required"}), 403
        kwargs["_auth_user"] = username
        kwargs["_auth_payload"] = payload
        return f(*args, **kwargs)
    return wrapped

# ---------------------------
# ROUTES: Login/Register pages and APIs
# ---------------------------
@app.route("/login")
def login_page():
    # if logged in, redirect to app
    token = get_token_from_request()
    if token and decode_jwt(token):
        return redirect("/")
    return render_template_string(LOGIN_HTML)

@app.route("/logout")
def logout():
    resp = make_response(redirect("/login"))
    resp.delete_cookie("token")
    return resp

@app.route("/api/register", methods=["POST"])
def api_register():
    payload = request.get_json(force=True)
    name = (payload.get("name") or "").strip()
    user = (payload.get("user") or "").strip()
    passwd = (payload.get("pass") or "").strip()
    is_admin = bool(payload.get("admin"))
    if not (name and user and passwd):
        return jsonify({"error":"Missing fields"}), 400
    data = read_data()
    if user in data:
        return jsonify({"error":"Username already exists"}), 409
    # create user
    data[user] = {
        "name": name,
        "password": generate_password_hash(passwd),
        "is_admin": bool(is_admin),
        "attendance": {}
    }
    write_data(data)
    return jsonify({"ok": True})

@app.route("/api/login", methods=["POST"])
def api_login():
    payload = request.get_json(force=True)
    user = (payload.get("user") or "").strip()
    passwd = (payload.get("pass") or "").strip()
    if not (user and passwd):
        return jsonify({"error":"Missing credentials"}), 400
    data = read_data()
    user_obj = data.get(user)
    if not user_obj:
        return jsonify({"error":"Invalid username or password"}), 401
    stored = user_obj.get("password")
    if not stored or not check_password_hash(stored, passwd):
        return jsonify({"error":"Invalid username or password"}), 401
    # create JWT
    token = create_jwt({"sub": user})
    resp = make_response(jsonify({"ok": True, "name": user_obj.get("name","")}))
    # set HttpOnly cookie
    resp.set_cookie("token", token, httponly=True, samesite='Lax', max_age=JWT_EXP_SECONDS)
    return resp

# ---------------------------
# Forgot password (create reset token) + reset view
# ---------------------------
@app.route("/api/forgot", methods=["POST"])
def api_forgot():
    payload = request.get_json(force=True)
    user = (payload.get("user") or "").strip()
    if not user:
        return jsonify({"error":"Missing username"}), 400
    data = read_data()
    if user not in data:
        return jsonify({"error":"User not found"}), 404
    # create a short-lived reset token (JWT with purpose 'reset' and username)
    reset_token = create_jwt({"sub": user, "purpose": "reset"}, exp_seconds=RESET_EXP_SECONDS)
    # store token info optionally
    data[user]["reset_token"] = {"token": reset_token, "exp": int(time.time()) + RESET_EXP_SECONDS}
    write_data(data)
    # In production: email the reset link. Here we return the link for testing.
    reset_link = request.url_root.rstrip("/") + url_for("reset_password_page", token=reset_token)
    return jsonify({"ok": True, "reset_link": reset_link})

@app.route("/reset/<token>", methods=["GET","POST"])
def reset_password_page(token):
    # GET: show form; POST: set new password
    payload = decode_jwt(token)
    if not payload or payload.get("purpose") != "reset":
        return "Invalid or expired reset token", 400
    username = payload.get("sub")
    if request.method == "GET":
        return f"""
        <!doctype html><html><body>
        <h3>Reset password for {username}</h3>
        <form method="POST">
          <label>New password: <input name="password" type="password"/></label><br/><br/>
          <button type="submit">Set password</button>
        </form>
        </body></html>
        """
    # POST
    newpw = (request.form.get("password") or "").strip()
    if not newpw:
        return "Missing password", 400
    data = read_data()
    user_obj = data.get(username)
    if not user_obj:
        return "User not found", 404
    # optional: check stored reset token matches token (prevents reuse if overwritten)
    stored = user_obj.get("reset_token", {}).get("token")
    if stored and stored != token:
        return "Reset token mismatch", 400
    user_obj["password"] = generate_password_hash(newpw)
    # delete reset token
    user_obj.pop("reset_token", None)
    write_data(data)
    return f"Password updated for {username}. You may now <a href='/login'>login</a>."

# ---------------------------
# Main attendance page (requires JWT auth)
# ---------------------------
@app.route("/")
@require_auth
def index(_auth_user=None, _auth_payload=None):
    current_user = _auth_user
    data = read_data()
    user_obj = data.get(current_user, {"name": "", "attendance": {}})
    attend = user_obj.get("attendance", {})

    today = date.today()
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)

    # Attendance cycle: 26 prev month → 25 current
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
    weeks = [days[i:i + 7] for i in range(0, len(days), 7)]

    # Prepare summary
    total_present = total_absent = 0
    total_ot_hours = 0.0
    shift_dates = {}

    for iso, rec in attend.items():
        try:
            d = datetime.fromisoformat(iso).date()
        except Exception:
            continue
        if not (start_date <= d <= end_date):
            continue
        status = rec.get("status", "")
        if status == "Present":
            total_present += 1
            try:
                total_ot_hours += float(rec.get("ot_hours", 0) or 0)
            except Exception:
                pass
        elif status == "Absent":
            total_absent += 1

        sh = (rec.get("shift") or "").strip()
        if status == "Present" and sh and sh != "GEN":
            shift_dates.setdefault(sh, []).append(d.day)

    shift_line = make_shift_line(shift_dates)
    total_ot_hours = round(total_ot_hours, 1)

    return render_template_string(MAIN_HTML, year=year, month=month, weeks=weeks,
        attendance=attend, calendar=calendar, today=today,
        total_present=total_present, total_absent=total_absent,
        total_ot_hours=total_ot_hours, shift_line=shift_line,
        current_name=user_obj.get("name",""), is_admin=user_obj.get("is_admin", False))

# ---------------------------
# API endpoints for attendance (per-user)
# ---------------------------
@app.route("/attendance/<day_iso>")
@require_auth
def get_attendance(day_iso, _auth_user=None, _auth_payload=None):
    user = _auth_user
    data = read_data()
    rec = data.get(user, {}).get("attendance", {}).get(day_iso, {})
    return jsonify(rec)

@app.route("/attendance/<day_iso>", methods=["DELETE"])
@require_auth
def delete_attendance(day_iso, _auth_user=None, _auth_payload=None):
    user = _auth_user
    data = read_data()
    user_obj = data.get(user)
    if not user_obj:
        return jsonify({"error":"Not found"}), 404
    daymap = user_obj.setdefault("attendance", {})
    if day_iso in daymap:
        del daymap[day_iso]
        write_data(data)
        return jsonify({"ok": True})
    return jsonify({"error":"Not found"}), 404

@app.route("/attendance", methods=["POST"])
@require_auth
def save_attendance(_auth_user=None, _auth_payload=None):
    user = _auth_user
    rec = request.get_json(force=True)
    day = rec.get("date")
    if not day:
        return jsonify({"error":"Missing date"}), 400
    data = read_data()
    user_obj = data.setdefault(user, {"name": request.args.get("name",""), "password": generate_password_hash("temp"), "is_admin": False, "attendance": {}})
    user_obj.setdefault("attendance", {})
    try:
        ot = float(rec.get("ot_hours", 0) or 0)
    except:
        ot = 0.0
    user_obj["attendance"][day] = {"shift": rec.get("shift"), "status": rec.get("status"), "ot_hours": ot}
    write_data(data)
    return jsonify({"ok": True})

@app.route("/summary")
@require_auth
def summary(_auth_user=None, _auth_payload=None):
    user = _auth_user
    data = read_data()
    user_obj = data.get(user, {})
    records = user_obj.get("attendance", {})
    present = absent = 0
    ot_hours = 0.0
    shift_dates = {}
    for day, rec in records.items():
        st = rec.get("status")
        if st == "Present":
            present += 1
            try:
                ot_hours += float(rec.get("ot_hours") or 0)
            except:
                pass
        elif st == "Absent":
            absent += 1
        sh = (rec.get("shift") or "").strip()
        if st == "Present" and sh and sh != "GEN":
            try:
                d = datetime.fromisoformat(day).day
                shift_dates.setdefault(sh, []).append(d)
            except:
                pass
    shift_line = make_shift_line(shift_dates)
    return jsonify({"present": present, "absent": absent, "ot_hours": round(ot_hours, 1), "shift_line": shift_line})

# ---------------------------
# Admin dashboard & APIs
# ---------------------------
@app.route("/admin")
@require_admin
def admin_page(_auth_user=None, _auth_payload=None):
    data = read_data()
    name = data.get(_auth_user, {}).get("name", "")
    return render_template_string(ADMIN_HTML, current_name=name)

@app.route("/api/admin/users")
@require_admin
def api_admin_users(_auth_user=None, _auth_payload=None):
    data = read_data()
    users = []
    for uname, obj in data.items():
        users.append({"username": uname, "name": obj.get("name"), "is_admin": bool(obj.get("is_admin", False))})
    return jsonify({"users": users})

@app.route("/api/admin/user/<username>")
@require_admin
def api_admin_user(username, _auth_user=None, _auth_payload=None):
    data = read_data()
    obj = data.get(username)
    if not obj:
        return jsonify({"error":"Not found"}), 404
    # don't return password hash
    out = {k:v for k,v in obj.items() if k != "password"}
    return jsonify(out)

@app.route("/api/admin/user/<username>", methods=["DELETE"])
@require_admin
def api_admin_delete(username, _auth_user=None, _auth_payload=None):
    data = read_data()
    if username in data:
        del data[username]
        write_data(data)
        return jsonify({"ok": True})
    return jsonify({"error":"Not found"}), 404

# ---------------------------
# Run
# ---------------------------
if __name__ == "__main__":
    # convenience: create an initial admin if none exists
    d = read_data()
    if "admin" not in d:
        d["admin"] = {
            "name": "Administrator",
            "password": generate_password_hash("admin123"),
            "is_admin": True,
            "attendance": {}
        }
        write_data(d)
        print("Created default admin / admin123 (change password immediately)")
    app.run(debug=True)
