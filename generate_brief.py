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
    earnings_start = (now - dt.timedelta(days=3)).date()
    earnings_end = (now + dt.timedelta(days=3)).date()

    return f"""
你是科技基金晨會的資深市場編輯。這是無人值守排程，不是對話。

現在台北時間：{now.strftime('%Y/%m/%d %H:%M')}
財報候選池日期：{earnings_start.isoformat()} 至 {earnings_end.isoformat()}。
昨夜資金輪動搜尋區間：最近一個完整美股交易日；必要時回看至 {prior_session_start.strftime('%Y/%m/%d %H:%M')}（台北時間）以取得收盤後消息。

【任務目標】

你的任務不是寫市場作文，而是回答：

1. 隔夜市場發生了什麼？
2. 昨晚真正主導科技股資金流向的是哪 1~3 個族群？
3. 今天有哪些「具體事件」可能影響美股、台股、AI、半導體或重要產業定價？
4. 是否存在「昨夜族群異常波動 × 今日或近期重要財報」的交叉催化劑？
5. 今天交易時段與未來48小時需要追蹤什麼？

【事件的定義】

合格事件必須至少包含一個可核實的主體與動作，例如：
公司發布產品、併購、IPO 定價、增發債券、財報、財測修正、關稅或法規生效、
出口管制、供應鏈規格改變、ETF 上市、商品價格突破、重大資本支出或重要政策變化。

「市場觀望」「投資人等待」「風險偏好改變」「漲跌互見」不是事件，
不能單獨列入重點事項。

【重要：以下為內部工作流程，不得出現在輸出 HTML】

第一步｜低成本建立最近 ±3 天財報候選池

搜尋過去3天已公布、今日公布、未來3天即將公布的重要科技與供應鏈公司財報。

不限 Mega Cap。只要公司與以下任一主題高度相關即可列入候選：
AI、GPU、ASIC、CPU、HBM、DRAM、NAND、Memory、
Networking、Optical Networking、Photonics、CPO、Transceiver、
AI Infrastructure、Cloud、Server、PCB、Power、Cooling、
Foundry、半導體設備、先進封裝。

此階段只建立「輕量候選池」，優先記錄：
- company
- ticker
- earnings date（若搜尋結果可直接取得）
- 所屬產業／族群

不要在此階段逐家公司深入搜尋：
- 不要求每家公司都查 IR
- 不要求每家公司都確認盤前／盤後
- 不要求每家公司都分析台灣供應鏈
- 不要求每家公司都做多來源交叉查證
- 不要為明顯不會入選的候選公司進行延伸搜尋

目的只是避免漏掉近期可能成為市場催化劑的重要財報。

第二步｜找出昨夜真正異常的科技族群

搜尋最近一個完整美股交易日，找出真正主導科技股資金流向、
且出現明顯同步漲跌的 1~3 個族群。

候選包括但不限於：
光通訊、Photonics、CPO、Transceiver、HBM、Memory、DRAM、NAND、
ASIC、Networking、AI Infrastructure、Cloud、Server、
PCB、Cooling、Power、Foundry、半導體設備、先進封裝。

「異常族群」必須有多檔公司同方向明顯異動，
或 ETF／產業指數可提供同步確認。

不能因為單一股票大漲或大跌，就判定整個族群異常。

對真正入選的 1~3 個族群紀錄：
- 族群名稱
- 2~4 家具代表性的公司
- 代表公司漲跌幅
- ETF 或產業指數漲跌幅（若可可靠取得）
- 最直接的異動原因

不要為沒有明顯異常的其他科技族群繼續深挖。

第三步｜交叉比對「異常族群 × 財報候選池」

把第二步的 1~3 個異常族群，與第一步 ±3 天財報候選池交叉比對。

優先找：

1. 昨夜族群明顯同步下跌／上漲，而今日有該族群重要公司財報。
2. 昨夜族群明顯異動，而未來72小時內有龍頭或具 read-through 能力的公司財報。
3. 過去3天剛公布的財報，是否正在造成昨夜同族群的二次定價或 read-through。
4. 財報公司是否可能影響 AI／科技供應鏈或台灣相關供應鏈。

只有符合上述條件、或本身具重大市場影響力的公司，
才進入「深度查證」階段。

硬性規則：

若「昨夜某族群明顯同步下跌或上漲」
且「今日有該族群龍頭、關鍵供應商或具高度 read-through 能力的公司財報」，
該事件必須列入本日重點財經事項，原則上評為 ★★★★★。

必須明確連結：
「昨夜族群價格行為 → 今日財報催化劑 → 今日應確認的重點」。

第四步｜只對真正入選事件做深度查證

完成初步篩選後，只對預計進入最終報告的事件進一步核實。

財報事件：
- 優先使用公司 IR、交易所、SEC 或其他第一方來源確認財報日期。
- 若要寫「盤前／盤後」，必須可靠確認後才可寫。
- 無法確認時段時，不得猜測。

其他重大事件：
優先採用公司公告、監管機構、交易所、政府文件、
以及具編採責任的財經媒體。

重大或有爭議事件盡量交叉查證。

關鍵數字、交易金額、關稅稅率、定價、財報日期、
盤前／盤後、漲跌幅若無法可靠確認，不得寫成確定值。

傳聞必須標示「據報」「傳出」或「消息人士稱」。

不要為最終不會採用的候選事件進行不必要的第二輪、第三輪查證。

第五步｜搜尋今日其他重大具體事件

除了財報與昨夜異常族群之外，
補充搜尋今日與未來48小時內具明確交易影響力的事件。

【選題優先順序】

1. AI、GPU、ASIC、HBM、記憶體、光通訊、資料中心、
   半導體設備、先進封裝。
2. 美國、中國、台灣的關稅、出口管制、產業政策與監管變化。
3. 大型科技公司、重要供應鏈公司的併購、融資、財測、
   產品規格與資本支出。
4. 重大 IPO、ETF 上市、公司債發行、資金流向與商品價格突破。
5. 對台積電及台灣科技供應鏈可能有直接影響的海外公司消息。

新產品、Roadmap、新 GPU、AI 晶片、新記憶體、
IPO、M&A、融資、Convertible、Analyst Upgrade/Downgrade、
政策、關稅、制裁、AI 法規均可納入。

案例名稱不得因過去曾經重要而固定出現。
只有當日有新增、可核實且具交易影響力的進展才納入。

第六步｜合併、去重與排序

不要依新聞熱度排序。

所有候選事件依下列順序綜合排序：

1. 今日交易影響力
2. 對 AI／科技供應鏈影響
3. 是否與昨夜異常族群直接相關
4. 是否具 read-through
5. 是否為今日或未來48小時催化劑
6. 是否對台灣科技供應鏈具明確關聯

同一件事若被多家媒體報導，只能保留一則事件，
來源可在該事件內補充，不得重複列項。

【重要性評分】

★★★★★：
可能影響整個市場、產業鏈、大型權值股，
或符合「昨夜異常族群 × 今日重要財報」交叉比對硬性規則。

★★★★☆：
可能明顯影響特定產業或多家相關公司。

★★★☆☆：
值得今日追蹤，但影響較集中或仍待確認。

低於三星不要寫。

重點事件最多 10 則。
真正重要事件不足時可以少於 10 則，禁止湊數。

【正文任務】

回答：

1. 隔夜市場發生了什麼？
2. 昨晚真正主導科技股資金流向的是哪 1~3 個族群？
3. 今天有哪些具體事件最可能影響美股、台股、AI、半導體或重要供應鏈定價？
4. 是否存在昨夜異常族群與今日／近期財報的交叉催化劑？
5. 未來48小時有哪些值得盯的催化劑？
6. 今天交易時段有哪些可以被驗證的追蹤事項？

【寫作規則】

- 使用繁體中文，句子短，資訊密度高。
- 先寫具體事實，再寫市場影響；不要用空泛評論填充。
- 不要固定列出台積電 ADR。
  只有它本身當日有顯著價格異動、公司消息或明確 read-through 時才列。
- 每個「昨夜科技資金輪動」族群必須列：
  代表公司、漲跌幅、異動原因；
  ETF 若有且資料可靠則一併列出。
- 財報事件若已可靠確認，寫出「日期＋盤前／盤後」。
- 無法確認盤前／盤後時，不得猜測。
- 每則事件標題必須包含公司、機構、商品、政策或清楚族群名稱，
  不可只寫抽象敘述。
- 每則事件盡量保留關鍵數字，例如價格、金額、稅率、
  估值、漲跌幅或時間。
- 「影響」需點出相關產業，以及有明確關聯的美股、
  ADR 或台灣供應鏈公司；不可硬湊股票。
- 不提供買賣建議，不預測必然上漲或下跌。
- 不要另外設置高盛、大摩、SemiAnalysis 或 Yahoo 專區；
  只有其內容本身夠重要時才列為事件。

【禁止用語】

除非後面立刻接具體事實，否則不要使用：

「市場觀望」
「投資人等待」
「漲跌互見」
「受到多重因素影響」
「風險偏好」
「市場情緒」
「仍須觀察」
「整體而言」
「未來可能」
「分析師認為」

【HTML 絕對規則】

- 只輸出可嵌入 WordPress 的 HTML。
- 不准輸出 Markdown、code fence、流程說明、候選池、
  搜尋紀錄、內部排序規則、查證過程、成本資訊、
  token 使用量、工具使用量或任何對話內容。
- 最外層必須是 <article class="mb-brief">。
- 不要輸出 <html>、<head>、<body>。
- 不要使用表格。
- 連結格式只能是：
  <a href="..." target="_blank" rel="noopener noreferrer">來源名稱</a>
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
        <p><strong>異動原因：</strong>用一至兩句寫出同步異動的直接催化劑、估值／資金輪動背景或公司事件，並附來源。</p>
      </li>
    </ol>
  </section>

  <section class="mb-section mb-events">
    <h3>本日重點財經事項</h3>
    <ol>
      <li>
        <strong>★★★★★ 具體事件標題（保留關鍵數字）</strong>
        <p>一至兩句說清楚誰在何時做了什麼，以及已確認的關鍵細節。財報類若已確認，包含公布日期與盤前／盤後。</p>
        <p><strong>影響：</strong>說明對產業、read-through、台灣供應鏈與今日交易的可能影響，以及今天要追蹤的確認點。 <a href="可靠來源網址" target="_blank" rel="noopener noreferrer">來源名稱</a></p>
      </li>
    </ol>
  </section>

  <section class="mb-section mb-catalysts">
    <h3>未來48小時催化劑</h3>
    <ul>
      <li>列 3 至 6 個已確認時間或日期的財報、法說、政策、產品、融資或監管催化劑；財報若已可靠確認，保留日期與盤前／盤後。</li>
    </ul>
  </section>

  <section class="mb-section mb-watchlist">
    <h3>今日觀察</h3>
    <ul>
      <li>列出 3 至 5 個今天可被驗證的追蹤事項，例如財報／法說、盤前價格、政策細節、公司公告或供應鏈 read-through。</li>
    </ul>
  </section>
</article>

輸出前在內部自行檢查，不要把檢查過程寫出來：

- 是否只建立 ±2 天輕量財報候選池。
- 是否避免逐家候選公司進行深度查證。
- 是否找出昨夜真正主導資金流向的 1~3 個異常科技族群。
- 每個入選族群是否都有代表公司、漲跌幅、異動原因。
- 是否完成「昨夜異常族群 × ±2 天財報」交叉比對。
- 符合「昨夜異常族群 × 今日重要財報」者是否列入重點並給予最高優先級。
- 是否只有真正入選事件才進行 IR／第一方來源深度核實。
- 財報日期與盤前／盤後若寫出，是否已有可靠來源支持。
- 是否依交易影響力、供應鏈、昨夜異動、read-through、
  48小時催化劑排序，而不是新聞熱度。
- 重點事項是否都是具體事件。
- 是否保留重要數字。
- 是否刪除重複與空泛句子。
- 每則重大事件是否至少有一個可靠來源。
- HTML 是否完全沒有候選池、搜尋過程、內部規則、
  token、成本或工具使用資訊。
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