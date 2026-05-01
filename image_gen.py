from PIL import Image, ImageDraw, ImageFont
import psycopg2
import psycopg2.extras
import os
from datetime import datetime, date

DATABASE_URL = os.environ.get("DATABASE_URL")

def generate_attendance_image(target_date=None):
    if target_date is None:
        target_date = date.today().isoformat()

    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
                SELECT m.name FROM attendance a
                                       JOIN members m ON a.member_id = m.id
                WHERE a.date = %s ORDER BY m.name
                """, (target_date,))
    present = [r["name"] for r in cur.fetchall()]

    cur.execute("""
                SELECT m.name, COUNT(*) as cnt FROM attendance a
                                                        JOIN members m ON a.member_id = m.id
                GROUP BY m.id, m.name ORDER BY cnt DESC LIMIT 3
                """)
    top3 = cur.fetchall()

    cur.execute("SELECT COUNT(*) as c FROM members")
    total = cur.fetchone()["c"]
    cur.close()
    conn.close()

    W, H = 800, 420
    YELLOW = (254, 229, 0)
    DARK = (44, 44, 42)
    GRAY = (95, 94, 90)
    LIGHT_GRAY = (211, 209, 199)
    MUTED = (180, 178, 169)

    img = Image.new("RGB", (W, H), YELLOW)
    draw = ImageDraw.Draw(img)

    try:
        font_sub  = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 22)
        font_date = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 72)
        font_mid  = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 24)
        font_rank = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 16)
        font_url  = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 13)
    except:
        font_sub = font_date = font_mid = font_rank = font_url = ImageFont.load_default()

    dt = datetime.strptime(target_date, "%Y-%m-%d")
    weekdays = ["월","화","수","목","금","토","일"]

    draw.text((60, 100), "폭헬방 출석부", fill=GRAY, font=font_sub)
    date_str = f"{dt.month}월 {dt.day}일({weekdays[dt.weekday()]})"
    draw.text((60, 138), date_str, fill=DARK, font=font_date)
    draw.text((60, 242), f"오늘 출석 {len(present)}명 / 전체 {total}명", fill=(68, 68, 65), font=font_mid)
    draw.rectangle([60, 282, 740, 284], fill=LIGHT_GRAY)

    if top3:
        rank_parts = [f"{i+1}위 {r['name']} {r['cnt']}회" for i, r in enumerate(top3)]
        draw.text((60, 300), "  ·  ".join(rank_parts), fill=GRAY, font=font_rank)

    app_url = os.environ.get("APP_URL", "attendance-app.onrender.com").replace("https://", "")
    draw.text((60, 378), app_url, fill=MUTED, font=font_url)

    os.makedirs("static", exist_ok=True)
    path = f"static/og_{target_date}.png"
    img.save(path)
    return path
