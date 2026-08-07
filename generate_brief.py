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
    required_sections = ["隔夜市場速覽", "本日重點財經事項", "今日觀察"]
    missing = [section for section in required_sections if section not in content]
    if missing:
        raise RuntimeError(f"Generated HTML is missing required sections: {', '.join(missing)}")


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
  <section class="mb-section mb-overnight">
    <h3>隔夜市場速覽</h3>
    <ul>
      <li><strong>美股：</strong>四大指數漲跌互見，費半相對抗跌。</li>
      <li><strong>ADR／亞洲科技：</strong>台積電 ADR 上漲，記憶體族群承壓。</li>
      <li><strong>台指夜盤：</strong>電子權值支撐，夜盤偏多震盪。</li>
      <li><strong>商品／利率：</strong>銅價走強，市場關注債券殖利率變化。</li>
    </ul>
  </section>
  <section class="mb-section mb-events">
    <h3>本日重點財經事項</h3>
    <ol>
      <li><strong>★★★★★ 測試重大事件</strong><p>一句話交代具體事件、數字與市場意義。</p><p><strong>影響：</strong>列出受影響產業、公司與今日觀察點。</p></li>
      <li><strong>★★★★☆ 測試次重要事件</strong><p>只保留具體、可驗證且可能影響市場定價的資訊。</p><p><strong>影響：</strong>說明產業鏈與相關股票。</p></li>
    </ol>
  </section>
  <section class="mb-section mb-watchlist">
    <h3>今日觀察</h3>
    <ul>
      <li>觀察事件是否延伸至相關台股供應鏈。</li>
      <li>追蹤盤前價格、公司公告與政策細節。</li>
      <li>確認版面、連結、行距與手機顯示正常。</li>
    </ul>
  </section>
