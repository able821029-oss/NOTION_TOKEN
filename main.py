"""
이로운 경제 — 쇼츠 소재 레이더 (클라우드판)
구글 트렌드 급상승 + 구글뉴스 경제 RSS → 관심도 점수화 → TOP 5 → 노션 적재
네이버는 클라우드 IP 차단이라 제외. PC의 radar.py와 병행 운용.
"""
import os, re, sys, json, html, datetime, urllib.request
import xml.etree.ElementTree as ET
import requests

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

TOP_N = int(os.environ.get("TOP_N", "5"))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0"}

KST = datetime.timezone(datetime.timedelta(hours=9))
now = datetime.datetime.now(KST)
today = now.strftime("%Y-%m-%d")
ROUND = "06시" if now.hour < 12 else "18시"

# 채널 축: 45세 이상 61.5% · 남성 91.3% → 자산·주식·환율 우선
CATEGORY = {
    "주식": ["코스피", "코스닥", "주가", "증시", "상장", "자사주", "배당", "주주환원",
             "삼성전자", "하이닉스", "반도체주", "공매도", "사이드카", "서킷브레이커"],
    "환율": ["환율", "원화", "달러", "엔화", "위안", "외환", "킹달러", "엔캐리"],
    "금리": ["금리", "기준금리", "한은", "연준", "FOMC", "국채", "채권", "대출금리"],
    "부동산": ["아파트", "집값", "전세", "청약", "재건축", "분양", "주택", "임대", "종부세", "양도세"],
    "자산": ["연금", "퇴직", "상속", "증여", "세금", "자산", "노후", "비트코인", "코인", "금값"],
    "산업": ["실적", "수출", "기업", "공장", "파업", "노조", "인수", "합병", "구조조정"],
    "정책": ["정부", "대책", "규제", "세제", "예산", "국회", "법안", "지원금"],
}
ECON_HINT = [w for ws in CATEGORY.values() for w in ws] + [
    "경제", "물가", "인플레", "GDP", "가계부채", "매수", "매도", "투자"]

# 정치 대립·지정학은 채널 축 밖 — 감점
PENALTY = ["대통령", "여당", "야당", "민주당", "국민의힘", "장관 임명", "특검",
           "우크라", "러시아", "이스라엘", "북한", "미사일", "전쟁"]


def fetch(url, timeout=25):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout).read()


def norm(s):
    return re.sub(r"[^가-힣A-Za-z0-9]", "", s or "")


# ── 1. 구글 트렌드 급상승 (가산 신호) ────────────────────────────
def get_trends():
    ns = {"ht": "https://trends.google.com/trending/rss"}
    out = []
    try:
        root = ET.fromstring(fetch("https://trends.google.com/trending/rss?geo=KR"))
    except Exception as e:
        print(f"[warn] 트렌드 실패: {e}", file=sys.stderr)
        return out
    for it in root.findall(".//item"):
        kw = (it.findtext("title") or "").strip()
        traf = it.findtext("ht:approx_traffic", default="", namespaces=ns)
        vol = int(re.sub(r"[^0-9]", "", traf) or 0)
        heads = [n.findtext("ht:news_item_title", namespaces=ns) or ""
                 for n in it.findall("ht:news_item", ns)]
        out.append({"kw": kw, "vol": vol, "heads": heads})
    print(f"[구글 트렌드] {len(out)}건 수집")
    return out


# ── 2. 구글뉴스 경제 RSS (본 소스) ──────────────────────────────
def get_news():
    url = "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko"
    items = []
    try:
        root = ET.fromstring(fetch(url))
    except Exception as e:
        print(f"[fatal] 구글뉴스 실패: {e}", file=sys.stderr)
        return items
    for rank, it in enumerate(root.findall(".//item"), 1):
        raw = html.unescape(it.findtext("title") or "")
        title, _, press = raw.rpartition(" - ")
        items.append({
            "title": (title or raw).strip(),
            "press": press.strip() or "구글뉴스",
            "link": (it.findtext("link") or "").strip(),
            "rank": rank,
        })
    print(f"[구글뉴스 경제] {len(items)}건 수집")
    return items


