from flask import Flask, render_template, request, jsonify, send_file
from datetime import datetime, date
import os
import re
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
    members = [
        "계란","꽁치","노을","레오","무지","방장","새벽","제니","열심히행복","오운어",
        "요이","와치","유치원","이팀장","주이","코코아빠","화이띵","자본주의미소","체리",
        "sleep well","여우","홀트","가쥬아","스딩","김금삼","손라","아비노","차니",
        "원판수집가","초코언니","스와","김철수","하급닌자","졔초이","딩딩","돈카스",
        "제제","먕하치","양재동","밥춘식","봉봉"
    ]
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

    cur.execute("SELECT name FROM members ORDER BY name")
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
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-31"

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, name FROM members ORDER BY id")
    members = cur.fetchall()

    cur.execute("""
                SELECT m.name, a.date::text FROM attendance a
                                                     JOIN members m ON a.member_id = m.id
                WHERE a.date >= %s AND a.date <= %s
                """, (start, end))
    records = cur.fetchall()
    cur.close()
    release_db(conn)

    att_map = {}
    for r in records:
        att_map.setdefault(r["name"], set()).add(r["date"])

    result = []
    for m in members:
        dates = sorted(att_map.get(m["name"], []))
        result.append({"name": m["name"], "count": len(dates), "dates": dates})
    result.sort(key=lambda x: -x["count"])
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
    cur.execute("SELECT id, name FROM members ORDER BY name")
    members = [dict(r) for r in cur.fetchall()]
    cur.close()
    release_db(conn)
    return jsonify(members)

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