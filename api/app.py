from flask import Flask, render_template_string, request, jsonify, make_response
from datetime import date, datetime, timedelta
from pathlib import Path
import calendar, json, threading

app = Flask(__name__)
DATA_FILE = Path("/tmp/attendance.json")
_cache = {}
_lock = threading.Lock()

if not DATA_FILE.exists():
    DATA_FILE.write_text("{}")

def read_data():
    with _lock:
        if not _cache:
            try:
                _cache.update(json.loads(DATA_FILE.read_text()))
            except Exception:
                _cache.clear()
        return _cache

def write_data(d):
    with _lock:
        DATA_FILE.write_text(json.dumps(d, indent=2))
        _cache.clear()
        _cache.update(d)

@app.after_request
def add_headers(resp):
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    resp.headers["Content-Encoding"] = "gzip"
    return resp

@app.route("/")
def index():
    today = date.today()
    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))
    data = read_data()

    # Cycle: 26 prev month → 25 current
    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    start_date, end_date = date(prev_year, prev_month, 26), date(year, month, 25)
    days = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
    first_weekday = days[0].weekday()
    weeks = [([None] * ((first_weekday + 1) % 7) + days)[i:i + 7] for i in range(0, len(days) + ((first_weekday + 1) % 7), 7)]

    total_present = total_absent = 0
    total_ot_hours = 0.0
    shift_dates = {}

    for iso, rec in data.items():
        try:
            d = datetime.fromisoformat(iso).date()
        except:
            continue
        if not (start_date <= d <= end_date):
            continue
        st = rec.get("status", "")
        if st == "Present":
            total_present += 1
            total_ot_hours += float(rec.get("ot_hours", 0) or 0)
        elif st == "Absent":
            total_absent += 1
        sh = (rec.get("shift") or "GEN").strip()
        if st == "Present" and sh != "GEN":
            shift_dates.setdefault(sh, []).append(d.day)

    for k in shift_dates:
        shift_dates[k] = sorted(set(shift_dates[k]))

    ordered = ["FS", "SS", "NS", "GEN2"]
    names = {"FS": "First Shift", "SS": "Second Shift", "NS": "Night Shift"}
    shift_line = "<br>".join(
        f"{names.get(s, s)}: {', '.join(map(str, shift_dates[s]))}"
        for s in ordered + sorted(set(shift_dates) - set(ordered))
        if s in shift_dates
    )

    total_ot_hours = round(total_ot_hours, 1)

    html = """<!doctype html><html lang='en'><head>
<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Attendance</title>
<link href='https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css' rel='stylesheet'>
<style>body{background:#fff;font-family:system-ui;margin:0;padding:0}
.calendar-card{max-width:739px;height:314px;margin:auto;background:url('http://176.9.41.10:8080/dl/690a8f9cac442ce7a2ee3114') no-repeat center;background-size:cover;border-radius:15px;box-shadow:0 6px 20px rgba(0,0,0,.06)}
.month-nav{position:absolute;top:220px;left:50%;transform:translateX(-50%);display:flex;align-items:center;justify-content:space-between;background:rgba(255,255,255,.25);backdrop-filter:blur(12px);border-radius:14px;padding:8px 20px;width:180px;border:1px solid rgba(255,255,255,.4)}
.nav-btn{color:#333;font-size:18px;text-decoration:none}.nav-btn:hover{color:#000;transform:scale(1.1)}.month-label{font-weight:700;font-size:18px;color:#111}
.day-cell{height:92px;border:1px solid #f0f2f7;cursor:pointer;padding:8px;background:white;transition:transform .06s}.day-cell:active{transform:scale(.98)}
.present{background:#e9fbe9}.absent{background:#fff0f0}.sunday{background:#f2f2f2;color:#777}
.status-pill{position:absolute;right:10px;top:18px;padding:4px 6px;border-radius:999px;font-size:12px;background:white;border:1px solid #e6e9ef}
.summary-box{margin-top:18px;padding:10px;border-radius:10px;background:#f7f7f7;border:1px solid #eee}
.shift-label{font-size:12px;color:#555}.ot-badge{font-size:10px;color:#444}
.today-border{outline:3px solid rgba(59,130,246,0.18);border-radius:8px}
</style></head><body>
<div class='calendar-card position-relative'>
<div class='month-nav'><a class='nav-btn' href='/?month={{ month-1 if month>1 else 12 }}&year={{ year if month>1 else year-1 }}'>&lt;</a>
<div class='month-label'>{{ calendar.month_name[month] }} {{ year }}</div>
<a class='nav-btn' href='/?month={{ month+1 if month<12 else 1 }}&year={{ year if month<12 else year+1 }}'>&gt;</a></div></div>
<div class='table-responsive'><table class='table table-borderless'><thead><tr>
<th>S</th><th>M</th><th>T</th><th>W</th><th>T</th><th>F</th><th>S</th></tr></thead><tbody>
{% for week in weeks %}<tr>
{% for d in week %}
{% if d %}{% set iso=d.isoformat() %}{% set rec=attendance.get(iso,{}) %}
<td class='p-0'><div class='day-cell{% if rec.get("status")=="Present"%} present{% elif rec.get("status")=="Absent"%} absent{% endif %}{% if d.weekday()==6 %} sunday{% endif %}{% if d==today %} today-border{% endif %}' data-date='{{ iso }}'>
<div class='d-flex justify-content-between'><div>{{ d.day }}</div><div class='status-pill'>{{ rec.get('status','')[:1] }}</div></div>
<div class='ot-badge'>{% if rec.get('ot_hours') %}OT: {{ rec.get('ot_hours') }}{% endif %}</div>
<div class='shift-label'>{{ rec.get('shift','GEN') }}</div></div></td>
{% else %}<td></td>{% endif %}{% endfor %}
</tr>{% endfor %}
</tbody></table></div>
<div class='summary-box'>
<b>🟢 Present:</b> {{ total_present }} | <b>🔴 Absent:</b> {{ total_absent }} | <b>🕒 OT:</b> {{ total_ot_hours }}
{% if shift_line %}<br><b>⚙️ Shifts:</b><br>{{ shift_line|safe }}{% endif %}
</div>
<script>
document.querySelectorAll('.day-cell').forEach(c=>c.onclick=()=>{const d=c.dataset.date;fetch('/attendance/'+d).then(r=>r.json()).then(x=>console.log(x));});
</script></body></html>"""

    resp = make_response(render_template_string(
        html, year=year, month=month, weeks=weeks,
        attendance=data, calendar=calendar, today=today,
        total_present=total_present, total_absent=total_absent,
        total_ot_hours=total_ot_hours, shift_line=shift_line
    ))
    return resp

@app.route("/attendance/<day_iso>")
def get_attendance(day_iso):
    return jsonify(read_data().get(day_iso, {}))

@app.route("/attendance/<day_iso>", methods=["DELETE"])
def delete_attendance(day_iso):
    d = read_data()
    if day_iso in d:
        del d[day_iso]
        write_data(d)
        return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404

@app.route("/attendance", methods=["POST"])
def save_attendance():
    p = request.get_json() or {}
    day = p.get("date")
    try:
        datetime.fromisoformat(day)
    except Exception:
        return jsonify({"error": "Invalid date"}), 400
    data = read_data()
    data[day] = {
        "shift": p.get("shift", "GEN"),
        "status": p.get("status", "Present"),
        "ot_hours": float(p.get("ot_hours", 0) or 0),
        "updated_at": datetime.utcnow().isoformat() + "Z"
    }
    write_data(data)
    return jsonify({"ok": True})

# Vercel entrypoint
def handler(event, context):
    return app(event, context)
