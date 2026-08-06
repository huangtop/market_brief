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
            "Run again, or switch OPENAI_MODEL to gpt-5-mini."
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
    <h3>台股漲跌原因</h3>
    <p>台股漲跌原因：這是離線測試內容。正式版只會整理台股漲跌背後的原因，例如美股科技股影響、外資與融資動向、AI/半導體族群、台積電與電子權值股、匯率或總經因素，不會列出指數點位、漲跌點數或漲跌幅。</p>
  </section>

  <section class="mb-section">
    <h3>台指期漲跌原因</h3>
    <p>台指期漲跌原因：本段為測試資料。正式版會整理台指期與現貨、美股期貨、半導體股、外資期貨部位及夜盤情緒的連動原因，不會列出期貨點位或漲跌點數。</p>
  </section>

  <section class="mb-section">
    <h3>美股漲跌原因</h3>
    <p>美股漲跌原因：這是測試段落。正式版會用一段文字整理市場分化、財報、AI 投資、利率、油價與風險偏好變化，不會列出 Dow Jones、S&amp;P 500、Nasdaq 的點位與漲跌點數。 <a href="https://apnews.com/" target="_blank" rel="noopener noreferrer">AP</a></p>
  </section>

  <section class="mb-section">
    <h3>高盛／大摩／SemiAnalysis</h3>
    <ul>
      <li>高盛：測試清單項目。</li>
      <li>大摩：測試清單項目。</li>
      <li>SemiAnalysis：測試清單項目。</li>
    </ul>
  </section>

  <section class="mb-section">
    <h3>Yahoo 財經 AI 重點新聞</h3>
    <ul>
      <li>測試清單項目。正式版會整理 Yahoo Finance / Yahoo 財經 AI 相關新聞與市場影響。</li>
    </ul>
  </section>

  <section class="mb-section">
    <h3>今日觀察重點</h3>
    <ul>
      <li>測試觀察重點。</li>
      <li>確認版面、連結、行距與手機顯示正常。</li>
    </ul>
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
- 不要輸出 <html>、<head>、<body>。
- 使用繁體中文。
- 不提供投資建議。
- 不要列出台股加權指數的收盤點位、漲跌點數、漲跌幅。
- 不要列出台指期的收盤點位、漲跌點數、漲跌幅。
- 不要列出美股三大指數的收盤點位、漲跌點數、漲跌幅。
- 不要使用表格。
- 重點只整理「漲跌原因、資金情緒、AI 與半導體主線、重要機構與新聞觀點」。
- 如果某項資料查不到，請寫「未取得可靠公開資訊」，不要杜撰。
- 每段最多 2 到 4 句，避免冗長。
- 連結必須使用 <a href="..." target="_blank" rel="noopener noreferrer">來源名稱</a>。
- 不准輸出像 ([來源](https://...)) 的 Markdown 連結。
- 來源連結請放在相關段落最後，不要另外做來源清單。

請整理：
1. 台股漲跌原因：只寫原因，不寫指數點位、漲跌點數、漲跌幅。
2. 台指期漲跌原因：只寫原因，不寫期貨點位、漲跌點數、漲跌幅。
3. 美股漲跌原因：只寫原因，不寫 Dow Jones、S&P 500、Nasdaq 的點位、漲跌點數、漲跌幅。
4. 高盛／大摩／SemiAnalysis：如果有 Goldman Sachs / 高盛、Morgan Stanley / 大摩、SemiAnalysis 相關 AI、半導體、GPU、HBM、資料中心、總經或市場新聞，請整理；若沒有新消息，明確寫沒有重大新消息。
5. Yahoo 財經 AI 重點新聞：彙整 Yahoo Finance / Yahoo 財經上的 AI 重點新聞，只寫重點與影響。
6. 今日觀察重點：列出 3 到 5 點。

請依照此固定 HTML 結構輸出，但內容請用你查到的最新資料替換：

<article class="mb-brief">
  <header class="mb-header">
    <p class="mb-kicker">每日市場早報</p>
    <h2>每日市場早報｜{now.strftime('%Y/%m/%d')}</h2>
    <p class="mb-updated">更新時間：{now.strftime('%Y/%m/%d %H:%M')} 台北時間</p>
  </header>

  <section class="mb-section">
    <h3>台股漲跌原因</h3>
    <p>台股漲跌原因：請用一段文字整理台股昨日漲跌原因，不要列出任何指數點位、漲跌點數或漲跌幅。請聚焦美股影響、外資與融資動向、AI/半導體族群、台積電與電子權值股、匯率或總經因素。</p>
  </section>

  <section class="mb-section">
    <h3>台指期漲跌原因</h3>
    <p>台指期漲跌原因：請用一段文字整理台指期昨日漲跌原因，不要列出任何期貨點位、漲跌點數或漲跌幅。請聚焦現貨連動、美股期貨、半導體股、外資期貨部位與夜盤情緒。</p>
  </section>

  <section class="mb-section">
    <h3>美股漲跌原因</h3>
    <p>美股漲跌原因：請用一段文字整理美股昨日漲跌原因，不要列出 Dow Jones、S&amp;P 500、Nasdaq 的點位、漲跌點數或漲跌幅。請聚焦財報、AI 投資、利率、油價、地緣政治與市場風險偏好。</p>
  </section>

  <section class="mb-section">
    <h3>高盛／大摩／SemiAnalysis</h3>
    <ul>
      <li>高盛：整理 Goldman Sachs / 高盛相關 AI、半導體、總經或市場觀點；若沒有新消息，請寫沒有重大新消息。</li>
      <li>大摩：整理 Morgan Stanley / 大摩相關 AI、半導體、總經或市場觀點；若沒有新消息，請寫沒有重大新消息。</li>
      <li>SemiAnalysis：整理 SemiAnalysis 相關 AI、GPU、HBM、資料中心或供應鏈觀點；若沒有新消息，請寫沒有重大新消息。</li>
    </ul>
  </section>

  <section class="mb-section">
    <h3>Yahoo 財經 AI 重點新聞</h3>
    <ul>
      <li>整理 Yahoo Finance / Yahoo 財經 AI 重點新聞與市場影響。</li>
    </ul>
  </section>

  <section class="mb-section">
    <h3>今日觀察重點</h3>
    <ul>
      <li>列出今日需要觀察的市場重點。</li>
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


def write_outputs(content: str) -> tuple[Path, Path]:
    """
    Always write BOTH:
    1. data/latest.html
    2. data/archive/YYYY-MM-DD.html
    """
    now = taipei_now()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    archive_path = ARCHIVE_DIR / f"{now.strftime('%Y-%m-%d')}.html"
    clean_content = content.strip() + "\n"

    LATEST_PATH.write_text(clean_content, encoding="utf-8")
    archive_path.write_text(clean_content, encoding="utf-8")

    print(f"Wrote latest:  {LATEST_PATH}")
    print(f"Wrote archive: {archive_path}")

    return LATEST_PATH, archive_path


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
