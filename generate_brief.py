#!/usr/bin/env python3
"""
Generate a daily market brief as HTML for WordPress shortcode display.

Local:
  cp .env.example .env
  python3 generate_brief.py --offline-sample
  python3 generate_brief.py --generate-only

GitHub Actions:
  Runs daily, writes data/latest.html and data/archive/YYYY-MM-DD.html,
  then commits those generated files back to the repo.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
LATEST_PATH = DATA_DIR / "latest.html"
TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Taipei")

DEFAULT_MODEL = "gpt-5-mini"

FORBIDDEN_PHRASES = [
    "請確認",
    "是否要我",
    "我準備開始",
    "我可以幫你",
    "請問",
    "若有其他",
    "請一併告知",
    "要我現在開始",
]


def taipei_now() -> dt.datetime:
    return dt.datetime.now(TAIPEI_TZ)


def strip_code_fences(text: str) -> str:
    text = text.strip()
    # Remove ```html ... ``` or ``` ... ``` if model returns fences.
    text = re.sub(r"^```(?:html)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def convert_markdown_links_to_html(content: str) -> str:
    """Convert Markdown links that sometimes appear inside generated HTML."""
    pattern = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

    def repl(match: re.Match) -> str:
        label = html.escape(match.group(1), quote=False)
        url = html.escape(clean_url(match.group(2)), quote=True)
        return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'

    return pattern.sub(repl, content)


def clean_url(url: str) -> str:
    """Remove common tracking parameters from a URL."""
    try:
        parts = urlsplit(url)
        query = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
        ]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except Exception:
        return url


def remove_tracking_params_from_html(content: str) -> str:
    """Clean href URLs in HTML."""
    def repl(match: re.Match) -> str:
        quote = match.group(1)
        url = match.group(2)
        return f'href={quote}{html.escape(clean_url(url), quote=True)}{quote}'

    return re.sub(r'href=(["\'])(https?://.*?)(\1)', repl, content)


def ensure_article_shell(content: str) -> str:
    content = content.strip()
    if "<article" in content and "mb-brief" in content:
        return content
    now = taipei_now()
    return f'''<article class="mb-brief">
  <header class="mb-header">
    <p class="mb-kicker">每日市場早報</p>
    <h2>每日市場早報｜{now.strftime('%Y/%m/%d')}</h2>
    <p class="mb-updated">更新時間：{now.strftime('%Y/%m/%d %H:%M')} 台北時間</p>
  </header>
  <section class="mb-section">
    {content}
  </section>
</article>'''


def validate_html(content: str) -> None:
    if "<article" not in content or "mb-brief" not in content:
        raise RuntimeError("Generated HTML does not contain <article class=\"mb-brief\">.")
    if any(phrase in content for phrase in FORBIDDEN_PHRASES):
        raise RuntimeError(
            "Model returned a confirmation-style response instead of the report. "
            "Run again, or switch OPENAI_MODEL to gpt-5."
        )
    if "```" in content:
        raise RuntimeError("Generated HTML still contains code fences.")


def post_process(content: str) -> str:
    content = strip_code_fences(content)
    content = convert_markdown_links_to_html(content)
    content = remove_tracking_params_from_html(content)
    content = ensure_article_shell(content)
    validate_html(content)
    return content.strip() + "\n"


def offline_sample_html() -> str:
    now = taipei_now()
    return f'''<article class="mb-brief">
  <header class="mb-header">
    <p class="mb-kicker">每日市場早報</p>
    <h2>每日市場早報｜本機測試樣板</h2>
    <p class="mb-updated">更新時間：本機測試｜{now.strftime('%Y/%m/%d %H:%M')} 台北時間</p>
  </header>
  <section class="mb-section">
    <h3>台股</h3>
    <p>這是離線測試內容，用來確認 WordPress shortcode 與 CSS 顯示正常。</p>
  </section>
  <section class="mb-section">
    <h3>台指期</h3>
    <p>本段為測試資料。</p>
  </section>
  <section class="mb-section">
    <h3>美股</h3>
    <table class="mb-table">
      <thead><tr><th>指數</th><th>收盤</th><th>漲跌點數</th><th>漲跌幅</th></tr></thead>
      <tbody><tr><td>Nasdaq</td><td>測試</td><td>測試</td><td>測試</td></tr></tbody>
    </table>
  </section>
  <section class="mb-section">
    <h3>Yahoo 財經 AI 重點新聞</h3>
    <ul><li>測試清單項目。</li></ul>
  </section>
</article>'''


def build_prompt() -> str:
    now = taipei_now()
    yesterday = now - dt.timedelta(days=1)
    return f"""
你是無人值守的每日財經早報產生器。這是排程任務，不是對話。

今天台北時間：{now.strftime('%Y/%m/%d %H:%M')}
請整理「昨天」：{yesterday.strftime('%Y/%m/%d')} 的市場資料。

