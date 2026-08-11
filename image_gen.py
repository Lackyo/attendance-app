from PIL import Image, ImageDraw, ImageFont
import os
from datetime import datetime, date

_HERE = os.path.dirname(os.path.abspath(__file__))

# 한글 폰트 후보 (앞에서부터 있는 것 사용)
# 1순위: 저장소에 포함한 폰트 — Render 등 시스템 폰트가 없는 환경 대비
FONT_CANDIDATES = [
    (os.path.join(_HERE, "fonts", "NanumGothic.ttf"),
     os.path.join(_HERE, "fonts", "NanumGothicBold.ttf")),
    (os.path.join(_HERE, "static", "fonts", "NanumGothic.ttf"),
     os.path.join(_HERE, "static", "fonts", "NanumGothicBold.ttf")),
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
     "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
     "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
     "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf",
     "/usr/share/fonts/opentype/noto/NotoSansCJKkr-Bold.otf"),
]

def _has_korean_glyph(path):
    """한글을 실제로 그릴 수 있는 폰트인지 확인.
    없는 글자는 .notdef(빈 사각형)로 그려지므로, 사용자 영역 문자와
    렌더 결과가 같으면 한글 미지원으로 판단."""
    try:
        f = ImageFont.truetype(path, 40)
        ko = f.getmask("가")
        nd = f.getmask("\ue001")   # 존재할 가능성이 거의 없는 문자
        if ko.getbbox() is None:
            return False
        return not (ko.size == nd.size and bytes(ko) == bytes(nd))
    except Exception:
        return False

_FONT_CACHE = {}

def _resolve_fonts():
    """(regular, bold) 경로를 찾아 반환. 한글 렌더 가능한 폰트만 채택."""
    if "paths" in _FONT_CACHE:
        return _FONT_CACHE["paths"]
    found = (None, None)
    for regular, bold in FONT_CANDIDATES:
        if os.path.exists(regular) and _has_korean_glyph(regular):
            found = (regular, bold if os.path.exists(bold) else regular)
            break
    if found[0] is None:
        print("[image_gen] 경고: 한글 폰트를 찾지 못했습니다. "
              "fonts/NanumGothic.ttf 를 저장소에 추가해주세요.")
    _FONT_CACHE["paths"] = found
    return found

def _load_fonts(sub_size, date_size):
    """사용 가능한 한글 폰트를 찾아 반환. 없으면 기본 폰트(한글 미지원)."""
    regular, bold = _resolve_fonts()
    if regular:
        try:
            return (ImageFont.truetype(regular, sub_size),
                    ImageFont.truetype(bold, date_size))
        except Exception:
            pass
    return ImageFont.load_default(), ImageFont.load_default()

def generate_attendance_image(target_date=None, save=True):
    """save=False 이면 파일로 저장하지 않고 PIL Image 를 반환."""
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

    if not save:
        return img
    os.makedirs("static", exist_ok=True)
    path = f"static/og_{target_date}.png"
    img.save(path)
    return path

# ── 팀전 이미지 (화면 디자인을 2배 스케일로 재현) ─────────────────
EMOJI_FONT_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji-Regular.ttf",
]

def _font_paths():
    return _resolve_fonts()

def _f(path, size):
    return ImageFont.truetype(path, size) if path else ImageFont.load_default()

def _emoji_font():
    """NotoColorEmoji는 109px 고정 크기만 지원. 없으면 None."""
    for p in EMOJI_FONT_PATHS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, 109)
            except Exception:
                continue
    return None

def _paste_emoji(img, ch, xy, size, ef):
    """이모지를 size 높이로 붙이고 차지한 폭을 반환. 폰트 없으면 0."""
    if not ef:
        return 0
    tmp = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((0, 0), ch, font=ef, embedded_color=True)
    bb = tmp.getbbox()
    if not bb:
        return 0
    crop = tmp.crop(bb)
    ratio = size / crop.height
    new = crop.resize((max(1, int(crop.width * ratio)), size), Image.LANCZOS)
    img.paste(new, xy, new)
    return new.width

