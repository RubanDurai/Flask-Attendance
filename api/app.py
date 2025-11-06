from flask import Flask, render_template_string, request, jsonify
from datetime import date, datetime, timedelta
import calendar, json
from pathlib import Path

app = Flask(__name__)
DATA_FILE = Path(__file__).resolve().parent.parent / "attendance.json"

# Ensure file exists
if not DATA_FILE.exists():
    DATA_FILE.write_text(json.dumps({}, indent=2))


def read_data():
    try:
        return json.loads(DATA_FILE.read_text())
    except Exception:
        return {}


def write_data(d):
    DATA_FILE.write_text(json.dumps(d, indent=2))


@app.route("/")
def index():
    today = date.today()
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)
    data = read_data()

    # Attendance cycle: 26 of previous month → 25 of selected month
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year

    start_date = date(prev_year, prev_month, 26)
    end_date = date(year, month, 25)

    # Generate date range
    raw_days = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]

    # Align to Sunday-starting week
    first_weekday = raw_days[0].weekday()  # Monday=0
    padding = (first_weekday + 1) % 7  # Sunday=0
    days = [None] * padding + raw_days
    weeks = [days[i:i + 7] for i in range(0, len(days), 7)]

    # Summary calculations
    total_present = total_absent = 0
    total_ot_hours = 0.0
    shift_dates = {}

    for iso, rec in data.items():
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

    for k in list(shift_dates.keys()):
        shift_dates[k] = sorted(set(shift_dates[k]))

    ordered_shifts = ["FS", "SS", "NS", "GEN2"]
    shift_names = {
        "FS": "First Shift",
        "SS": "Second Shift",
        "NS": "Night Shift",
    }

    shift_line_parts = []
    for s in ordered_shifts:
        if s in shift_dates and shift_dates[s]:
            label = shift_names.get(s, s)
            shift_line_parts.append(f"{label}: {', '.join(str(x) for x in shift_dates[s])}")

    for s in sorted(shift_dates.keys()):
        if s not in ordered_shifts and shift_dates[s]:
            label = shift_names.get(s, s)
            shift_line_parts.append(f"{label}: {', '.join(str(x) for x in shift_dates[s])}")

    shift_line = "<br>".join(shift_line_parts)
    total_ot_hours = round(total_ot_hours, 1)

    # Render HTML (same UI)
    html = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Self Attendance</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
