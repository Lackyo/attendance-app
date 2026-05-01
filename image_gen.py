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
    img = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    YELLOW = (254, 229, 0)
    DARK = (44, 44, 42)
    GREEN = (29, 158, 117)
    GRAY = (136, 135, 128)
    LIGHT = (241, 239, 232)

    draw.rectangle([0, 0, W, 70], fill=YELLOW)

    try:
        font_lg = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 28)
        font_md = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 20)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 16)
        font_nm = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 17)
    except:
        font_lg = font_md = font_sm = font_nm = ImageFont.load_default()

    dt = datetime.strptime(target_date, "%Y-%m-%d")
    weekdays = ["월","화","수","목","금","토","일"]
    date_str = f"{dt.year}년 {dt.month}월 {dt.day}일({weekdays[dt.weekday()]})"

    draw.text((24, 14), "폭헬방 출석부 🏋️", fill=DARK, font=font_lg)
    draw.text((24, 46), date_str, fill=DARK, font=font_sm)

    draw.rounded_rectangle([W-160, 10, W-20, 60], radius=10, fill=DARK)
    draw.text((W-148, 16), "출석", fill=YELLOW, font=font_sm)
    draw.text((W-112, 10), str(len(present)), fill=YELLOW, font=font_lg)
    draw.text((W-76, 30), f"/ {total}명", fill=GRAY, font=font_sm)

    draw.text((24, 88), "✅ 오늘 출석", fill=GREEN, font=font_md)

    x, y = 24, 120
    for name in present:
        bbox = draw.textbbox((0, 0), name, font=font_nm)
        tw = bbox[2] - bbox[0]
        tag_w = tw + 24
        if x + tag_w > W - 24:
            x = 24
            y += 38
        if y > 255:
            remaining = len(present) - present.index(name)
            draw.text((x, y + 4), f"+{remaining}명 더", fill=GRAY, font=font_sm)
            break
        draw.rounded_rectangle([x, y, x + tag_w, y + 30], radius=6, fill=LIGHT)
        draw.text((x + 12, y + 6), name, fill=DARK, font=font_nm)
        x += tag_w + 6

    draw.rectangle([24, 292, W - 24, 293], fill=LIGHT)
    draw.text((24, 302), "🏆 누적 순위", fill=DARK, font=font_md)

    medals = ["1위", "2위", "3위"]
    for i, r in enumerate(top3):
        bx = 24 + i * 258
        draw.rounded_rectangle([bx, 330, bx + 244, 390], radius=8, fill=LIGHT)
        draw.text((bx + 12, 338), f"{medals[i]} {r['name']}", fill=DARK, font=font_md)
        draw.text((bx + 12, 364), f"{r['cnt']}회 출석", fill=GREEN, font=font_sm)

    os.makedirs("static", exist_ok=True)
    path = f"static/og_{target_date}.png"
    img.save(path)
    return path