絕對規則：
- 不准問問題。
- 不准請使用者確認。
- 不准說「我準備開始」、「請確認是否要我現在開始」、「我可以幫你」。
- 不准輸出流程說明。
- 不准輸出 Markdown。
- 不准輸出 ```html 或任何 code fence。
- 必須直接輸出可嵌入 WordPress 的 HTML。
- HTML 最外層必須是 <article class="mb-brief">。
- 如果某項資料查不到，請寫「未取得可靠公開數據」，不要杜撰。
- 不提供投資建議。
- 連結必須使用 <a href="..." target="_blank" rel="noopener noreferrer">來源名稱</a>。
- 不准輸出像 ([來源](https://...)) 的 Markdown 連結。
- 不要輸出 <html>、<head>、<body>。
- 使用繁體中文。

請整理：
1. 台股加權指數：收盤點位、漲跌點數、漲跌幅、漲跌原因。
2. 台指期：日盤或夜盤收盤點位、漲跌點數、漲跌幅、漲跌原因。
3. 美股三大指數：Dow Jones、S&P 500、Nasdaq 的收盤點位、漲跌點數、漲跌幅、漲跌原因。
4. 如果有 Goldman Sachs / 高盛、Morgan Stanley / 大摩、SemiAnalysis 相關 AI、半導體、GPU、HBM、資料中心、總經或市場新聞，請整理；若沒有新消息，明確寫沒有重大新消息。
5. 彙整 Yahoo Finance / Yahoo 財經上的相關 AI 重點新聞。
6. 最後列出今日觀察重點。

請依照此固定 HTML 結構輸出，但內容請用你查到的最新資料替換：

<article class="mb-brief">
  <header class="mb-header">
    <p class="mb-kicker">每日市場早報</p>
    <h2>每日市場早報｜{now.strftime('%Y/%m/%d')}</h2>
    <p class="mb-updated">更新時間：{now.strftime('%Y/%m/%d %H:%M')} 台北時間</p>
  </header>

  <section class="mb-section">
    <h3>台股</h3>
    <p>加權指數收盤點位、漲跌點數、漲跌幅與原因。</p>
  </section>

  <section class="mb-section">
    <h3>台指期</h3>
    <p>台指期收盤點位、漲跌點數、漲跌幅與原因。</p>
  </section>

  <section class="mb-section">
    <h3>美股</h3>
    <table class="mb-table">
      <thead>
        <tr><th>指數</th><th>收盤</th><th>漲跌點數</th><th>漲跌幅</th></tr>
      </thead>
      <tbody>
        <tr><td>Dow Jones</td><td></td><td></td><td></td></tr>
        <tr><td>S&amp;P 500</td><td></td><td></td><td></td></tr>
        <tr><td>Nasdaq</td><td></td><td></td><td></td></tr>
      </tbody>
    </table>
    <p>美股漲跌原因。</p>
  </section>

  <section class="mb-section">
    <h3>高盛／大摩／SemiAnalysis</h3>
    <ul>
      <li>高盛相關整理。</li>
      <li>大摩相關整理。</li>
      <li>SemiAnalysis 相關整理。</li>
    </ul>
  </section>

  <section class="mb-section">
    <h3>Yahoo 財經 AI 重點新聞</h3>
    <ul>
      <li>Yahoo Finance / Yahoo 財經 AI 重點新聞。</li>
    </ul>
  </section>

  <section class="mb-section">
    <h3>今日觀察重點</h3>
    <ul>
      <li>今日需要觀察的市場重點。</li>
    </ul>
  </section>
</article>
""".strip()


def generate_with_openai(model: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY. Put it in .env locally or GitHub Secrets.")

    client = OpenAI(api_key=api_key, timeout=240.0)

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "developer",
                "content": [
                    {
                        "type": "input_text",
                        "text": "你是排程任務執行器。必須直接完成任務，不得提問、不得請確認、不得輸出對話式回覆。",
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": build_prompt(),
                    }
                ],
            },
        ],
        tools=[
            {
                "type": "web_search",
                "search_context_size": "low",
                "user_location": {
                    "type": "approximate",
                    "country": "TW",
                    "city": "Taipei",
                    "timezone": "Asia/Taipei",
                },
            }
        ],
        tool_choice="required",
    )

    return post_process(response.output_text)


def write_outputs(content: str) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    today = taipei_now().date().isoformat()
    archive_path = ARCHIVE_DIR / f"{today}.html"

    LATEST_PATH.write_text(content, encoding="utf-8")
    archive_path.write_text(content, encoding="utf-8")

    print(f"Wrote: {LATEST_PATH}")
    print(f"Wrote: {archive_path}")
    print("\n--- HTML preview ---\n")
    print(content[:2500])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline-sample", action="store_true", help="Write sample HTML without calling OpenAI.")
    parser.add_argument("--generate-only", action="store_true", help="Generate HTML and write data files. Same as default.")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    if args.offline_sample:
        content = post_process(offline_sample_html())
        write_outputs(content)
        return 0

    content = generate_with_openai(model)
    write_outputs(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
