from flask import Flask, render_template, request, jsonify, send_file
from datetime import datetime, date, timedelta
import os
import re
import calendar
import psycopg2
import psycopg2.extras
import psycopg2.pool
import threading
from image_gen import generate_attendance_image, generate_team_image
import io

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

# ── DB 연결 풀 ────────────────────────────────────────────────
# 요청마다 새로 연결하면 SSL 핸드셰이크 비용(100~300ms)이 매번 발생하므로 풀을 사용
_pool = None
_pool_lock = threading.Lock()

def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    1, 5, DATABASE_URL, sslmode="require"
                )
    return _pool

def get_db():
    """풀에서 연결을 가져옴. 끊긴 연결이면 버리고 새로 확보."""
    pool = _get_pool()
    for _ in range(3):
        conn = pool.getconn()
        try:
            if conn.closed:
                raise psycopg2.OperationalError("closed connection")
            # 유휴 중 서버가 끊었는지 확인 (비용이 거의 없는 쿼리)
            with conn.cursor() as c:
                c.execute("SELECT 1")
            conn.rollback()
            return conn
        except Exception:
            try:
                pool.putconn(conn, close=True)
            except Exception:
                pass
    # 풀이 계속 실패하면 직접 연결로 폴백
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def release_db(conn):
    """연결을 풀에 반납 (닫지 않음)."""
    if conn is None:
        return
    try:
        conn.rollback()   # 미완료 트랜잭션 정리
    except Exception:
        pass
    try:
        _get_pool().putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
                CREATE TABLE IF NOT EXISTS members (
                                                       id SERIAL PRIMARY KEY,
                                                       name TEXT UNIQUE NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attendance (
                                                          id SERIAL PRIMARY KEY,
                                                          member_id INTEGER REFERENCES members(id),
                    date DATE NOT NULL,
                    UNIQUE(member_id, date)
                    );
                """)
    members = ["방장"]
    for m in members:
        cur.execute("INSERT INTO members (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (m,))
    conn.commit()
    cur.close()
    release_db(conn)

def remove_emoji(text):
    emoji_pattern = re.compile("["
                               u"\U0001F600-\U0001F64F"
                               u"\U0001F300-\U0001F5FF"
                               u"\U0001F680-\U0001F6FF"
                               u"\U0001F1E0-\U0001F1FF"
                               u"\U00002702-\U000027B0"
                               u"\U0001f926-\U0001f937"
                               u"\U0001fa00-\U0001fa9f"
                               u"\u2600-\u26FF"
                               u"\u2700-\u27BF"
                               "]+", flags=re.UNICODE)
    return emoji_pattern.sub('', text).strip()

def parse_kakao_message(text):
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]

    # 첫 번째 줄에서 날짜 추출 (이모티콘 제거 후)
    parsed_date = date.today().isoformat()
    names_lines = lines

    if lines:
        first_line = remove_emoji(lines[0])
        date_match = re.search(r'(\d{2,4})[.\s]+(\d{1,2})[.\s]+(\d{1,2})', first_line)
        if date_match:
            y, m, d = date_match.groups()
            if len(y) == 2:
                y = "20" + y
            parsed_date = f"{y}-{int(m):02d}-{int(d):02d}"
            names_lines = lines[1:]  # 첫 줄 제외하고 나머지가 이름

    # 둘째 줄부터 이름 파싱 (이모티콘 제거 후 콤마 구분)
    names_text = ",".join(names_lines)
    names_text = remove_emoji(names_text)
    raw_names = [n.strip() for n in re.split(r'[,،、]', names_text) if n.strip()]
    names = [n for n in raw_names if n and not re.match(r'^[\d\s().월화수목금토일(금)(월)(화)(수)(목)(토)(일)출석부]+$', n)]

    return parsed_date, names

def get_all_streaks(cur):
    cur.execute("SELECT member_id, date FROM attendance ORDER BY member_id, date DESC")
    rows = cur.fetchall()
    member_dates = {}
    for r in rows:
        mid = r["member_id"] if isinstance(r, dict) else r[0]
        d = r["date"] if isinstance(r, dict) else r[1]
        member_dates.setdefault(mid, []).append(d)
    streaks = {}
    for mid, dates in member_dates.items():
        streak = 1
        for i in range(1, len(dates)):
            if (dates[i-1] - dates[i]).days == 1:
                streak += 1
            else:
                break
        streaks[mid] = streak
    return streaks

def find_member_id(name, cur):
    # 1단계 - 정확히 일치 (비활성 멤버는 제외)
    cur.execute("SELECT id FROM members WHERE name=%s AND active = TRUE", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    # 2단계 - alias 매칭 (비활성 멤버는 제외)
    cur.execute("""
                SELECT al.member_id FROM aliases al
                                             JOIN members m ON al.member_id = m.id
                WHERE al.alias=%s AND m.active = TRUE
                """, (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    return None

@app.route("/")
def index():
    today = date.today().isoformat()
    return render_template("index.html", today=today)

# UptimeRobot ping 엔드포인트 (슬립 방지)
@app.route("/ping")
def ping():
    return "pong", 200

@app.route("/api/today")
def api_today():
    target = request.args.get("date", date.today().isoformat())
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
                SELECT m.name FROM attendance a
                                       JOIN members m ON a.member_id = m.id
                WHERE a.date = %s AND m.active = TRUE ORDER BY m.name
                """, (target,))
    present = [r["name"] for r in cur.fetchall()]

    cur.execute("SELECT name FROM members WHERE active = TRUE ORDER BY name")
    all_members = [r["name"] for r in cur.fetchall()]
    cur.close()
    release_db(conn)

    absent = [n for n in all_members if n not in present]
    return jsonify({
        "date": target,
        "present": present,
        "absent": absent,
        "total": len(all_members),
        "count": len(present)
    })

