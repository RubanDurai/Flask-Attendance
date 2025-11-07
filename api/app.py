from flask import Flask, render_template_string, request, jsonify
from datetime import date, datetime, timedelta
import calendar, json
from pathlib import Path

app = Flask(__name__)

DATA_FILE = Path("/tmp/attendance.json")  # ✅ Vercel-safe temporary storage
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

    # Attendance cycle: 26 previous month → 25 current
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

    shift_line = make_shift_line(shift_dates)
    total_ot_hours = round(total_ot_hours, 1)

    # ---------------- HTML Template ----------------
    html = """
    <!doctype html><html lang="en"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Self Attendance</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
    body{background:#fafbff;font-family:system-ui;margin:0;padding:0;}
    .calendar-card{position:relative;width:100%;max-width:739px;height:314px;margin:0 auto;
      background:url('http://176.9.41.10:8080/dl/690a8f9cac442ce7a2ee3114') no-repeat center;background-size:cover;
      border-radius:15px;box-shadow:0 6px 20px rgba(20,20,30,.06);display:flex;justify-content:center;align-items:flex-start;}
    .month-nav{position:absolute;top:220px;left:50%;transform:translateX(-50%);display:flex;align-items:center;
      justify-content:space-between;background:rgba(255,255,255,0.25);backdrop-filter:blur(12px);border-radius:14px;
      padding:8px 20px;width:180px;box-shadow:0 3px 8px rgba(0,0,0,0.05);border:1px solid rgba(255,255,255,0.4);}
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
    </style></head><body>

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
          }
        }catch(e){alert('Clear failed:'+e.message);}
      };
      setShiftActive('GEN');setStatusActive('Present');
    });
    </script></body></html>
    """

    return render_template_string(html, year=year, month=month, weeks=weeks,
        attendance=data, calendar=calendar, today=today,
        total_present=total_present, total_absent=total_absent,
        total_ot_hours=total_ot_hours, shift_line=shift_line)

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

@app.route("/attendance/<day_iso>")
def get_attendance(day_iso):
    return jsonify(read_data().get(day_iso, {}))

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
    rec = request.get_json(force=True)
    day = rec.get("date")
    if not day:
        return jsonify({"error": "Missing date"}), 400
    data = read_data()
    data[day] = {"shift": rec.get("shift"), "status": rec.get("status"), "ot_hours": rec.get("ot_hours", 0)}
    write_data(data)
    return jsonify({"ok": True})

@app.route("/summary")
def summary():
    data = read_data()
    present = absent = 0
    ot_hours = 0.0
    shift_dates = {}
    for day, rec in data.items():
        st = rec.get("status")
        if st == "Present":
            present += 1
            try:
                ot_hours += float(rec.get("ot_hours") or 0)
            except Exception:
                pass
        elif st == "Absent":
            absent += 1
        sh = (rec.get("shift") or "").strip()
        if st == "Present" and sh and sh != "GEN":
            try:
                d = datetime.fromisoformat(day).day
                shift_dates.setdefault(sh, []).append(d)
            except Exception:
                pass
    shift_line = make_shift_line(shift_dates)
    return jsonify({"present": present, "absent": absent, "ot_hours": round(ot_hours, 1), "shift_line": shift_line})