# ── 2-b. 트렌드 연관 뉴스를 후보로 편입 ─────────────────────────
def trend_candidates(trends):
    out = []
    for t in trends:
        for h in t["heads"]:
            h = html.unescape(h or "").strip()
            if not h or not any(w in h for w in ECON_HINT):
                continue
            out.append({"title": h, "press": t["kw"], "link": "", "rank": 0,
                        "from_trend": True, "kw": t["kw"], "vol": t["vol"]})
            break  # 키워드당 대표 기사 1건
    print(f"[트렌드→경제 후보] {len(out)}건")
    return out


# ── 3. 점수화 ────────────────────────────────────────────────
trends_news_cache = []


def categorize(title):
    best, hits = "기타", 0
    for cat, words in CATEGORY.items():
        n = sum(1 for w in words if w in title)
        if n > hits:
            best, hits = cat, n
    return best, hits


def score(item, trends):
    title = item["title"]
    if not any(w in title for w in ECON_HINT):
        return None

    nt = norm(title)
    trend_kw, trend_vol = "", 0

    if item.get("from_trend"):
        # 트렌드 출신 — 검색량이 곧 폭발력. 45~80점
        trend_kw, trend_vol = item["kw"], item["vol"]
        s = 45.0 + 35.0 * (min(trend_vol, 10000) / 10000)
        # 같은 이슈가 구글뉴스에도 있으면 2소스 동시 → 가산
        if any(norm(n["title"])[:14] and norm(n["title"])[:14] in nt
               for n in trends_news_cache):
            s += 15.0
    else:
        # (a) 뉴스 순위 0~55점
        s = max(0.0, 55.0 * (1 - (item["rank"] - 1) / 60))
        # (b) 트렌드 매칭 시 가산 0~40점
        for t in trends:
            k = norm(t["kw"])
            matched = len(k) >= 2 and k in nt
            if not matched:
                matched = any(norm(h) and norm(h)[:14] in nt for h in t["heads"])
            if matched and t["vol"] > trend_vol:
                trend_kw, trend_vol = t["kw"], t["vol"]
        if trend_vol:
            s += min(40.0, 10 + 30 * (min(trend_vol, 10000) / 10000))

    # (c) 카테고리 가중 — 자산·주식·환율 우선
    cat, hits = categorize(title)
    s += {"주식": 12, "환율": 12, "자산": 10, "금리": 8,
          "부동산": 8, "산업": 4, "정책": 2}.get(cat, 0)
    s += min(6, hits * 2)

    # (d) 정치·지정학 감점
    if any(w in title for w in PENALTY):
        s *= 0.7

    item.update(category=cat, trend_kw=trend_kw, trend_vol=trend_vol,
                score=round(min(100.0, s), 1))
    return item


# ── 4. Gemini — 사실이 아니라 '쇼츠 각도'만 ──────────────────────
def add_angles(picks):
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"[warn] Gemini 초기화 실패: {e}", file=sys.stderr)
        return picks

    lines = "\n".join(f'{i+1}. [{p["category"]}] {p["title"]}'
                      for i, p in enumerate(picks))
    prompt = (
        "아래는 오늘 한국 경제뉴스 헤드라인이다. 각 항목마다 유튜브 쇼츠 훅을 한 줄씩 써라.\n"
        "채널: 45세 이상 남성 시청자 90%, 자산·주식·환율 관심층. 종목 추천·투자 권유 금지, "
        "정치 입장 금지. 숫자를 읽는 법이나 구조를 설명하는 각도로.\n"
        "각 줄은 40자 이내. 헤드라인에 없는 사실을 새로 만들지 말 것.\n"
        "출력은 다른 말 없이 '번호. 훅' 형식 줄만.\n\n" + lines
    )
    try:
        r = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        for ln in (r.text or "").strip().split("\n"):
            m = re.match(r"^\s*(\d+)[.)]\s*(.+)$", ln.strip())
            if m and 1 <= int(m.group(1)) <= len(picks):
                picks[int(m.group(1)) - 1]["angle"] = m.group(2).strip()[:200]
    except Exception as e:
        print(f"[warn] Gemini 실패: {e}", file=sys.stderr)
    return picks


