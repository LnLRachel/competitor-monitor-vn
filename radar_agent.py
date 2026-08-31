"""
radar_agent.py - 베트남 버전
구글 뉴스 RSS 수집 → 본문 크롤링 → Claude 요약 → 이메일 발송
"""

import os
import re
import time
import smtplib
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ============================================================
# 설정
# ============================================================

OUR_PRODUCT = """
[BW - Tumbler/Bottle] Metro, Bly Soul
[SA - Small/Beauty/Oral Appliances] Lock&Lock SA lineup
"""

BRAND_CAT = {
    # BW 텀블러/보틀
    "Starbucks bình giữ nhiệt":      "BW",
    "Thermos bình giữ nhiệt":        "BW",
    "Stanley bình giữ nhiệt":        "BW",
    "Zebra bình giữ nhiệt":          "BW",
    "Rạng Đông bình giữ nhiệt":      "BW",
    "Tiger bình giữ nhiệt":          "BW",
    "Yui Tan bình giữ nhiệt":        "BW",
    "Elmich bình giữ nhiệt":         "BW",
    # SA 소형·미용·구강가전
    "Philips đồ gia dụng":           "SA",
    "Sunhouse đồ gia dụng":          "SA",
    "Bear máy":                      "SA",
    "Elmich đồ gia dụng":            "SA",
    "Tefal đồ gia dụng":             "SA",

CAT_NAME = {
    "BW": "Tumbler / Bottle",
    "SA": "소형·미용·구강가전",
}

EXCLUDE_KW = [
    "stock", "shares", "recruit", "hiring", "insurance",
    "chứng khoán", "tuyển dụng", "bảo hiểm",
]

STRATEGY_KW = [
    "new product", "launch", "ra mắt", "sản phẩm mới",
    "promotion", "khuyến mãi", "campaign", "chiến dịch",
    "pop-up", "collaboration", "collab", "hợp tác",
    "price", "giá", "sale", "discount", "giảm giá",
    "marketing", "strategy", "chiến lược",
]

GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_PASS = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT  = os.environ["RECIPIENT_EMAIL"]
API_KEY    = os.environ["ANTHROPIC_API_KEY"]

# ============================================================
# 1단계: 구글 뉴스 RSS 수집 (베트남)
# ============================================================

def collect_news() -> dict[str, list[dict]]:
    print("\n▶ [1단계] 구글 뉴스 RSS 수집 (베트남)")
    headers = {"User-Agent": "Mozilla/5.0"}
    brand_articles: dict[str, list[dict]] = {b: [] for b in BRAND_CAT}

    for brand in BRAND_CAT:
        rss = (
            f"https://news.google.com/rss/search"
            f"?q={requests.utils.quote(brand)}+when:7d&hl=en&gl=VN&ceid=VN:en"
        )
        try:
            res  = requests.get(rss, headers=headers, timeout=10)
            soup = BeautifulSoup(res.content, "xml")

            for item in soup.find_all("item"):
                title    = item.title.text.strip()
                link     = item.link.text.strip()
                pub_date = item.pubDate.text.strip() if item.pubDate else ""

                if any(kw.lower() in title.lower() for kw in EXCLUDE_KW):
                    continue
                #if not any(kw.lower() in title.lower() for kw in STRATEGY_KW):
                #    continue

                brand_articles[brand].append({
                    "title": title, "link": link, "pub_date": pub_date
                })

            print(f"  ✔ {brand}: {len(brand_articles[brand])}건")

        except Exception as e:
            print(f"  ⚠ {brand} 오류: {e}")

        time.sleep(0.2)

    return brand_articles


# ============================================================
# 2단계: 기사 본문 크롤링
# ============================================================

def fetch_body(url: str) -> str:
    try:
        res  = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8, allow_redirects=True)
        soup = BeautifulSoup(res.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        for selector in ["article", "main", ".article-body", "p"]:
            el = soup.select(selector)
            if el:
                text = re.sub(r"\s+", " ", " ".join(e.get_text(" ", strip=True) for e in el)).strip()
                if len(text) > 200:
                    return text[:2000]
    except Exception:
        pass
    return ""


# ============================================================
# 3단계: Claude 요약
# ============================================================

API_URL = "https://api.anthropic.com/v1/messages"
API_HEADERS = {
    "x-api-key": API_KEY,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}
MODEL      = "claude-sonnet-4-5"
MAX_TOKENS = 600

SYSTEM_PROMPT = f"""You are a competitive intelligence analyst for Lock&Lock Vietnam.

[Lock&Lock Products]
{OUR_PRODUCT}

Analyze competitor news and summarize in the format below (respond in Korean).
Only include strategically meaningful content.
If nothing notable, output exactly: NO_UPDATE

Format:
• 주요 동향: (2~3문장. 신제품/마케팅/가격/전략 중심)
• 락앤락 시사점: (베트남 시장 관점에서 자사 제품에 주목할 포인트 1문장)

No greetings or meta-commentary."""


def summarize_brand(brand: str, articles: list[dict]) -> str | None:
    if not articles:
        return None

    articles_text = ""
    for i, a in enumerate(articles[:5]):
        body = fetch_body(a["link"])
        articles_text += f"\n[Article {i+1}] {a['title']}\n"
        if body:
            articles_text += f"{body[:800]}\n"
        articles_text += f"Link: {a['link']}\n"

    try:
        resp = requests.post(
            API_URL, headers=API_HEADERS,
            json={
                "model": MODEL, "max_tokens": MAX_TOKENS,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": f"Brand: {brand}\n\n{articles_text}"}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"].strip()
        return None if "NO_UPDATE" in text else text
    except Exception as e:
        print(f"  ⚠ {brand} 요약 오류: {e}")
        return None


def summarize_all(brand_articles: dict) -> dict[str, list[dict]]:
    print("\n▶ [2단계] Claude 요약")
    results: dict[str, list[dict]] = {cat: [] for cat in CAT_NAME}

    for brand, articles in brand_articles.items():
        cat = BRAND_CAT[brand]
        print(f"  🔎 {brand} ({len(articles)}건)...", end=" ", flush=True)
        summary = summarize_brand(brand, articles)
        if summary:
            print("동향 있음")
            results[cat].append({"brand": brand, "summary": summary, "articles": articles[:3]})
        else:
            print("특이사항 없음")
        time.sleep(0.5)

    return results


# ============================================================
# 4단계: 이메일 발송
# ============================================================

CAT_COLOR = {"BW": "#2E6DA4", "SA": "#7D3C98"}
CAT_ICON  = {"BW": "🧊", "SA": "⚡"}


def _brand_block(b: dict) -> str:
    summary_html = "".join(
        f'<div style="margin:4px 0 4px 8px;font-size:13px;color:#333;">{l}</div>'
        if l.startswith("•") else
        f'<div style="font-size:13px;color:#444;">{l}</div>'
        for l in (line.strip() for line in b["summary"].split("\n")) if l
    )
    links_html = "".join(
        f'<div style="margin:3px 0;"><a href="{a["link"]}" '
        f'style="font-size:12px;color:#2E6DA4;text-decoration:none;">'
        f'↗ {a["title"][:60]}{"..." if len(a["title"])>60 else ""}</a></div>'
        for a in b["articles"]
    )
    return f"""
    <div style="margin:10px 0;padding:14px 16px;background:#fafafa;
                border-left:3px solid #ddd;border-radius:4px;">
      <div style="font-weight:700;font-size:14px;color:#111;margin-bottom:8px;">{b['brand']}</div>
      {summary_html}
      <div style="margin-top:10px;padding-top:8px;border-top:1px solid #eee;">{links_html}</div>
    </div>"""


def build_html(results: dict) -> str:
    today    = datetime.now().strftime("%Y년 %m월 %d일")
    week_num = datetime.now().isocalendar()[1]
    total    = sum(len(v) for v in results.values())

    sections = "".join(
        f'<div style="margin:24px 0;">'
        f'<div style="background:{CAT_COLOR[cat]};color:#fff;padding:10px 18px;'
        f'border-radius:6px 6px 0 0;font-size:15px;font-weight:700;">'
        f'{CAT_ICON[cat]} {CAT_NAME[cat]}</div>'
        f'<div style="border:1px solid #e0e0e0;border-top:none;'
        f'border-radius:0 0 6px 6px;padding:12px 16px;background:#fff;">'
        f'{"".join(_brand_block(b) for b in brands)}</div></div>'
        for cat, brands in results.items() if brands
    )

    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f4f4;
             font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
  <div style="max-width:680px;margin:24px auto;background:#fff;
              border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08);">
    <div style="background:#1a1a2e;padding:28px 32px;">
      <div style="color:#fff;font-size:20px;font-weight:700;">
        🔍 베트남 경쟁 브랜드 위클리 인텔리전스
      </div>
      <div style="color:#aab;font-size:13px;margin-top:6px;">
        {today} · {week_num}주차 · 주요 동향 {total}건
      </div>
    </div>
    <div style="padding:24px 32px;">
      {'<p style="color:#666;font-size:13px;">이번 주 주목할 경쟁 브랜드 동향이 없습니다.</p>' if total==0 else sections}
    </div>
    <div style="background:#f8f8f8;padding:16px 32px;border-top:1px solid #eee;
                font-size:11px;color:#aaa;text-align:center;">
      Lock&Lock Vietnam 경쟁 모니터링 시스템 · 자동 발송
    </div>
  </div>
</body></html>"""


def send_email(results: dict) -> None:
    total      = sum(len(v) for v in results.values())
    week_num   = datetime.now().isocalendar()[1]
    subject    = f"[VN 경쟁브랜드 위클리] {datetime.now().strftime('%Y.%m.%d')} {week_num}주차 · {total}건"
    recipients = [r.strip() for r in RECIPIENT.split(",")]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(build_html(results), "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_PASS)
        s.sendmail(GMAIL_USER, recipients, msg.as_string())
    print(f"\n✅ 이메일 발송 → {', '.join(recipients)} ({total}건)")


# ============================================================
# 메인
# ============================================================

def main():
    print(f"\n{'='*55}")
    print(f" 베트남 경쟁 브랜드 모니터링 시작: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")
    brand_articles = collect_news()
    results        = summarize_all(brand_articles)
    send_email(results)
    print(f"\n{'='*55}")
    print(" ✅ 완료")
    print(f"{'='*55}\n")

if __name__ == "__main__":
    main()
