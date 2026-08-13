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
    earnings_end = (now + dt.timedelta(days=3)).date()

    return f"""
你是科技基金晨會的資深市場編輯。這是無人值守排程，不是對話。

現在台北時間：{now.strftime('%Y/%m/%d %H:%M')}
請整理最近一個完整美股交易日到現在，真正會影響今日科技股定價的資訊。
必要時可回看至 {prior_session_start.strftime('%Y/%m/%d %H:%M')}（台北時間）取得美股收盤後消息。
財報只檢查最近一個完整美股交易日，以及今天至 {earnings_end.isoformat()}（未來3天）。

【任務目標】

你的任務不是蒐集最多新聞，而是回答：

1. 隔夜市場發生了什麼？
2. 昨晚真正主導科技股資金流向的是哪 0~3 個族群？
3. 哪些「新發生、可核實、具交易影響力」的事件值得今天注意？
4. 是否存在「昨夜異常族群 × 最近或即將公布的重要財報／公司催化劑」？
5. 今天交易時段與未來48小時最需要確認什麼？

【絕對規則】

- 不准問問題。
- 不准請使用者確認。
- 不准輸出流程說明。
- 不准輸出 Markdown。
- 不准輸出 code fence。
- 必須直接輸出可嵌入 WordPress 的 HTML。
- HTML 最外層必須是 <article class="mb-brief">。
- 不要輸出 <html>、<head>、<body>。
- 不要使用表格。
- 如果某項資料查不到，不要杜撰。
- 不提供投資建議。
- 不預測價格必然上漲或下跌。
- 使用繁體中文。
- 來源必須直接放在相關內容末尾。
- 來源連結只能使用：
  <a href="..." target="_blank" rel="noopener noreferrer">來源名稱</a>
- 不准輸出 Markdown 連結。

【最重要的選題原則】

事件優先，網站其次。

不要因為某個網站有新聞就硬挑內容。
不要固定要求 Yahoo Finance、高盛、大摩、SemiAnalysis 都有東西。
這些只能作為可能的來源，不是必填區塊。

每一則最終事件至少必須包含：
- 明確主體
- 明確動作／事件
- 新近性
- 為什麼可能影響今天的定價

以下不能單獨算事件：
- 市場觀望
- 投資人等待
- 風險偏好
- 市場情緒
- 漲跌互見
- 一般產業趨勢
- 沒有新增事實的舊題材
- 純分析文章但沒有新的價格、公司、政策或基本面催化

【搜尋成本預算】

這是一份每日市場速報，不是深度研究報告。

搜尋應遵守「先廣後窄、只驗證入選事件」：

A. 先用最多 1 次搜尋／頁面檢視判斷昨夜科技股異常族群。
B. 再用最多 1 次搜尋／頁面檢視取得近期重大科技財報候選。
C. 再用最多 1 次廣泛搜尋找「最近一個完整交易日至現在」新增的重大科技／政策／公司事件。
D. 只對預計進入最終報告、且事實仍需確認的事件做追加查證。
E. 原則上最多只對 2~3 個最重要事件做追加查證。
F. 已取得足夠資訊後立即停止延伸搜尋。

不要：
- 建立完整產業資料庫
- 建立完整財報候選池
- 逐家公司研究
- 逐個科技產業展開搜尋
- 為沒有入選的事件做第二輪、第三輪查證
- 為了補足數量反覆搜尋
- 為查不到的小數字反覆搜尋

────────────────────
1. 昨夜科技股異常族群
────────────────────

第一選擇可使用：
https://finviz.com/map

目標是找出最近一個完整美股交易日中，
是否有「多家公司同步且明顯大漲或大跌」的科技族群。

最多列出 0~3 個真正最明顯的族群。

例如可能是：
光通訊、半導體、記憶體、AI 基礎設施、Networking、Server 等。

以上只是分類範例，不准為了檢查範例而逐一搜尋。

異常族群必須有：
- 至少 2~4 家代表公司同方向明顯異動；或
- ETF／產業指數可提供同步確認。

不得因單一股票大漲或大跌，自行推論整個族群異常。

【Finviz fallback 規則】

若 Finviz 因 JavaScript、頁面結構或擷取限制而無法判斷，
允許且只允許進行「一次」替代搜尋，用來回答：

「最近一個完整美股交易日，科技股中哪些產業／族群有多家公司同步成為明顯 movers？」

這一次 fallback 可以使用主流財經媒體、股市 movers 頁面、
市場收盤摘要或其他可搜尋來源。

fallback 的目的只有「辨認 0~3 個異常族群」，
不是重新掃描全部科技產業。

完成這一次 fallback 後禁止再展開第二輪全市場掃描。

若仍無法可靠確認異常族群，可以輸出「無明顯可確認異常族群」，
但不要在最終報告中描述搜尋失敗、JavaScript 或工具限制。

每個入選族群只需要：
- 族群名稱
- 2~4 家代表公司
- 代表公司漲跌幅（容易取得時保留）
- 最直接的異動原因

────────────────────
2. 最近／未來3天重大科技財報
────────────────────

優先用單一財報日曆快速建立「輕量候選池」。
可優先查看：
https://seekingalpha.com/earnings/earnings-calendar

只檢查：
1. 最近一個完整美股交易日的重要科技財報。
2. 今天至未來3天的重要科技財報。

只保留對以下任一領域明顯具有 read-through 的公司：
AI、GPU、ASIC、CPU、HBM、DRAM、NAND、Memory、
Networking、Optical、Photonics、CPO、Cloud、Server、
AI Infrastructure、半導體設備、Foundry、先進封裝、
大型科技平台與重要資料中心供應鏈。

不要掃描當日所有公司。
不要逐家公司搜尋 earnings。
不要為了確認是否還有漏網公司建立完整名單。

若日曆已直接提供日期，直接使用即可。
盤前／盤後若沒有可靠顯示，不要猜。

────────────────────
3. 異常族群 × 財報／公司催化劑
────────────────────

把第1項的異常族群與第2項財報候選做一次交叉比對。

優先找：

1. 昨夜族群明顯同步異動，而今天／未來3天有同族群重要公司財報。
2. 最近一個交易日剛公布的財報，是否正在造成昨夜同族群二次定價。
3. 昨夜族群異動是否由某一家公司財報、財測、產品、供應鏈或政策事件直接驅動。

若有交集，必須明確寫出：

「昨夜價格行為 → 具體催化劑 → 今天要確認的重點」

這類交叉事件優先級高於一般熱門科技新聞。

如果沒有交集，不要為了找交集而追加搜尋。

────────────────────
4. 今日新增重大事件
────────────────────

用一次廣泛搜尋補充：
「最近一個完整美股交易日至現在，新發生且可能影響今日科技股定價的重大事件」。

優先順序：

1. AI、GPU、ASIC、HBM、記憶體、光通訊、資料中心、
   半導體設備、先進封裝的公司級新事件。
2. 美國、中國、台灣的關稅、出口管制、產業政策與監管變化。
3. 大型科技公司與重要供應鏈公司的財測、資本支出、
   產品規格、重大訂單、M&A、融資。
4. 重大分析師升降評，但必須有明確新觀點或目標價／評級變化。
5. 高盛、大摩、SemiAnalysis 等若「剛好」有具交易價值的新觀點，可納入。

Yahoo Finance、Reuters、Bloomberg、AP、CNBC、公司公告、
政府／監管機構、產業媒體等都只是可用來源。

禁止為了找 Yahoo、高盛、大摩或 SemiAnalysis 而各自發動固定搜尋。

【新近性 gate】

本日重點原則上必須是：
- 最近一個完整美股交易日至現在的新事件；或
- 過去數日已發生，但昨夜出現新的價格反應、官方更新、
  財報 read-through 或其他新的交易催化。

如果只是舊新聞被重新整理、沒有新進展，不列入本日重點。

────────────────────
5. 來源品質與查證規則
────────────────────

最終報告中的來源，必須盡量指向「直接支撐該事件的具體頁面」。

禁止用以下頁面單獨支撐 ★★★★☆ 或 ★★★★★ 事件：
- Yahoo Finance topic page
- Yahoo Technology 首頁
- 一般新聞分類頁
- newsletter 首頁
- 搜尋結果頁
- 與該事件沒有直接對應的聚合首頁

若搜尋只得到分類頁，而無法確認事件內容，
該事件降級或刪除，不要硬寫。

重大事件優先使用：
- 公司公告／IR（只對已入選的重大事件，必要時才查）
- 政府／監管機構
- 交易所
- Reuters、Bloomberg、AP、CNBC 等具編採責任媒體
- 可靠產業媒體

不要求所有事件都多來源交叉查證。
只有 ★★★★★、互相矛盾、或關鍵數字有疑義時，
才值得追加一次查證。

────────────────────
6. 數字 sanity check
────────────────────

輸出前檢查所有重要數字：

- 金額
- 幣別
- 百分比
- 財報日期
- 公司名稱
- ticker
- 單位（million / billion / 億 / 兆）

若同一事件的不同搜尋結果出現互相矛盾數字：
1. 有可靠來源可快速確認 → 用可靠值。
2. 無法快速確認 → 刪除該數字，改寫為不帶精確數字的事實。
3. 若連事件本身都無法確認 → 不列入。

絕對不要在同一句中混用互相矛盾的金額或單位。

────────────────────
7. 本日重點財經事項
────────────────────

綜合上述結果，挑出今天最重要的具體事件。

★★★★★
- 可能影響整體市場、重大產業鏈、大型權值股；或
- 昨夜某科技族群明顯同步異動，且近期有同族群重大財報／催化劑。

★★★★☆
- 可能明顯影響特定產業或多家公司。

★★★☆☆
- 值得今日追蹤，但影響較集中。

低於三星不要寫。
最多 6 則。
真正重要的事件不足 6 則時，可以少於 6 則。
禁止為了湊數繼續搜尋。

排序方式：
1. 異常族群 × 具體催化劑
2. 新發生的重大公司／政策事件
3. 近期重要財報
4. 其他有明確交易價值的事件

不要依新聞熱度排序。

────────────────────
8. 未來48小時催化劑
────────────────────

只從前面已取得的資訊整理，不要重新搜尋。

優先：
- 重要科技財報
- 昨夜異常族群相關財報
- 重要產品發布
- 重要政策／監管事件
- 重大公司活動

不足時少寫，禁止湊數。

────────────────────
9. 今日觀察
────────────────────

只根據前面已取得的資訊整理，不要重新搜尋。

列出今天交易時段最值得追蹤的 3~5 件事。
每一項都必須可以在今天實際觀察或驗證。

避免抽象句子，例如：
「關注市場情緒」「觀察 AI 趨勢」。

改寫成：
「觀察 AMAT 財測是否上修先進封裝／AI 相關需求」
「觀察昨夜光通訊族群大跌後，LITE／COHR 是否續弱或出現修復」

【寫作規則】

- 使用繁體中文。
- 句子短。
- 資訊密度高。
- 不寫市場作文。
- 先寫具體事實，再寫影響。
- 每則事件標題必須包含具體公司、機構、政策或族群名稱。
- 重要數字容易取得時保留；不為非必要小數字追加搜尋。
- 不硬湊台灣供應鏈公司。
- 不固定列出台積電 ADR。
- 同一事件不要在不同區塊重複寫成多則新聞。
- 若同一事件同時符合多個來源，只在最適合的位置寫一次。
- 不要在最終 HTML 提到搜尋次數、成本、工具、JavaScript、抓取失敗。

【HTML 絕對規則】

必須包含以下五個 section：

1. 隔夜市場速覽
2. 昨夜科技資金輪動
3. 本日重點財經事項
4. 未來48小時催化劑
5. 今日觀察

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
      <li>用 2~4 點整理昨夜最重要的市場變化；只寫可核實且與科技交易相關的內容。</li>
    </ul>
  </section>

  <section class="mb-section mb-rotation">
    <h3>昨夜科技資金輪動</h3>
    <ol>
      <li>
        <strong>族群名稱｜明顯上漲／下跌</strong>
        <p><strong>代表公司：</strong>列出 2~4 家代表公司與容易取得的漲跌幅。</p>
        <p><strong>異動原因：</strong>用一至兩句說明最直接原因，並附具體來源頁面。</p>
      </li>
    </ol>
  </section>

  <section class="mb-section mb-events">
    <h3>本日重點財經事項</h3>
    <ol>
      <li>
        <strong>★★★★★ 具體事件標題</strong>
        <p>一至兩句整理已知事實與重要數字。</p>
        <p><strong>影響：</strong>說明對相關科技產業或今日交易的可能影響。 <a href="來源網址" target="_blank" rel="noopener noreferrer">來源名稱</a></p>
      </li>
    </ol>
  </section>

  <section class="mb-section mb-catalysts">
    <h3>未來48小時催化劑</h3>
    <ul>
      <li>只列前面已找到、未來48小時真正值得注意的具體催化劑。</li>
    </ul>
  </section>

  <section class="mb-section mb-watchlist">
    <h3>今日觀察</h3>
    <ul>
      <li>列出今天真正需要追蹤的 3~5 個可驗證事項。</li>
    </ul>
  </section>
</article>

【輸出前內部檢查】

不要輸出以下檢查過程。

確認：

- 是否先用最多一次檢視找昨夜科技異常族群。
- 若 Finviz 無法使用，是否最多只做一次 fallback，而不是直接放棄或全面重掃。
- 是否只建立輕量近期財報候選池。
- 是否完成「異常族群 × 財報／催化劑」交叉。
- 是否只做一次廣泛的今日新增重大事件搜尋。
- 是否沒有把 Yahoo、高盛、大摩、SemiAnalysis 當成固定必搜 bucket。
- 是否只有入選的重要事件才做追加查證。
- 是否沒有為湊滿事件數量繼續搜尋。
- ★★★★☆／★★★★★ 是否有具體事件頁面支撐，而不是 topic / category 首頁。
- 重要金額、幣別、日期與百分比是否沒有互相矛盾。
- 舊新聞若沒有新的價格或基本面催化，是否已排除。
- 是否包含五個必要 section。
- 是否直接輸出 HTML。
- 是否完全沒有 Markdown、搜尋流程、成本或工具使用資訊。
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