@app.route("/api/months")
def api_months():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT DISTINCT TO_CHAR(date, 'YYYY-MM') as ym FROM attendance ORDER BY ym")
    months = [r["ym"] for r in cur.fetchall()]
    cur.close()
    release_db(conn)
    if not months:
        now = date.today()
        months = [f"{now.year}-{now.month:02d}"]
    return jsonify(months)

@app.route("/api/monthly")
def api_monthly():
    now = date.today()
    year = int(request.args.get("year", now.year))
    month = int(request.args.get("month", now.month))
    last_day = calendar.monthrange(year, month)[1]
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-{last_day}"

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, name FROM members WHERE active = TRUE ORDER BY id")
    members = cur.fetchall()

    cur.execute("""
                SELECT m.name, a.date::text FROM attendance a
                                                     JOIN members m ON a.member_id = m.id
                WHERE a.date >= %s AND a.date <= %s
                """, (start, end))
    records = cur.fetchall()
    att_map = {}
    for r in records:
        att_map.setdefault(r["name"], set()).add(r["date"])

    # 연속 출석 일괄 계산 (쿼리 1회)
    streak_map = get_all_streaks(cur)

    # 올해 총 출석일수 조회 (쿼리 1회)
    cur.execute("""
                SELECT m.id, COUNT(*) as yearly FROM attendance a
                                                         JOIN members m ON a.member_id = m.id
                WHERE EXTRACT(YEAR FROM a.date) = %s
                GROUP BY m.id
                """, (year,))
    yearly_map = {r["id"]: r["yearly"] for r in cur.fetchall()}

    result = []
    for m in members:
        dates = sorted(att_map.get(m["name"], []))
        streak = streak_map.get(m["id"], 0)
        yearly = yearly_map.get(m["id"], 0)
        result.append({"name": m["name"], "count": len(dates), "dates": dates, "streak": streak, "yearly": yearly})
    # 1순위: 출석 횟수, 2순위: 연속 출석, 3순위: 올해 총 출석, 4순위: 이름 가나다순
    cur.close()
    release_db(conn)
    result.sort(key=lambda x: (-x["count"], -x["yearly"], x["name"]))
    return jsonify(result)

