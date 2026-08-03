from PIL import Image, ImageDraw, ImageFont
import os
from datetime import datetime, date

# 한글 폰트 후보 (앞에서부터 있는 것 사용)
FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
     "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
     "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
     "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf",
     "/usr/share/fonts/opentype/noto/NotoSansCJKkr-Bold.otf"),
]

def _load_fonts(sub_size, date_size):
    """사용 가능한 한글 폰트를 찾아 반환. 없으면 기본 폰트(한글 미지원)."""
    for regular, bold in FONT_CANDIDATES:
        if os.path.exists(regular):
            bold_path = bold if os.path.exists(bold) else regular
            try:
                return (ImageFont.truetype(regular, sub_size),
                        ImageFont.truetype(bold_path, date_size))
            except Exception:
                continue
    return ImageFont.load_default(), ImageFont.load_default()

def generate_attendance_image(target_date=None):
    if target_date is None:
        target_date = date.today().isoformat()

    W, H = 800, 420
    YELLOW = (254, 229, 0)
    DARK = (44, 44, 42)
    GRAY = (95, 94, 90)

    img = Image.new("RGB", (W, H), YELLOW)
    draw = ImageDraw.Draw(img)

    font_sub, font_date = _load_fonts(26, 84)

    dt = datetime.strptime(target_date, "%Y-%m-%d")
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    date_str = f"{dt.month}월 {dt.day}일({weekdays[dt.weekday()]})"
    title = "폭헬방 출석부"

    # 가운데 정렬로 배치
    tb = draw.textbbox((0, 0), title, font=font_sub)
    db = draw.textbbox((0, 0), date_str, font=font_date)
    gap = 18
    block_h = (tb[3] - tb[1]) + gap + (db[3] - db[1])
    top = (H - block_h) // 2

    draw.text(((W - (tb[2] - tb[0])) // 2 - tb[0], top - tb[1]),
              title, fill=GRAY, font=font_sub)
    draw.text(((W - (db[2] - db[0])) // 2 - db[0], top + (tb[3] - tb[1]) + gap - db[1]),
              date_str, fill=DARK, font=font_date)

    os.makedirs("static", exist_ok=True)
    path = f"static/og_{target_date}.png"
    img.save(path)
    return path