def _hgrad(draw, box, c1, c2):
    x0, y0, x1, y1 = box
    w = max(x1 - x0, 1)
    for i in range(w):
        t = i / w
        c = tuple(int(c1[k] + (c2[k] - c1[k]) * t) for k in range(3))
        draw.line([(x0 + i, y0), (x0 + i, y1)], fill=c)

def _round_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                        radius=radius, fill=255)
    return m

def generate_team_image(data):
    """팀전 화면과 동일한 레이아웃의 이미지를 PIL Image 로 반환.
    data = {month_label, days_badge, date_label, a, b, winner, lead,
            a_members, b_members}"""
    S = 2                      # 화면(390px) → 이미지(780px) 스케일
    W = 800
    PAD = 28                   # 화면 14px
    BG = (245, 244, 240); WHITE = (255, 255, 255)
    DARK = (44, 44, 42); GRAY = (170, 168, 160)
    YELLOW = (254, 229, 0); HDR_SUB = (85, 85, 85)
    RED = (226, 75, 74); GREEN = (29, 158, 117)
    RED_D = (179, 46, 45); RED_L = (244, 147, 139)
    GRN_L = (127, 213, 184); GRN_D = (11, 95, 73)
    LINE = (241, 239, 232)

    A, B = data["a"], data["b"]
    aM, bM = data["a_members"], data["b_members"]
    rows = max(len(aM), len(bM), 1)

    REG, BOLD = _font_paths()
    ef = _emoji_font()
    f_hdr    = _f(BOLD, 16 * S)   # 헤더 앱 이름
    f_hdr_d  = _f(REG, 12 * S)    # 헤더 날짜
    f_title  = _f(BOLD, 13 * S)   # 팀전 타이틀
    f_badge  = _f(BOLD, 10 * S)   # 남은 일수
    f_team   = _f(BOLD, 12 * S)   # A팀/B팀 라벨
    f_score  = _f(BOLD, 46 * S)   # 큰 점수
    f_vs     = _f(BOLD, 11 * S)
    f_pct    = _f(BOLD, 10 * S)
    f_lead   = _f(BOLD, 13 * S)
    f_ch     = _f(BOLD, 12 * S)   # 멤버카드 헤더
    f_csc    = _f(REG, 10 * S)    # 멤버카드 총점
    f_name   = _f(REG, 13 * S)
    f_num    = _f(BOLD, 13 * S)
    f_rank   = _f(REG, 10 * S)

    HDR_H  = 32 * S
    CARD_H = 200 * S
    ROW_H  = 32 * S
    COL_H  = 20 * S + rows * ROW_H + 5 * S
    H = HDR_H + PAD + CARD_H + 10 * S + COL_H + PAD

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    def tw(txt, font):
        bb = d.textbbox((0, 0), txt, font=font)
        return bb[2] - bb[0], bb

    # ── 카카오 노란 헤더 ──
    d.rectangle([0, 0, W, HDR_H], fill=YELLOW)
    hx = 16 * S
    d.text((hx, HDR_H // 2 - 11 * S), "폭헬방 출석부", font=f_hdr, fill=DARK)
    hx += tw("폭헬방 출석부", f_hdr)[0] + 6 * S
    _paste_emoji(img, "🏋️", (hx, HDR_H // 2 - 9 * S), 17 * S, ef)
    dl = data.get("date_label", "")
    if dl:
        dw, _ = tw(dl, f_hdr_d)
        d.text((W - 16 * S - dw, HDR_H // 2 - 8 * S), dl, font=f_hdr_d, fill=HDR_SUB)

    # ── 점수 카드 ──
    cy0 = HDR_H + PAD
    d.rounded_rectangle([PAD, cy0, W - PAD, cy0 + CARD_H], radius=12 * S, fill=WHITE)

    # 타이틀 행: ⚔️ + "N월 팀전" + [남은 일수]
    title = data["month_label"] + " 팀전"
    badge = data.get("days_badge", "")
    tw_t, _ = tw(title, f_title)
    em_w = 15 * S if ef else 0
    gap = 6 * S
    bw_pill = 0
    if badge:
        bw, _ = tw(badge, f_badge)
        bw_pill = bw + 14 * S
    total_w = em_w + (gap if em_w else 0) + tw_t + (8 * S + bw_pill if badge else 0)
    tx = (W - total_w) // 2
    ty = cy0 + 18 * S
    if em_w:
        _paste_emoji(img, "⚔️", (tx, ty - 1 * S), 14 * S, ef)
        tx += em_w + gap
    d.text((tx, ty - f_title.getbbox(title)[1]), title, font=f_title, fill=DARK)
    tx += tw_t
    if badge:
        tx += 8 * S
        ph = 15 * S
        d.rounded_rectangle([tx, ty - 1 * S, tx + bw_pill, ty - 1 * S + ph],
                            radius=ph // 2, fill=LINE)
        bb2 = f_badge.getbbox(badge)
        d.text((tx + 7 * S, ty - 1 * S + (ph - (bb2[3] - bb2[1])) // 2 - bb2[1]),
               badge, font=f_badge, fill=(136, 136, 136))

    # 점수 행
    sy = cy0 + 46 * S
    for label, val, cx, col in [("A팀", str(A), W // 4 + 8 * S, RED),
                                ("B팀", str(B), W * 3 // 4 - 8 * S, GREEN)]:
        lw, lb = tw(label, f_team)
        d.text((cx - lw // 2 - lb[0], sy - lb[1]), label, font=f_team, fill=col)
        vw, vb = tw(val, f_score)
        d.text((cx - vw // 2 - vb[0], sy + 18 * S - vb[1]), val, font=f_score, fill=col)

    # VS 배지
    vsw, vsb = tw("VS", f_vs)
    pw, ph = vsw + 16 * S, 17 * S
    px = W // 2 - pw // 2
    py = sy + 34 * S
    d.rounded_rectangle([px, py, px + pw, py + ph], radius=ph // 2, fill=DARK)
    d.text((px + 8 * S - vsb[0], py + (ph - (vsb[3] - vsb[1])) // 2 - vsb[1]),
           "VS", font=f_vs, fill=WHITE)

    # 게이지
    gx0, gx1 = PAD + 14 * S, W - PAD - 14 * S
    gy0, gh = cy0 + 118 * S, 20 * S
    if A + B == 0:
        d.rounded_rectangle([gx0, gy0, gx1, gy0 + gh], radius=gh // 2, fill=(233, 231, 224))
    else:
        total = A + B
        split = gx0 + int((gx1 - gx0) * A / total)
        bar = Image.new("RGB", (gx1 - gx0, gh), LINE)
        bd = ImageDraw.Draw(bar)
        if split > gx0:
            _hgrad(bd, (0, 0, split - gx0, gh), RED_D, RED_L)
        if split < gx1:
            _hgrad(bd, (split - gx0, 0, gx1 - gx0, gh), GRN_L, GRN_D)
        img.paste(bar, (gx0, gy0), _round_mask((gx1 - gx0, gh), gh // 2))
        aP = round(A / total * 100); bP = 100 - aP
        if aP >= 18:
            pb = f_pct.getbbox(f"{aP}%")
            d.text((gx0 + 8 * S, gy0 + (gh - (pb[3] - pb[1])) // 2 - pb[1]),
                   f"{aP}%", font=f_pct, fill=WHITE)
        if bP >= 18:
            t2 = f"{bP}%"; w2, pb2 = tw(t2, f_pct)
            d.text((gx1 - 8 * S - w2, gy0 + (gh - (pb2[3] - pb2[1])) // 2 - pb2[1]),
                   t2, font=f_pct, fill=WHITE)

    # 리드 배너
    win = data.get("winner")
    bg_c = (253, 236, 234) if win == "A" else ((225, 245, 238) if win == "B" else LINE)
    fg_c = (192, 57, 43) if win == "A" else ((8, 80, 65) if win == "B" else (136, 136, 136))
    by0, bh2 = cy0 + 150 * S, 34 * S
    d.rounded_rectangle([gx0, by0, gx1, by0 + bh2], radius=8 * S, fill=bg_c)
    lead = data["lead"]
    em2 = 13 * S if (ef and win) else 0
    lw2, lb2 = tw(lead, f_lead)
    sx = (W - (em2 + (5 * S if em2 else 0) + lw2)) // 2
    if em2:
        _paste_emoji(img, "🏆", (sx, by0 + (bh2 - em2) // 2), em2, ef)
        sx += em2 + 5 * S
    d.text((sx - lb2[0], by0 + (bh2 - (lb2[3] - lb2[1])) // 2 - lb2[1]),
           lead, font=f_lead, fill=fg_c)

    # ── 멤버 카드 2단 ──
    top = cy0 + CARD_H + 10 * S
    gap_c = 9 * S
    cw = (W - PAD * 2 - gap_c) // 2
    for idx, (mem, col, label, sc) in enumerate([(aM, RED, "A팀", A), (bM, GREEN, "B팀", B)]):
        card = Image.new("RGB", (cw, COL_H), WHITE)
        cd = ImageDraw.Draw(card)
        cd.rectangle([0, 0, cw, 20 * S], fill=col)
        lb3 = f_ch.getbbox(label)
        cd.text((11 * S, (20 * S - (lb3[3] - lb3[1])) // 2 - lb3[1]), label, font=f_ch, fill=WHITE)
        st = f"{sc}점"
        sb3 = cd.textbbox((0, 0), st, font=f_csc)
        cd.text((cw - 11 * S - (sb3[2] - sb3[0]), (20 * S - (sb3[3] - sb3[1])) // 2 - sb3[1]),
                st, font=f_csc, fill=(255, 255, 255))
        for i, m in enumerate(mem):
            y = 20 * S + i * ROW_H
            mid = y + ROW_H // 2
            # 순위 또는 왕관
            if i == 0 and m["score"] > 0 and ef:
                _paste_emoji(card, "👑", (10 * S, mid - 7 * S), 13 * S, ef)
            else:
                rb = cd.textbbox((0, 0), str(i + 1), font=f_rank)
                cd.text((12 * S - (rb[2] - rb[0]) // 2 + 4 * S, mid - (rb[3] - rb[1]) // 2 - rb[1]),
                        str(i + 1), font=f_rank, fill=(187, 187, 187))
            nb = cd.textbbox((0, 0), m["name"], font=f_name)
            cd.text((30 * S, mid - (nb[3] - nb[1]) // 2 - nb[1]), m["name"], font=f_name, fill=DARK)
            vb3 = cd.textbbox((0, 0), str(m["score"]), font=f_num)
            cd.text((cw - 11 * S - (vb3[2] - vb3[0]), mid - (vb3[3] - vb3[1]) // 2 - vb3[1]),
                    str(m["score"]), font=f_num, fill=col)
            if i < len(mem) - 1:
                cd.line([(0, y + ROW_H), (cw, y + ROW_H)], fill=LINE)
        img.paste(card, (PAD + idx * (cw + gap_c), top), _round_mask((cw, COL_H), 12 * S))

    return img


def generate_team_og_image(data):
    """카카오톡 링크 미리보기용 팀전 이미지 (800x420, 약 2:1).
    점수판만 담아 썸네일에서 잘리지 않게 구성.
    data = {month_label, days_badge, a, b, winner, lead}"""
    W, H = 800, 420
    BG = (245, 244, 240); WHITE = (255, 255, 255)
    DARK = (44, 44, 42); YELLOW = (254, 229, 0)
    RED = (226, 75, 74); GREEN = (29, 158, 117)
    RED_D = (179, 46, 45); RED_L = (244, 147, 139)
    GRN_L = (127, 213, 184); GRN_D = (11, 95, 73)
    LINE = (241, 239, 232)

    A, B = data["a"], data["b"]
    REG, BOLD = _font_paths()
    ef = _emoji_font()
    f_hdr   = _f(BOLD, 30)
    f_title = _f(BOLD, 27)
    f_badge = _f(BOLD, 19)
    f_team  = _f(BOLD, 24)
    f_score = _f(BOLD, 72)
    f_vs    = _f(BOLD, 21)
    f_pct   = _f(BOLD, 19)
    f_lead  = _f(BOLD, 27)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    def tw(txt, font):
        bb = d.textbbox((0, 0), txt, font=font)
        return bb[2] - bb[0], bb

    # 노란 헤더
    HDR = 58
    d.rectangle([0, 0, W, HDR], fill=YELLOW)
    hx = 28
    d.text((hx, HDR // 2 - 20), "폭헬방 출석부", font=f_hdr, fill=DARK)
    hx += tw("폭헬방 출석부", f_hdr)[0] + 10
    _paste_emoji(img, "🏋️", (hx, HDR // 2 - 16), 30, ef)

    # 카드
    cy0, cy1 = HDR + 22, H - 22
    d.rounded_rectangle([24, cy0, W - 24, cy1], radius=22, fill=WHITE)

    # 타이틀 행
    title = data["month_label"] + " 팀전"
    badge = data.get("days_badge", "")
    tw_t, _ = tw(title, f_title)
    em_w = 26 if ef else 0
    bw_pill = 0
    if badge:
        bw, _ = tw(badge, f_badge)
        bw_pill = bw + 26
    total_w = em_w + (10 if em_w else 0) + tw_t + (14 + bw_pill if badge else 0)
    tx, ty = (W - total_w) // 2, cy0 + 26
    if em_w:
        _paste_emoji(img, "⚔️", (tx, ty), 25, ef)
        tx += em_w + 10
    d.text((tx, ty - f_title.getbbox(title)[1]), title, font=f_title, fill=DARK)
    tx += tw_t
    if badge:
        tx += 14
        ph = 30
        d.rounded_rectangle([tx, ty - 3, tx + bw_pill, ty - 3 + ph], radius=ph // 2, fill=LINE)
        bb2 = f_badge.getbbox(badge)
        d.text((tx + 13, ty - 3 + (ph - (bb2[3] - bb2[1])) // 2 - bb2[1]),
               badge, font=f_badge, fill=(136, 136, 136))

    # 점수
    sy = cy0 + 72
    for label, val, cx, col in [("A팀", str(A), W // 4 + 10, RED),
                                ("B팀", str(B), W * 3 // 4 - 10, GREEN)]:
        lw, lb = tw(label, f_team)
        d.text((cx - lw // 2 - lb[0], sy - lb[1]), label, font=f_team, fill=col)
        vw, vb = tw(val, f_score)
        d.text((cx - vw // 2 - vb[0], sy + 30 - vb[1]), val, font=f_score, fill=col)

    # VS
    vsw, vsb = tw("VS", f_vs)
    pw, ph = vsw + 34, 34
    px, py = W // 2 - pw // 2, sy + 50
    d.rounded_rectangle([px, py, px + pw, py + ph], radius=ph // 2, fill=DARK)
    d.text((px + 17 - vsb[0], py + (ph - (vsb[3] - vsb[1])) // 2 - vsb[1]),
           "VS", font=f_vs, fill=WHITE)

    # 게이지
    gx0, gx1, gh = 54, W - 54, 32
    gy0 = cy0 + 186
    if A + B == 0:
        d.rounded_rectangle([gx0, gy0, gx1, gy0 + gh], radius=gh // 2, fill=(233, 231, 224))
    else:
        total = A + B
        split = gx0 + int((gx1 - gx0) * A / total)
        bar = Image.new("RGB", (gx1 - gx0, gh), LINE)
        bd = ImageDraw.Draw(bar)
        if split > gx0:
            _hgrad(bd, (0, 0, split - gx0, gh), RED_D, RED_L)
        if split < gx1:
            _hgrad(bd, (split - gx0, 0, gx1 - gx0, gh), GRN_L, GRN_D)
        img.paste(bar, (gx0, gy0), _round_mask((gx1 - gx0, gh), gh // 2))
        aP = round(A / total * 100); bP = 100 - aP
        if aP >= 18:
            pb = f_pct.getbbox(f"{aP}%")
            d.text((gx0 + 14, gy0 + (gh - (pb[3] - pb[1])) // 2 - pb[1]),
                   f"{aP}%", font=f_pct, fill=WHITE)
        if bP >= 18:
            t2 = f"{bP}%"; w2, pb2 = tw(t2, f_pct)
            d.text((gx1 - 14 - w2, gy0 + (gh - (pb2[3] - pb2[1])) // 2 - pb2[1]),
                   t2, font=f_pct, fill=WHITE)

    # 리드 배너
    win = data.get("winner")
    bg_c = (253, 236, 234) if win == "A" else ((225, 245, 238) if win == "B" else LINE)
    fg_c = (192, 57, 43) if win == "A" else ((8, 80, 65) if win == "B" else (136, 136, 136))
    by0, bh2 = cy0 + 236, 46
    d.rounded_rectangle([gx0, by0, gx1, by0 + bh2], radius=14, fill=bg_c)
    lead = data["lead"]
    em2 = 26 if (ef and win) else 0
    lw2, lb2 = tw(lead, f_lead)
    sx = (W - (em2 + (8 if em2 else 0) + lw2)) // 2
    if em2:
        _paste_emoji(img, "🏆", (sx, by0 + (bh2 - em2) // 2), em2, ef)
        sx += em2 + 8
    d.text((sx - lb2[0], by0 + (bh2 - (lb2[3] - lb2[1])) // 2 - lb2[1]),
           lead, font=f_lead, fill=fg_c)

    return img


def generate_og_image(data):
    """카카오톡 링크 미리보기용 기본 이미지 (800x420).
    날짜 + 이번 달 순위 TOP3. 팀전 여부와 무관하게 항상 동일한 구성.
    data = {date_label, month_label, top: [{name, cnt}, ...]}"""
    W, H = 800, 420
    BG = (245, 244, 240); WHITE = (255, 255, 255)
    DARK = (44, 44, 42); GRAY = (150, 148, 141); LIGHT = (196, 194, 186)
    YELLOW = (254, 229, 0); LINE = (238, 236, 228)
    GOLD = (198, 148, 20); SILVER = (138, 138, 138); BRONZE = (166, 106, 50)

    REG, BOLD = _font_paths()
    ef = _emoji_font()
    f_hdr   = _f(BOLD, 30)
    f_date  = _f(BOLD, 56)
    f_label = _f(REG, 21)
    f_name  = _f(BOLD, 27)
    f_cnt   = _f(REG, 21)
    f_rank  = _f(BOLD, 22)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    def tw(txt, font):
        bb = d.textbbox((0, 0), txt, font=font)
        return bb[2] - bb[0], bb

    def ctext(cx, y, txt, font, fill):
        w, bb = tw(txt, font)
        d.text((cx - w // 2 - bb[0], y - bb[1]), txt, font=font, fill=fill)

    # 노란 헤더
    HDR = 58
    d.rectangle([0, 0, W, HDR], fill=YELLOW)
    hx = 28
    d.text((hx, HDR // 2 - 20), "폭헬방 출석부", font=f_hdr, fill=DARK)
    hx += tw("폭헬방 출석부", f_hdr)[0] + 10
    _paste_emoji(img, "🏋️", (hx, HDR // 2 - 16), 30, ef)

    # 카드
    cy0, cy1 = HDR + 22, H - 22
    d.rounded_rectangle([24, cy0, W - 24, cy1], radius=22, fill=WHITE)

    # 날짜
    ctext(W // 2, cy0 + 34, data["date_label"], f_date, DARK)

    top = data.get("top") or []
    if not top:
        ctext(W // 2, cy0 + 132, "아직 출석 기록이 없어요", f_label, LIGHT)
        return img

    # 구분선 + 순위 라벨
    d.line([(64, cy0 + 116), (W - 64, cy0 + 116)], fill=LINE, width=2)
    ctext(W // 2, cy0 + 134, f"{data['month_label']} 순위", f_label, GRAY)

    # TOP3 3단
    medals = ["🥇", "🥈", "🥉"]
    fallback = ["1", "2", "3"]
    colors = [GOLD, SILVER, BRONZE]
    n = min(len(top), 3)
    slot = (W - 120) // 3
    base_x = 60 + slot // 2
    for i in range(n):
        cx = base_x + i * slot
        y = cy0 + 178
        placed = _paste_emoji(img, medals[i], (cx - 19, y), 38, ef)
        if not placed:
            ctext(cx, y + 8, fallback[i], f_rank, colors[i])
        ctext(cx, y + 52, top[i]["name"], f_name, DARK)
        ctext(cx, y + 90, f"{top[i]['cnt']}회", f_cnt, colors[i])

    return img