@app.route("/api/yearly")
def api_yearly():
    year = int(request.args.get("year", date.today().year))
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, name FROM members WHERE active = TRUE ORDER BY id")
    members = cur.fetchall()

    cur.execute("""
                SELECT m.id, m.name, COUNT(*) as cnt FROM attendance a
                                                              JOIN members m ON a.member_id = m.id
                WHERE EXTRACT(YEAR FROM a.date) = %s
                GROUP BY m.id, m.name
                """, (year,))
    counts = {r["id"]: {"name": r["name"], "count": r["cnt"]} for r in cur.fetchall()}
    cur.close()
    release_db(conn)

    result = []
    for m in members:
        cnt = counts.get(m["id"], {}).get("count", 0)
        result.append({"name": m["name"], "count": cnt})
    result.sort(key=lambda x: (-x["count"], x["name"]))
    return jsonify(result)

@app.route("/api/aliases")
def api_aliases():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
                SELECT a.id, a.alias, m.name as member_name, m.id as member_id
                FROM aliases a JOIN members m ON a.member_id = m.id
                ORDER BY m.name, a.alias
                """)
    rows = cur.fetchall()
    cur.close()
    release_db(conn)
    return jsonify([dict(r) for r in rows])

@app.route("/api/aliases/add", methods=["POST"])
def api_alias_add():
    data = request.json
    member_name = data.get("member_name", "").strip()
    alias = data.get("alias", "").strip()
    if not member_name or not alias:
        return jsonify({"error": "멤버 이름과 alias를 입력해주세요"}), 400
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id FROM members WHERE name=%s", (member_name,))
    member = cur.fetchone()
    if not member:
        cur.close()
        release_db(conn)
        return jsonify({"error": f"멤버 '{member_name}'를 찾을 수 없어요"}), 404
    try:
        cur.execute("INSERT INTO aliases (member_id, alias) VALUES (%s, %s)", (member["id"], alias))
        conn.commit()
    except Exception as e:
        conn.rollback()
        cur.close()
        release_db(conn)
        return jsonify({"error": "이미 등록된 alias예요"}), 400
    cur.close()
    release_db(conn)
    return jsonify({"ok": True})

@app.route("/api/aliases/delete", methods=["POST"])
def api_alias_delete():
    data = request.json
    alias_id = data.get("id")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM aliases WHERE id=%s", (alias_id,))
    conn.commit()
    cur.close()
    release_db(conn)
    return jsonify({"ok": True})

@app.route("/api/members")
def api_members():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, name FROM members WHERE active = TRUE ORDER BY name")
    members = [dict(r) for r in cur.fetchall()]
    cur.close()
    release_db(conn)
    return jsonify(members)

@app.route("/api/members/add", methods=["POST"])
def api_member_add():
    data = request.json
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "이름을 입력해주세요"}), 400
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("INSERT INTO members (name, active) VALUES (%s, TRUE) RETURNING id", (name,))
        new_id = cur.fetchone()["id"]
        conn.commit()
    except Exception:
        conn.rollback()
        cur.close()
        release_db(conn)
        return jsonify({"error": "이미 등록된 멤버예요"}), 400
    cur.close()
    release_db(conn)
    return jsonify({"ok": True, "id": new_id, "name": name})

@app.route("/api/members/rename", methods=["POST"])
def api_member_rename():
    data = request.json
    mid = data.get("id")
    name = data.get("name", "").strip()
    if not mid or not name:
        return jsonify({"error": "이름을 입력해주세요"}), 400
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE members SET name=%s WHERE id=%s", (name, mid))
        conn.commit()
    except Exception:
        conn.rollback()
        cur.close()
        release_db(conn)
        return jsonify({"error": "이미 있는 이름이에요"}), 400
    cur.close()
    release_db(conn)
    return jsonify({"ok": True})

@app.route("/api/members/delete", methods=["POST"])
def api_member_delete():
    data = request.json
    mid = data.get("id")
    if not mid:
        return jsonify({"error": "멤버를 선택해주세요"}), 400
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE members SET active = FALSE WHERE id=%s", (mid,))
    conn.commit()
    cur.close()
    release_db(conn)
    return jsonify({"ok": True})

@app.route("/api/team/assign", methods=["POST"])
def api_team_assign():
    """팀 직접 지정 저장. 해당 월 배정을 통째로 덮어씀 (재편성 가능)
    body: {year, month, assignments: [{member_id, team}]}  team은 'A'(A팀) 또는 'B'(B팀)"""
    data = request.json or {}
    now = date.today()
    year = int(data.get("year", now.year))
    month = int(data.get("month", now.month))
    assignments = data.get("assignments", [])

    # 유효성 검사 ('A'/'B' 아닌 값은 버림)
    cleaned = []
    for a in assignments:
        mid = a.get("member_id")
        team = a.get("team")
        if not mid or team not in ("A", "B"):
            continue
        cleaned.append((int(mid), team))

    if not cleaned:
        return jsonify({"error": "팀에 지정된 멤버가 없어요"}), 400

    conn = get_db()
    cur = conn.cursor()
    # 기존 배정 삭제 후 재삽입 (미참가로 바뀐 멤버도 함께 정리됨)
    cur.execute("DELETE FROM team_assignments WHERE year=%s AND month=%s", (year, month))
    for mid, team in cleaned:
        cur.execute("""
                    INSERT INTO team_assignments (member_id, team, year, month)
                    VALUES (%s, %s, %s, %s) ON CONFLICT (member_id, year, month) DO NOTHING
                    """, (mid, team, year, month))
    conn.commit()
    cur.close()
    release_db(conn)

    a_cnt = sum(1 for _, t in cleaned if t == "A")
    return jsonify({"ok": True, "year": year, "month": month,
                    "pok": a_cnt, "hell": len(cleaned) - a_cnt})

@app.route("/api/team/clear", methods=["POST"])
def api_team_clear():
    """해당 월 팀 편성 전체 삭제"""
    data = request.json or {}
    now = date.today()
    year = int(data.get("year", now.year))
    month = int(data.get("month", now.month))

    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM team_assignments WHERE year=%s AND month=%s", (year, month))
    conn.commit()
    cur.close()
    release_db(conn)
    return jsonify({"ok": True, "year": year, "month": month})

def fetch_team_data(req_y=None, req_m=None, with_daily=False):
    """팀전 데이터 조회 (API·이미지 공용). 연결 1회로 목록·편성·점수를 모두 가져옴.
    with_daily=True 이면 최근 7일 팀별 출석률도 함께 반환 (화면 그래프용)."""
    now = date.today()
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 편성이 있는 달 목록
    cur.execute("""
                SELECT DISTINCT year, month FROM team_assignments
                ORDER BY year DESC, month DESC
                """)
    months = [{"year": r["year"], "month": r["month"]} for r in cur.fetchall()]

    # 조회 대상 월 결정
    if req_y and req_m:
        year, month = int(req_y), int(req_m)
    elif any(t["year"] == now.year and t["month"] == now.month for t in months):
        year, month = now.year, now.month
    elif months:
        year, month = months[0]["year"], months[0]["month"]
    else:
        year, month = now.year, now.month

    last_day = calendar.monthrange(year, month)[1]
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-{last_day}"

    # 편성 현황 (비활성 멤버 제외)
    cur.execute("""
                SELECT ta.member_id, m.name, ta.team
                FROM team_assignments ta
                         JOIN members m ON ta.member_id = m.id
                WHERE ta.year = %s AND ta.month = %s AND m.active = TRUE
                ORDER BY ta.team, m.name
                """, (year, month))
    assignments = [dict(r) for r in cur.fetchall()]

    # 점수 (출석 1회당 1점) — 전체 행을 받지 않고 DB에서 집계
    cur.execute("""
                SELECT m.name, ta.team, COUNT(*) as score
                FROM attendance a
                         JOIN members m ON a.member_id = m.id
                         JOIN team_assignments ta
                              ON ta.member_id = m.id AND ta.year = %s AND ta.month = %s
                WHERE a.date >= %s AND a.date <= %s AND m.active = TRUE
                GROUP BY m.name, ta.team
                ORDER BY score DESC, m.name
                """, (year, month, start, end))
    score_rows = cur.fetchall()

    # 일별 팀 출석률 (화면 그래프용) — 이번 달 1일부터 오늘까지
    # (기본 화면은 어제까지 보이고, 오른쪽으로 밀면 오늘도 확인 가능)
    daily = []
    if with_daily and assignments:
        # 기준일: 진행 중인 달이면 오늘, 지난달이면 그 달 마지막 날
        if year == now.year and month == now.month:
            anchor = now
        else:
            anchor = date(year, month, last_day)
        month_first = date(year, month, 1)
        d_start = month_first
        if anchor >= month_first:
            cur.execute("""
                        SELECT a.date, ta.team, COUNT(*) as cnt
                        FROM attendance a
                                 JOIN members m ON a.member_id = m.id
                                 JOIN team_assignments ta
                                      ON ta.member_id = m.id AND ta.year = %s AND ta.month = %s
                        WHERE a.date >= %s AND a.date <= %s AND m.active = TRUE
                        GROUP BY a.date, ta.team
                        """, (year, month, d_start.isoformat(), anchor.isoformat()))
            day_map = {}
            for r in cur.fetchall():
                key = r["date"].isoformat() if hasattr(r["date"], "isoformat") else str(r["date"])
                day_map.setdefault(key, {})[r["team"]] = r["cnt"]

            size_a = sum(1 for a in assignments if a["team"] == "A")
            size_b = sum(1 for a in assignments if a["team"] == "B")
            wd = ["월", "화", "수", "목", "금", "토", "일"]
            span = (anchor - d_start).days + 1
            for i in range(span):
                dd = d_start + timedelta(days=i)
                key = dd.isoformat()
                ca = day_map.get(key, {}).get("A", 0)
                cb = day_map.get(key, {}).get("B", 0)
                daily.append({
                    "date": key,
                    "day": dd.day,
                    "weekday": wd[dd.weekday()],
                    "a_cnt": ca, "b_cnt": cb,
                    "a_rate": round(ca / size_a * 100) if size_a else 0,
                    "b_rate": round(cb / size_b * 100) if size_b else 0,
                })

    cur.close()
    release_db(conn)

    scores = {"A": 0, "B": 0}
    members = {"A": [], "B": []}
    for r in score_rows:
        scores[r["team"]] += r["score"]
        members[r["team"]].append({"name": r["name"], "score": r["score"]})

    return {
        "year": year,
        "month": month,
        "months": months,
        "assignments": assignments,
        "scores": scores,
        "members": members,
        "daily": daily
    }

@app.route("/api/team/all")
def api_team_all():
    """팀전 화면에 필요한 모든 데이터를 한 번에 반환"""
    return jsonify(fetch_team_data(request.args.get("year"), request.args.get("month"),
                                   with_daily=True))

@app.route("/team-image")
def team_image():
    """팀전 화면과 동일한 레이아웃의 PNG 반환 (저장/공유용)"""
    d = fetch_team_data(request.args.get("year"), request.args.get("month"))
    year, month = d["year"], d["month"]
    a_score, b_score = d["scores"]["A"], d["scores"]["B"]

    today = date.today()
    is_cur = (year == today.year and month == today.month)
    last_day = calendar.monthrange(year, month)[1]

    if a_score > b_score:
        winner, verb, diff = "A", ("리드" if is_cur else "승리"), a_score - b_score
        lead = f"A팀 {diff}점 {verb}"
    elif b_score > a_score:
        winner, verb, diff = "B", ("리드" if is_cur else "승리"), b_score - a_score
        lead = f"B팀 {diff}점 {verb}"
    else:
        winner, lead = None, "동점"

    if is_cur:
        left = last_day - today.day
        badge = f"{left}일 남음" if left > 0 else "마지막 날"
    else:
        badge = "종료"

    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    date_label = f"{today.month}월 {today.day}일 ({weekdays[today.weekday()]})"

    # 편성됐지만 출석 0인 멤버도 0점으로 포함 (화면과 동일)
    def build(team):
        rows = list(d["members"][team])
        have = {m["name"] for m in rows}
        for a in d["assignments"]:
            if a["team"] == team and a["name"] not in have:
                rows.append({"name": a["name"], "score": 0})
        rows.sort(key=lambda x: -x["score"])
        return rows

    img = generate_team_image({
        "month_label": f"{year}년 {month}월",
        "days_badge": badge,
        "date_label": date_label,
        "a": a_score, "b": b_score,
        "winner": winner, "lead": lead,
        "a_members": build("A"),
        "b_members": build("B"),
    })

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    # inline=1 이면 브라우저에 바로 표시 (길게 눌러 저장용), 아니면 다운로드
    if request.args.get("inline"):
        return send_file(buf, mimetype="image/png")
    return send_file(buf, mimetype="image/png", as_attachment=True,
                     download_name=f"team_{year}-{month:02d}.png")

@app.route("/api/team/months")
def api_team_months():
    """팀 배정이 존재하는 달 목록 (최신순)"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
                SELECT DISTINCT year, month FROM team_assignments
                ORDER BY year DESC, month DESC
                """)
    months = [{"year": r["year"], "month": r["month"]} for r in cur.fetchall()]
    cur.close()
    release_db(conn)
    return jsonify(months)

@app.route("/api/team/assignments")
def api_team_assignments():
    """현재 달 팀 배정 현황"""
    now = date.today()
    year = int(request.args.get("year", now.year))
    month = int(request.args.get("month", now.month))

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
                SELECT ta.member_id, m.name, ta.team
                FROM team_assignments ta
                         JOIN members m ON ta.member_id = m.id
                WHERE ta.year = %s AND ta.month = %s AND m.active = TRUE
                ORDER BY ta.team, m.name
                """, (year, month))
    rows = cur.fetchall()
    cur.close()
    release_db(conn)
    return jsonify([dict(r) for r in rows])