</article>'''


def build_prompt() -> str:
    now = taipei_now()
    window_start = now - dt.timedelta(hours=30)

    return f"""
你是科技基金晨會的資深市場編輯。這是無人值守排程，不是對話。

現在台北時間：{now.strftime('%Y/%m/%d %H:%M')}
主要搜尋區間：{window_start.strftime('%Y/%m/%d %H:%M')} 至 {now.strftime('%Y/%m/%d %H:%M')}（台北時間）。
若事件發生較早、但在此區間出現重大新進展，也可納入，並明確寫出新進展。

你的任務不是寫市場作文，而是回答：
1. 隔夜市場發生了什麼？
2. 今天有哪些「具體事件」可能影響美股、台股、AI、半導體或重要產業定價？
3. 今天交易時段需要追蹤什麼？

【事件的定義】
合格事件必須至少包含一個可核實的主體與動作，例如：公司發布產品、併購、IPO 定價、增發債券、關稅或法規生效、財測修正、供應鏈規格改變、ETF 上市、商品價格突破、重大資本支出或重要財報。
「市場觀望」「投資人等待」「風險偏好改變」「漲跌互見」不是事件，不能單獨列入重點事項。

【選題優先順序】
- AI、GPU、ASIC、HBM、記憶體、光通訊、資料中心、半導體設備與先進封裝。
- 美國、中國、台灣的關稅、出口管制、產業政策與監管變化。
- 大型科技公司、重要供應鏈公司的併購、融資、財測、產品規格與資本支出。
- 重大 IPO、ETF 上市、公司債發行、資金流向與商品價格突破。
- 對台積電及台灣科技供應鏈可能有直接影響的海外公司消息。

【重要性評分】
★★★★★：可能影響整個市場、產業鏈或大型權值股定價。
★★★★☆：可能明顯影響特定產業或多家相關公司。
★★★☆☆：值得今日追蹤，但影響較集中或仍待確認。
低於三星不要寫。最多 10 則；真正重要事件不足時可以少於 10 則，禁止湊數。

【搜尋與查證】
- 必須主動搜尋多個可信來源，不得只依賴單一入口或只看 Yahoo Finance。
- 優先採用公司公告、監管機構、交易所、政府文件與具編採責任的財經媒體。
- 每則事件至少要有一個可點擊來源；重大或有爭議事件應盡量交叉查證。
- 若標題中的關鍵數字、交易金額、關稅稅率、定價或漲跌幅無法可靠確認，就不要寫入。
- 嚴禁把傳聞寫成已確定事實；傳聞必須標示「據報」「傳出」或「消息人士稱」。

【寫作規則】
- 使用繁體中文，句子短，資訊密度高。
- 先寫具體事實，再寫市場影響；不要用空泛評論填充。
- 每則事件標題必須包含公司、機構、商品或政策名稱，不可只寫「AI 需求升溫」這類抽象標題。
- 每則事件需盡量保留關鍵數字，例如價格、金額、稅率、估值、漲跌幅或時間。
- 「影響」需點出相關產業，以及有明確關聯的美股、ADR 或台股公司；不可硬湊股票。
- 不提供買賣建議，不預測必然上漲或下跌。
- 不要另外設置高盛、大摩、SemiAnalysis 或 Yahoo 專區；只有其內容本身夠重要時才列為事件。

【禁止用語】
除非後面立刻接具體事實，否則不要使用：「市場觀望」「投資人等待」「漲跌互見」「受到多重因素影響」「風險偏好」「市場情緒」「仍須觀察」「整體而言」「未來可能」「分析師認為」。

【HTML 絕對規則】
- 直接輸出可嵌入 WordPress 的 HTML，不准輸出 Markdown、code fence、流程說明或對話內容。
- 最外層必須是 <article class="mb-brief">；不要輸出 <html>、<head>、<body>。
- 不要使用表格。
- 連結格式只能是 <a href="..." target="_blank" rel="noopener noreferrer">來源名稱</a>。
- 來源放在相關項目末尾，不要另做來源清單。

請嚴格使用以下結構：

<article class="mb-brief">
  <header class="mb-header">
    <p class="mb-kicker">每日市場早報</p>
    <h2>每日市場早報｜{now.strftime('%Y/%m/%d')}</h2>
    <p class="mb-updated">更新時間：{now.strftime('%Y/%m/%d %H:%M')} 台北時間</p>
  </header>
  <section class="mb-section mb-overnight">
    <h3>隔夜市場速覽</h3>
    <ul>
      <li><strong>美股：</strong>用一句話列出道瓊、標普 500、那斯達克、費半的方向與必要漲跌幅，並點出最直接原因。</li>
      <li><strong>ADR／亞洲科技：</strong>優先列出台積電 ADR、重要半導體或記憶體公司具體表現；沒有顯著變化可省略個股。</li>
      <li><strong>台指夜盤：</strong>列出夜盤方向與漲跌幅，並用短句說明主要連動因素。</li>
      <li><strong>商品／利率／匯率：</strong>只列當日有明顯異動或與科技股相關的項目。</li>
    </ul>
  </section>
  <section class="mb-section mb-events">
    <h3>本日重點財經事項</h3>
    <ol>
      <li>
        <strong>★★★★★ 具體事件標題（保留關鍵數字）</strong>
        <p>一至兩句說清楚誰在何時做了什麼，以及已確認的關鍵細節。</p>
        <p><strong>影響：</strong>說明對產業與具關聯公司的可能影響，以及今天要追蹤的確認點。 <a href="可靠來源網址" target="_blank" rel="noopener noreferrer">來源名稱</a></p>
      </li>
    </ol>
  </section>
  <section class="mb-section mb-watchlist">
    <h3>今日觀察</h3>
    <ul>
      <li>列出 3 至 5 個今天可被驗證的追蹤事項，例如公司公告、政策細節、盤前價格、財報、法說或經濟數據。</li>
    </ul>
  </section>
</article>

輸出前自行檢查：重點事項是否都是具體事件、是否依影響程度排序、是否保留重要數字、是否刪除重複與空泛句子、是否每則都有來源。
""".strip()

def generate_with_openai(model: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY. Put it in .env locally or GitHub Secrets.")

    client = OpenAI(
      api_key=api_key,
      timeout=600.0,
      max_retries=2,
    )

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