body {
  background: #fafbff;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial;
  margin: 0; padding: 0;
}
.calendar-card {
  position: relative; width: 100%; max-width: 739px; height: 314px;
  margin: 0 auto; overflow: hidden;
  background: url("http://176.9.41.10:8080/dl/690a8f9cac442ce7a2ee3114") no-repeat left center;
  background-size: cover; border-radius: 15px;
  box-shadow: 0 6px 20px rgba(20, 20, 30, 0.06);
  display: flex; justify-content: center; align-items: flex-start;
}
.month-nav {
  position: absolute; top: 220px; left: 50%; transform: translateX(-50%);
  display: flex; align-items: center; justify-content: space-between;
  background: rgba(255,255,255,0.25); backdrop-filter: blur(12px);
  border-radius: 14px; padding: 8px 20px; width: 180px;
  box-shadow: 0 3px 8px rgba(0,0,0,0.05);
  border: 1px solid rgba(255,255,255,0.4);
}
.nav-btn { color: #333; font-size: 18px; text-decoration: none; transition: 0.2s; }
.nav-btn:hover { color: #000; transform: scale(1.1); }
.month-label { font-weight: 700; font-size: 18px; color: #111; }
@media (max-width: 768px) {
  .calendar-card { height: 140px; background-size: contain; background-position: center top; background-repeat: no-repeat; padding-bottom: 0; }
  .month-nav { top: auto; bottom: 20px; width: 60%; max-width: 300px; padding: 10px 20px; }
  .month-label { font-size: 16px; } .nav-btn { font-size: 16px; }
}
.day-cell{height:92px;border:1px solid #f0f2f7;cursor:pointer;position:relative;padding:8px;background:white;transition:transform .06s ease;}
.day-cell:active{transform:scale(.997);}
.day-num{font-weight:700;font-size:14px;}
.present{background:#e9fbe9;}
.absent{background:#fff0f0;}
.sunday{background:#f2f2f2;color:#6b7280;}
.shift-label{font-size:12px;color:#6b7280;display:block;margin:0px 2px;}
.status-pill{position:absolute;right:10px;top:18px;padding:4px 0;border-radius:999px;font-size:12px;background:white;border:1px solid #e6e9ef;min-width:20px;text-align:center;}
.status-pill:empty{display:none;}
.ot-badge{font-size:10px;color:#374151;margin-top:0;display:block;}
.today-border{outline:3px solid rgba(59,130,246,0.18);border-radius:8px;}
.summary-box{margin-top:18px;padding:10px;border-radius:10px;background:#f7f7f7;border:1px solid #eee;display:flex;flex-direction:column;gap:6px;}
.summary-top{font-weight:700;}
.summary-shifts{font-weight:600;color:#374151;}
</style></head><body>
<div class="calendar-card">
  <div class="month-nav shadow-sm">
    <a class="nav-btn" href="/?month={{ month-1 if month>1 else 12 }}&year={{ year if month>1 else year-1 }}"><i class="fa-solid fa-arrow-left"></i></a>
    <div class="month-label">{{ calendar.month_name[month] }} {{ year }}</div>
    <a class="nav-btn" href="/?month={{ month+1 if month<12 else 1 }}&year={{ year if month<12 else year+1 }}"><i class="fa-solid fa-arrow-right"></i></a>
  </div>
</div>

<div class="table-responsive mt-4">
<table class="table table-borderless">
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
    <div class="d-flex justify-content-between"><div class="day-num">{{ d.day }}</div>
    <div class="status-pill">{{ rec.get('status','')[:1] if rec.get('status') else '' }}</div></div>
    <div style="position:absolute;bottom:8px;left:8px;right:8px;">
      <div class="ot-badge">{% if rec.get('ot_hours') %}OT: {{ rec.get('ot_hours') }}{% endif %}</div>
      <div class="shift-label">{% if rec.get('shift') and rec.get('shift') != 'GEN' %}{{ rec.get('shift') }}{% else %}GEN{% endif %}</div>
    </div>
  </div>
</td>
{% else %}<td class="p-0"></td>{% endif %}
{% endfor %}
</tr>
{% endfor %}
</tbody></table></div>

<div class="summary-box">
  <div class="summary-top">
    🟢 <b>Present:</b> {{ total_present }}  
    🔴 <b>Absent:</b> {{ total_absent }}  
    🕒 <b>OT Hours:</b> {{ "%.1f"|format(total_ot_hours) }}
  </div>
  {% if shift_line %}
  <div class="summary-shifts">⚙️ <b>Shifts →</b><br>{{ shift_line|safe }}</div>
  {% endif %}
</div>
</body></html>
"""

    return render_template_string(
        html,
        year=year, month=month, weeks=weeks,
        attendance=data, calendar=calendar, today=today,
        total_present=total_present, total_absent=total_absent,
        total_ot_hours=total_ot_hours, shift_line=shift_line
    )


@app.route("/attendance/<day_iso>")
def get_attendance(day_iso):
    data = read_data()
    return jsonify(data.get(day_iso, {}))


@app.route("/attendance/<day_iso>", methods=["DELETE"])
def delete_attendance(day_iso):
    data = read_data()
    if day_iso in data:
        del data[day_iso]
        write_data(data)
        return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404


@app.route("/attendance", methods=["POST"])
def save_attendance():
    payload = request.get_json()
    if not payload or "date" not in payload:
        return jsonify({"error": "Invalid input"}), 400
    day = payload["date"]
    try:
        datetime.fromisoformat(day)
    except Exception:
        return jsonify({"error": "Invalid date"}), 400
    try:
        ot = float(payload.get("ot_hours", 0) or 0)
    except Exception:
        ot = 0.0
    if payload.get("status") == "Absent":
        ot = 0.0
    data = read_data()
    data[day] = {
        "shift": payload.get("shift", "GEN"),
        "status": payload.get("status", "Present"),
        "ot_hours": ot,
        "updated_at": datetime.utcnow().isoformat() + "Z"
    }
    write_data(data)
    return jsonify({"ok": True})
