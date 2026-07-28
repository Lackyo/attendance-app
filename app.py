from flask import Flask, render_template, request, jsonify, send_file
from datetime import datetime, date
import os
import re
import calendar
import psycopg2
import psycopg2.extras
from image_gen import generate_attendance_image

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def release_db(conn):
    conn.close()

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
    # 1단계 - 정확히 일치
    cur.execute("SELECT id FROM members WHERE name=%s", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    # 2단계 - alias 매칭
    cur.execute("SELECT member_id FROM aliases WHERE alias=%s", (name,))
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
                WHERE a.date = %s ORDER BY m.name
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
    """이전 달 출석 순위 기반으로 이번 달 팀 자동 배정 (뱀 드래프트)"""
    now = date.today()
    year, month = now.year, now.month

    # 이미 배정됐으면 스킵
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT COUNT(*) as cnt FROM team_assignments WHERE year=%s AND month=%s", (year, month))
    if cur.fetchone()["cnt"] > 0:
        cur.close()
        release_db(conn)
        return jsonify({"error": "이미 이번 달 팀이 배정됐어요"}), 400

    # 이전 달 출석 횟수 순위
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    last_day = calendar.monthrange(prev_year, prev_month)[1]
    start = f"{prev_year}-{prev_month:02d}-01"
    end = f"{prev_year}-{prev_month:02d}-{last_day}"

    cur.execute("""
        SELECT m.id, m.name, COUNT(a.id) as cnt
        FROM members m
        LEFT JOIN attendance a ON a.member_id = m.id AND a.date >= %s AND a.date <= %s
        WHERE m.active = TRUE
        GROUP BY m.id, m.name
        ORDER BY cnt DESC, m.name
    """, (start, end))
    members = cur.fetchall()

    # 뱀 드래프트: 1→A, 2→B, 3→A, 4→B ...
    for i, m in enumerate(members):
        team = "A" if i % 2 == 0 else "B"
        cur.execute("""
            INSERT INTO team_assignments (member_id, team, year, month)
            VALUES (%s, %s, %s, %s) ON CONFLICT (member_id, year, month) DO NOTHING
        """, (m["id"], team, year, month))

    conn.commit()
    cur.close()
    release_db(conn)
    return jsonify({"ok": True, "year": year, "month": month})

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
        SELECT m.name, ta.team
        FROM team_assignments ta
        JOIN members m ON ta.member_id = m.id
        WHERE ta.year = %s AND ta.month = %s
        ORDER BY ta.team, m.name
    """, (year, month))
    rows = cur.fetchall()
    cur.close()
    release_db(conn)
    return jsonify([dict(r) for r in rows])

@app.route("/api/team/score")
def api_team_score():
    """이번 달 팀별 점수 (월~목 +1, 금~일 +2)"""
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
        WHERE a.date >= %s AND a.date <= %s
        ORDER BY ta.team, m.name, a.date
    """, (year, month, start, end))
    rows = cur.fetchall()
    cur.close()
    release_db(conn)

    team_scores = {"A": 0, "B": 0}
    team_members = {"A": {}, "B": {}}

    for r in rows:
        d = r["date"]
        weekday = d.weekday()  # 0=월 ... 6=일
        point = 2 if weekday >= 4 else 1  # 금(4)~일(6) +2, 나머지 +1
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
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
                SELECT m.name FROM attendance a
                                       JOIN members m ON a.member_id = m.id
                WHERE a.date = %s ORDER BY m.name
                """, (target,))
    present = cur.fetchall()

    cur.execute("""
                SELECT m.name, COUNT(*) as cnt FROM attendance a
                                                        JOIN members m ON a.member_id = m.id
                GROUP BY m.id, m.name ORDER BY cnt DESC LIMIT 3
                """)
    top = cur.fetchall()
    cur.close()
    release_db(conn)

    dt = datetime.strptime(target, "%Y-%m-%d")
    weekdays = ["월","화","수","목","금","토","일"]
    day_str = f"{dt.month}월 {dt.day}일({weekdays[dt.weekday()]})"
    names_str = ", ".join([r["name"] for r in present])
    top_str = " | ".join([f"{i+1}위 {r['name']} {r['cnt']}회" for i, r in enumerate(top)])

    text = f"""📋 폭헬방 출석부 — {day_str}
✅ 출석 {len(present)}명
{names_str}

🏆 누적 순위
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