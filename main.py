import os
import datetime
import requests
from google import genai

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)
prompt = (
    "오늘자 가장 이슈가 되고 있는 경제/부동산 주요 뉴스 TOP 5를 요약하고, "
    "각 항목별 핵심 쟁점과 반응을 깔끔한 마크다운 형식으로 작성해줘."
)

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt,
)
content_text = response.text

today = datetime.datetime.now().strftime("%Y-%m-%d")
url = "https://api.notion.com/v1/pages"
headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

payload = {
    "parent": {"database_id": DATABASE_ID},
    "properties": {
        "제목": {"title": [{"text": {"content": f"[{today}] 일간 주요 경제 이슈 TOP 5"}}]},
        "날짜": {"date": {"start": today}}
    },
    "children": [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"text": {"content": f"📌 {today} 브리핑 리포트"}}]}
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"text": {"content": content_text[:1900]}}]}
        }
    ]
}

res = requests.post(url, headers=headers, json=payload)
if res.status_code == 200:
    print("성공적으로 노션에 업로드되었습니다.")
else:
    print(f"업로드 실패 ({res.status_code}): {res.text}")
