# 폭헬방 출석부 🏋️
Render + Supabase + UptimeRobot 완전 무료 구성

---

## 배포 순서

### 1단계 — Supabase DB 만들기
1. https://supabase.com 접속 → GitHub로 가입
2. "New Project" 클릭
3. 프로젝트 이름: `attendance-db`
4. 비밀번호 설정 (기억해두기)
5. Region: Northeast Asia (Seoul)
6. 생성 완료 후 → Settings → Database
7. **Connection string (URI)** 복사해두기
   - 형식: `postgresql://postgres:[비밀번호]@db.xxxx.supabase.co:5432/postgres`

---

### 2단계 — GitHub에 코드 올리기
```bash
git init
git add .
git commit -m "폭헬방 출석부"
git remote add origin https://github.com/lackyo/attendance-app.git
git push -u origin main
```

---

### 3단계 — Render 배포
1. https://render.com 접속 → GitHub 로그인
2. "New +" → "Web Service"
3. GitHub 레포 연결
4. 설정:
   - Name: `attendance-app`
   - Runtime: `Python 3`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python -c 'from app import init_db; init_db()' && gunicorn app:app --bind 0.0.0.0:$PORT`
5. Environment Variables 추가:
   - `DATABASE_URL` = Supabase에서 복사한 URI
   - `APP_URL` = 나중에 생성된 Render URL 입력
6. "Create Web Service" 클릭 → 3~5분 후 배포 완료

---

### 4단계 — UptimeRobot 슬립 방지
1. https://uptimerobot.com 접속 → 무료 가입
2. "Add New Monitor" 클릭
3. 설정:
   - Monitor Type: HTTP(s)
   - Friendly Name: `폭헬방 출석부`
   - URL: `https://내Render주소.onrender.com/ping`
   - Monitoring Interval: **5 minutes**
4. 저장 → 이제 슬립 없음!

---

## 사용법
1. 카카오톡 출석 메시지 복사
2. 출석부 웹사이트 → "출석입력" 탭
3. 붙여넣기 → "출석 등록하기"
4. "오늘" 탭에서 현황 확인
5. "카카오톡으로 공유하기" 버튼 → 카톡방에 붙여넣기

## 카카오톡 링크 미리보기
링크를 카톡방에 공유하면 출석 현황 이미지가 자동으로 미리보기로 표시됩니다.
