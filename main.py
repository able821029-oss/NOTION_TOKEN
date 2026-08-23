import os, re, datetime, requests
from google import genai
from google.genai import types

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

KST = datetime.timezone(datetime.timedelta(hours=9))
today = datetime.datetime.now(KST).strftime("%Y-%m-%d")

client = genai.Client(api_key=GEMINI_API_KEY)

prompt = (
    f"오늘은 {today}이다. 구글 검색으로 실제 오늘자 뉴스를 확인한 뒤, "
    "한국 경제/부동산 주요 뉴스 TOP 5를 작성하라.\n"
    "형식: 각 항목을 '## 1. 제목' 헤딩으로 쓰고, 그 아래 "
    "'- 핵심:', '- 쟁점:', '- 반응:' 3개 불릿으로 정리.\n"
    "검색으로 확인되지 않은 내용은 절대 지어내지 말 것."
)

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt,
    config=types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())]
    ),
)

content_text = (response.text or "").strip()
if not content_text:
    raise RuntimeError("Gemini 응답이 비어 있습니다.")


def rich(text):
    out = []
    for p in re.split(r"(\*\*[^*]+\*\*)", text):
        if not p:
            continue
        if p.startswith("**") and p.endswith("**"):
            out.append({"type": "text", "text": {"content": p[2:-2]},
                        "annotations": {"bold": True}})
        else:
            out.append({"type": "text", "text": {"content": p}})
    return out or [{"type": "text", "text": {"content": ""}}]


def md_to_blocks(md):
    blocks = []
    for line in md.split("\n"):
        s = line.strip()
        if not s:
            continue
        if len(s) >= 3 and set(s) <= set("-*_"):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            continue
        if s.startswith("### "):
            t, c = "heading_3", s[4:]
        elif s.startswith("## "):
            t, c = "heading_2", s[3:]
        elif s.startswith("# "):
            t, c = "heading_1", s[2:]
        elif s.startswith(("- ", "* ")):
            t, c = "bulleted_list_item", s[2:]
        elif re.match(r"^\d+\.\s", s):
            t, c = "numbered_list_item", re.sub(r"^\d+\.\s", "", s)
        else:
            t, c = "paragraph", s
        for i in range(0, len(c), 1900):
            chunk = c[i:i + 1900]
            blocks.append({"object": "block", "type": t,
                           t: {"rich_text": rich(chunk)}})
    return blocks[:100]


payload = {
    "parent": {"database_id": DATABASE_ID},
    "properties": {
        "제목": {"title": [{"text": {"content": f"[{today}] 일간 주요 경제 이슈 TOP 5"}}]},
        "날짜": {"date": {"start": today}}
    },
    "children": (
        [{"object": "block", "type": "heading_2",
          "heading_2": {"rich_text": [{"text": {"content": f"📌 {today} 브리핑 리포트"}}]}}]
        + md_to_blocks(content_text)
    )
}

res = requests.post("https://api.notion.com/v1/pages",
                    headers={
                        "Authorization": f"Bearer {NOTION_TOKEN}",
                        "Content-Type": "application/json",
                        "Notion-Version": "2022-06-28",
                    }, json=payload, timeout=30)

if res.status_code >= 400:
    print("NOTION ERROR", res.status_code, res.text)
res.raise_for_status()
print("업로드 완료:", res.json().get("url"))