@app.route("/api/team/score")
def api_team_score():
    """이번 달 팀별 점수 (출석 1회당 1점)"""
    now = date.today()
    year = int(request.args.get("year", now.year))
    month = int(request.args.get("month", now.month))
    last_day = calendar.monthrange(year, month)[1]
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-{last_day}"

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 해당 달 출석 + 팀 배정 JOIN
    cur.execute("""
                SELECT m.name, ta.team, a.date
                FROM attendance a
                         JOIN members m ON a.member_id = m.id
                         JOIN team_assignments ta ON ta.member_id = m.id AND ta.year = %s AND ta.month = %s
                WHERE a.date >= %s AND a.date <= %s AND m.active = TRUE
                ORDER BY ta.team, m.name, a.date
                """, (year, month, start, end))
    rows = cur.fetchall()
    cur.close()
    release_db(conn)

    team_scores = {"A": 0, "B": 0}
    team_members = {"A": {}, "B": {}}

    for r in rows:
        point = 1  # 요일 구분 없이 출석 1회당 1점
        team = r["team"]
        name = r["name"]
        team_scores[team] += point
        if name not in team_members[team]:
            team_members[team][name] = 0
        team_members[team][name] += point

    return jsonify({
        "year": year,
        "month": month,
        "scores": team_scores,
        "members": {
            team: [{"name": n, "score": s} for n, s in sorted(members.items(), key=lambda x: -x[1])]
            for team, members in team_members.items()
        }
    })

