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
    required_sections = ["隔夜市場速覽", "昨夜科技資金輪動", "本日重點財經事項", "未來48小時催化劑", "今日觀察"]
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
    <h2>每日市場早報｜本機版面測試</h2>
    <p class="mb-updated">更新時間：{now.strftime('%Y/%m/%d %H:%M')} 台北時間</p>
  </header>
  <section class="mb-section mb-overnight">
    <h3>隔夜市場速覽</h3>
    <ul><li>本機版面測試內容，不代表即時市場資料。</li></ul>
  </section>
  <section class="mb-section mb-events">
    <h3>本日重點財經事項</h3>
    <ol><li><strong>★★★☆☆ 範例事件</strong><p>僅用於驗證 HTML 結構。</p></li></ol>
  </section>
  <section class="mb-section mb-rotation">
    <h3>昨夜科技資金輪動</h3>
    <ol><li><strong>範例族群</strong><p>僅用於驗證 HTML 結構。</p></li></ol>
  </section>
  <section class="mb-section mb-catalysts">
    <h3>未來48小時催化劑</h3>
    <ul><li>僅用於驗證 HTML 結構。</li></ul>
  </section>
  <section class="mb-section mb-watchlist">
    <h3>今日觀察</h3>
    <ul><li>僅用於驗證 HTML 結構。</li></ul>
  </section>