# ── 5. 노션 ─────────────────────────────────────────────────
NH = {"Authorization": f"Bearer {NOTION_TOKEN}",
      "Content-Type": "application/json", "Notion-Version": "2022-06-28"}


def existing_titles(days=3):
    since = (now - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        r = requests.post(
            f"https://api.notion.com/v1/databases/{DATABASE_ID}/query", headers=NH,
            json={"page_size": 100,
                  "filter": {"property": "수집일시", "date": {"on_or_after": since}}},
            timeout=30)
        r.raise_for_status()
        out = set()
        for pg in r.json().get("results", []):
            t = pg["properties"].get("제목", {}).get("title", [])
            if t:
                out.add(norm(t[0]["plain_text"]))
        return out
    except Exception as e:
        print(f"[warn] 중복조회 실패(전건 적재로 진행): {e}", file=sys.stderr)
        return set()


def upload(p):
    props = {
        "제목": {"title": [{"text": {"content": p["title"][:200]}}]},
        "언론사/채널": {"rich_text": [{"text": {"content": p["press"][:100]}}]},
        "관심도": {"number": p["score"]},
        "카테고리": {"select": {"name": p["category"]}},
        "소스": {"select": {"name": "구글 트렌드" if p.get("from_trend") else "구글 RSS"}},
        "회차": {"select": {"name": ROUND}},
        "수집일시": {"date": {"start": now.isoformat(timespec="seconds")}},
    }
    if p["rank"]:
        props["순위"] = {"number": p["rank"]}
    if p.get("link"):
        props["링크"] = {"url": p["link"][:2000]}
    if p.get("angle"):
        props["쇼츠 각도"] = {"rich_text": [{"text": {"content": p["angle"]}}]}
    if p["trend_vol"]:
        props["요약"] = {"rich_text": [{"text": {
            "content": f'구글 트렌드 급상승 "{p["trend_kw"]}" 검색 {p["trend_vol"]}+ 연동'}}]}

    r = requests.post("https://api.notion.com/v1/pages", headers=NH,
                      json={"parent": {"database_id": DATABASE_ID}, "properties": props},
                      timeout=30)
    if r.status_code >= 400:
        print(f"[NOTION {r.status_code}] {r.text[:400]}", file=sys.stderr)
        return False
    return True


# ── main ────────────────────────────────────────────────────
def main():
    trends = get_trends()
    news = get_news()
    if not news:
        sys.exit("구글뉴스 수집 실패 — 중단")

    global trends_news_cache
    trends_news_cache = news
    cands = trend_candidates(trends) + news
    scored = [x for x in (score(dict(n), trends) for n in cands) if x]
    scored.sort(key=lambda x: -x["score"])

    seen, uniq = set(), []
    for it in scored:
        k = norm(it["title"])[:20]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(it)

    dup = existing_titles()
    picks, skipped = [], 0
    for it in uniq:
        if norm(it["title"])[:20] in {d[:20] for d in dup}:
            skipped += 1
            continue
        picks.append(it)
        if len(picks) >= TOP_N:
            break

    print(f"\n경제 필터 통과 {len(scored)} → 중복병합 {len(uniq)} → "
          f"기적재 제외 {skipped} → 적재 {len(picks)}건 ({ROUND})\n")

    picks = add_angles(picks)
    ok = 0
    for i, p in enumerate(picks, 1):
        tag = f' / 트렌드 "{p["trend_kw"]}" {p["trend_vol"]}+' if p["trend_vol"] else ""
        print(f'{i}. [{p["score"]}] [{p["category"]}] {p["title"]}{tag}')
        if p.get("angle"):
            print(f'   훅: {p["angle"]}')
        if upload(p):
            ok += 1
    print(f"\n적재 완료 {ok}/{len(picks)}건")
    if ok == 0 and picks:
        sys.exit("전건 적재 실패")


if __name__ == "__main__":
    main()