@app.route("/api/checkin", methods=["POST"])
def api_checkin():
    data = request.json
    text = data.get("text", "")
    parsed_date, names = parse_kakao_message(text)

    conn = get_db()
    cur = conn.cursor()
    matched, unmatched = [], []
    for name in names:
        mid = find_member_id(name, cur)
        if mid:
            try:
                cur.execute(
                    "INSERT INTO attendance (member_id, date) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (mid, parsed_date)
                )
                matched.append(name)
            except Exception as e:
                unmatched.append(name)
        else:
            unmatched.append(name)
    conn.commit()
    cur.close()
    release_db(conn)

    try:
        generate_attendance_image(parsed_date)
    except:
        pass

    return jsonify({"date": parsed_date, "matched": matched, "unmatched": unmatched})

@app.route("/api/share-text")
def api_share_text():
    target = request.args.get("date", date.today().isoformat())
    dt = datetime.strptime(target, "%Y-%m-%d")

    last_day = calendar.monthrange(dt.year, dt.month)[1]
    m_start = f"{dt.year}-{dt.month:02d}-01"
    m_end = f"{dt.year}-{dt.month:02d}-{last_day}"

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 이번 달 출석 순위 TOP 3
    cur.execute("""
                SELECT m.name, COUNT(*) as cnt FROM attendance a
                                                        JOIN members m ON a.member_id = m.id
                WHERE a.date >= %s AND a.date <= %s AND m.active = TRUE
                GROUP BY m.id, m.name ORDER BY cnt DESC, m.name LIMIT 3
                """, (m_start, m_end))
    top = cur.fetchall()

    # 해당 날짜가 속한 달의 팀전 점수 (출석 1회당 1점)
    cur.execute("""
                SELECT ta.team, COUNT(*) as pts
                FROM attendance a
                         JOIN members m ON a.member_id = m.id
                         JOIN team_assignments ta
                              ON ta.member_id = a.member_id AND ta.year = %s AND ta.month = %s
                WHERE a.date >= %s AND a.date <= %s AND m.active = TRUE
                GROUP BY ta.team
                """, (dt.year, dt.month, m_start, m_end))
    team_rows = cur.fetchall()

    cur.close()
    release_db(conn)

    weekdays = ["월","화","수","목","금","토","일"]
    day_str = f"{dt.month}월 {dt.day}일({weekdays[dt.weekday()]})"
    top_str = " | ".join([f"{i+1}위 {r['name']} {r['cnt']}회" for i, r in enumerate(top)])

    # 팀전 블록 (편성이 없으면 생략)
    team_block = ""
    if team_rows:
        pts = {r["team"]: r["pts"] for r in team_rows}
        a_pts, b_pts = pts.get("A", 0), pts.get("B", 0)
        if a_pts > b_pts:
            lead = f"A팀 {a_pts - b_pts}점 리드"
        elif b_pts > a_pts:
            lead = f"B팀 {b_pts - a_pts}점 리드"
        else:
            lead = "🤝 동점"
        team_block = f"""⚔️ {dt.month}월 팀전
A팀 {a_pts} : {b_pts} B팀 — {lead}

"""

    text = f"""📋 폭헬방 출석부 — {day_str}

{team_block}🏆 {dt.month}월 순위
{top_str}

🔗 전체 출석부: {os.environ.get('APP_URL', '')}"""
    return jsonify({"text": text})

@app.route("/og-image")
def og_image():
    target = request.args.get("date", date.today().isoformat())
    path = f"static/og_{target}.png"
    if not os.path.exists(path):
        try:
            generate_attendance_image(target)
        except:
            pass
    if os.path.exists(path):
        return send_file(path, mimetype="image/png")
    return "", 404

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)