</article>'''


def build_prompt() -> str:
    now = taipei_now()
    prior_session_start = now - dt.timedelta(hours=36)
    earnings_start = (now - dt.timedelta(days=7)).date()
    earnings_end = (now + dt.timedelta(days=7)).date()

    return f"""
你是科技基金晨會的資深市場編輯。這是無人值守排程，不是對話。

現在台北時間：{now.strftime('%Y/%m/%d %H:%M')}
財報候選池日期：{earnings_start.isoformat()} 至 {earnings_end.isoformat()}。
昨日資金輪動搜尋區間：最近一個完整美股交易日；必要時回看至 {prior_session_start.strftime('%Y/%m/%d %H:%M')}（台北時間）以取得收盤後消息。

【重要：以下為內部工作流程，不得出現在輸出 HTML】

第一步｜建立最近 ±7 天重要事件候選池
A. 財報：搜尋過去7天已公布、今日公布、未來7天即將公布的公司；不限 Mega Cap。
只要公司與以下任一主題高度相關即可納入：AI、半導體、GPU、ASIC、CPU、Memory、HBM、DRAM、NAND、Networking、Optical Networking、Photonics、CPO、Transceiver、AI Infrastructure、Cloud、Server、PCB、Power、Cooling、Foundry。
每家公司在內部候選池紀錄：company、ticker、earnings date、before open / after close、是否具 read-through、是否影響台灣供應鏈。
財報日期與盤前/盤後時間必須優先用公司 IR、交易所或其他第一方來源核實；若無法確認時段，不得猜測。

B. 昨日異常族群：搜尋最近一個完整美股交易日，找出科技產業中同步漲跌最明顯、真正主導科技股資金流向的 1~3 個族群。
候選包括但不限於：光通訊、HBM、Memory、ASIC、Networking、AI Infrastructure、Cloud、Server、PCB、Cooling、Power、Foundry。
每個族群內部紀錄：族群名稱、代表公司、代表公司漲跌幅、ETF（若有）、ETF 漲跌幅（若可可靠取得）、異動原因。
「異常族群」必須是多檔同方向或 ETF/產業指數同步確認，不能因單一股票大漲大跌就判定整個族群。

C. 今日重大公司級／政策級事件：搜尋今日與未來48小時內可能影響交易的重要事件，包括但不限於新產品、Roadmap、新 GPU、AI 晶片、新記憶體、IPO、M&A、融資、Convertible、Analyst Upgrade/Downgrade、政策、關稅、制裁、AI 法規。
HBF、OpenRouter、Rubin Ultra、Trump 232、Google 發債、Unitree IPO 只是案例，不得因案例名稱而硬塞；只有當日有新增且具交易影響力的進展才納入。

第二步｜合併與排序
不要依新聞熱度排序。對所有候選事件依下列順序綜合排序：
1. 今日交易影響力
2. 對 AI / 科技供應鏈影響
3. 是否具 read-through
4. 是否與昨日資金輪動有關
5. 是否為未來48小時催化劑

硬性規則：若「昨日某族群明顯同步下跌」且「今日有該族群龍頭財報」，該財報事件必須列為 ★★★★★，並明確連結昨夜族群價格行為與今日催化劑。

第三步｜來源與交叉查證
- 必須主動搜尋多個可信來源，不得只依賴單一入口或只看 Yahoo Finance。
- 優先採用公司 IR、SEC/監管機構、交易所、政府文件與具編採責任的財經媒體。
- 每則輸出的重大事件至少一個可點擊來源；重大或有爭議事件盡量交叉查證。
- 關鍵數字、交易金額、關稅稅率、財報日期/時段、定價、漲跌幅無法可靠確認時，不得寫成確定值。
- 傳聞必須標示「據報」「傳出」或「消息人士稱」。

【正文任務】
回答：
1. 隔夜市場發生了什麼？
2. 昨晚真正主導科技股資金流向的是哪 1~3 個族群？
3. 今天有哪些具體事件最可能影響美股、台股、AI、半導體或重要供應鏈定價？
4. 未來48小時有哪些值得盯的催化劑？

【重要性評分】
★★★★★：可能影響整個市場、產業鏈、大型權值股，或符合「昨日族群明顯同步下跌＋今日龍頭財報」硬性規則。
★★★★☆：可能明顯影響特定產業或多家相關公司。
★★★☆☆：值得今日追蹤，但影響較集中或仍待確認。
低於三星不要寫。重點事件最多 10 則；不足時可以少於 10 則，禁止湊數。

【寫作規則】
- 使用繁體中文，句子短，資訊密度高。
- 先寫具體事實，再寫市場影響；不要用空泛評論填充。
- 不要固定列出台積電 ADR。只有它本身當日有顯著價格異動、公司消息或明確 read-through 時才列。
- 每個「昨夜科技資金輪動」族群都必須列：代表公司、漲跌幅、異動原因；ETF 若有且資料可靠則一併列出。
- 財報事件必須寫出「日期＋盤前/盤後」；若公司 IR 已確認時段，必須保留。
- 每則事件標題必須包含公司、機構、商品、政策或清楚族群名稱，不可只寫抽象敘述。
- 每則事件盡量保留關鍵數字，例如價格、金額、稅率、估值、漲跌幅或時間。
- 「影響」需點出相關產業，以及有明確關聯的美股或台灣供應鏈公司；不可硬湊股票。
- 不提供買賣建議，不預測必然上漲或下跌。

【禁止用語】
除非後面立刻接具體事實，否則不要使用：「市場觀望」「投資人等待」「漲跌互見」「受到多重因素影響」「風險偏好」「市場情緒」「仍須觀察」「整體而言」「未來可能」「分析師認為」。

【HTML 絕對規則】
- 只輸出可嵌入 WordPress 的 HTML，不准輸出 Markdown、code fence、流程說明、候選池、搜尋紀錄、內部排序規則或對話內容。
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
      <li><strong>美股：</strong>用一句話列出道瓊、標普 500、那斯達克、費半方向與必要漲跌幅，並點出最直接原因。</li>
      <li><strong>科技風格：</strong>只寫當晚真正有意義的科技內部強弱分化；禁止固定點名台積電 ADR。</li>
      <li><strong>台指夜盤：</strong>若資料可靠，列夜盤方向與漲跌幅，並用短句說明主要連動因素。</li>
      <li><strong>商品／利率／匯率：</strong>只列當日有明顯異動或與科技股相關的項目。</li>
    </ul>
  </section>

  <section class="mb-section mb-rotation">
    <h3>昨夜科技資金輪動</h3>
    <ol>
      <li>
        <strong>族群名稱｜上漲/下跌 X%</strong>
        <p><strong>代表公司：</strong>公司A +X.X%、公司B -X.X%、公司C -X.X%。若有可靠 ETF，補充 ETF 名稱與漲跌幅。</p>
        <p><strong>異動原因：</strong>用一至兩句寫出同步異動的直接催化劑、估值/資金輪動背景或公司事件，並附來源。</p>
      </li>
    </ol>
  </section>

  <section class="mb-section mb-events">
    <h3>本日重點財經事項</h3>
    <ol>
      <li>
        <strong>★★★★★ 具體事件標題（保留關鍵數字）</strong>
        <p>一至兩句說清楚誰在何時做了什麼，以及已確認的關鍵細節。財報類必須包含公布日期與盤前/盤後。</p>
        <p><strong>影響：</strong>說明對產業、read-through、台灣供應鏈與今日交易的可能影響，以及今天要追蹤的確認點。 <a href="可靠來源網址" target="_blank" rel="noopener noreferrer">來源名稱</a></p>
      </li>
    </ol>
  </section>

  <section class="mb-section mb-catalysts">
    <h3>未來48小時催化劑</h3>
    <ul>
      <li>列 3 至 6 個已確認時間的財報、法說、政策、產品、融資或監管催化劑；財報優先保留日期與盤前/盤後。</li>
    </ul>
  </section>

  <section class="mb-section mb-watchlist">
    <h3>今日觀察</h3>
    <ul>
      <li>列出 3 至 5 個今天可被驗證的追蹤事項，例如財報/法說、盤前價格、政策細節、公司公告或供應鏈 read-through。</li>
    </ul>
  </section>
</article>

輸出前在內部自行檢查，不要把檢查過程寫出來：
- 是否先建立 ±7 天財報候選池且沒有只看 Mega Cap。
- 是否找出昨夜真正主導資金流向的 1~3 個科技族群，而非固定列單一 ADR。
- 每個族群是否都有代表公司、漲跌幅、異動原因。
- 今日/未來財報是否保留日期與盤前/盤後；已知公司 IR 時段不得漏掉。
- 是否把昨日異常族群與今日龍頭財報做交叉比對；符合硬性規則者是否為 ★★★★★。
- 是否依交易影響力、供應鏈、read-through、昨日輪動、48小時催化劑排序，而不是依新聞熱度。
- 是否刪除重複、空泛句子與無法核實的數字。
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