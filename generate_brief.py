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
MAX_WEB_SEARCH_CALLS = int(os.getenv("MAX_WEB_SEARCH_CALLS", "4"))

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

只回答：

1. 隔夜市場真正重要的變化。
2. 昨夜科技股是否有 0~3 個明顯同步異動族群。
3. 今天最重要、可核實、具交易影響力的事件。
4. 是否有「異常族群 × 近期財報／公司催化劑」。
5. 未來48小時與今日交易時段真正需要追蹤什麼。

事件優先，網站其次。
不要為了填滿版面而蒐集新聞。

【輸出規則】

- 不准問問題、請確認或輸出流程說明。
- 不准輸出 Markdown 或 code fence。
- 必須直接輸出可嵌入 WordPress 的 HTML。
- 最外層必須是 <article class="mb-brief">。
- 不要輸出 <html>、<head>、<body>。
- 不要使用表格。
- 使用繁體中文。
- 不提供投資建議。
- 不預測價格必然上漲或下跌。
- 查不到就少寫，不得杜撰。
- 來源直接放在相關內容末尾。
- 來源連結只能使用：
  <a href="..." target="_blank" rel="noopener noreferrer">來源名稱</a>

【硬性成本限制】

整份任務：

- 初始探索最多 3 個搜尋方向。
- 初始探索完成後，最多只允許 1 次追加搜尋。
- 不得為每一則事件各自重新搜尋。
- 不得因輸出前檢查而啟動新搜尋。
- 資訊不足時：刪除 > 降級 > 省略數字，優先於新增搜尋。

初始探索固定為：

A. 最多 1 次：昨夜科技股異常族群。
B. 最多 1 次：近期重大科技財報候選。
C. 最多 1 次：最近一個完整交易日至現在的重大科技／政策／公司事件。

唯一一次追加搜尋：

只能用於全日本日最重要的 ★★★★★ 事件。

其他事件若資訊不足：
直接刪除、降級或省略細節。

不要：

- 建完整產業資料庫。
- 建完整財報名單。
- 逐家公司研究。
- 逐個科技產業搜尋。
- 為湊數反覆搜尋。
- 為非必要小數字追加搜尋。
- 為了確認是否漏掉其他公司而重新展開搜尋。

────────────────────
1. 昨夜科技股異常族群
────────────────────

使用最多 1 次廣泛搜尋，判斷最近一個完整美股交易日中，
科技股是否有「多家公司同步且明顯上漲或下跌」的族群。

搜尋目標只有：

辨認 0~3 個最明顯的科技族群。

不要重新掃描全部科技產業。

可使用：

- 主流財經媒體的美股收盤摘要
- technology stocks movers
- sector / industry movers
- 科技股盤後或收盤整理
- 可靠產業媒體

最多列出 0~3 個族群。

異常族群必須有：

- 至少 2~4 家代表公司同方向明顯異動；或
- ETF／產業指數可同步確認。

不得因單一股票異動推論整個族群。

每個入選族群只需要：

- 族群名稱
- 2~4 家代表公司
- 容易取得的漲跌幅
- 最直接異動原因

如果這一次搜尋後仍無法可靠確認異常族群：

直接寫：
「無明顯可確認異常族群」

不得為了確認族群再做第二次全市場搜尋。

────────────────────
2. 最近／未來3天重大科技財報
────────────────────

財報候選優先只查看：

https://www.tipranks.com/calendars/earnings

一次查看：

1. 最近一個完整美股交易日。
2. 今天至未來3天。

優先使用：

- 日期格直接顯示的代表公司
- 頁面中容易取得的 Market Cap
- Earnings Time
- EPS / Revenue 資料

日期格直接顯示的重要科技公司，
直接視為優先候選。

不要為了確認是否還有
「更重要但沒有直接顯示的公司」
而繼續搜尋。

只保留：

- 日期格直接顯示的重要科技公司；或
- Market Cap 較大，且明顯屬於以下領域的重要公司：

AI
GPU
ASIC
CPU
HBM
Memory
Networking
Optical
Photonics
CPO
Cloud
Server
AI Infrastructure
半導體設備
Foundry
先進封裝
大型科技平台
重要資料中心供應鏈

每個日期最多保留 1~3 家真正重要的科技公司。

不要：

- 點開當日全部公司
- 掃描完整公司名單
- 為了找更多公司翻頁
- 逐家公司搜尋 earnings
- 建立完整財報候選池

若 TipRanks 無法取得可用財報資料：

只允許使用一次備援：

https://www.investing.com/earnings-calendar

使用 Investing.com 時：

- 優先依 Market Cap 與科技產業重要性挑選。
- 每個日期最多保留 1~3 家。
- 不掃描完整公司清單。
- 不翻頁尋找更多公司。

TipRanks 若可正常取得資料，
不得另外搜尋 Investing.com 建立第二份財報名單。

TipRanks → Investing.com 的 fallback
仍然屬於「財報候選」這一個搜尋方向，
不得因此再新增第三個財報來源。

盤前／盤後時間若頁面沒有可靠提供，
不要猜。

────────────────────
3. 事件狀態
────────────────────

對有明確日期／時間的事件，例如：

- 財報
- 法說
- Investor Day
- 發布會
- 政策公布
- 聽證會
- 產品發布
- 監管裁決
- 經濟數據

在寫入報告前判斷：

A. 尚未發生
B. 正在發生
C. 已經發生

三種狀態不得混用。

只有尚未發生的事件才可以寫：

- 即將公布
- 今日需觀察
- 市場等待
- 預計舉行

若事件已經發生：

先檢查目前已取得的搜尋結果是否已有實際結果。

若已有：

使用實際結果，例如：

- 營收
- EPS
- 財測
- 重要業務指標
- 管理層重要說法
- 容易可靠取得的價格反應

若目前只有預告頁：

- 若它是全日最重要的 ★★★★★ 事件，
  可以使用整份報告唯一一次追加搜尋。
- 否則不得追加搜尋。
- 降低重要性、刪除未被支持的細節，或整則不列。

不要拿：

- event calendar
- conference call 預告頁
- earnings date 頁
- 活動預告新聞

冒充已經發生事件的實際結果。

────────────────────
4. 異常族群 × 財報／公司催化劑
────────────────────

把第1項異常族群，
與第2項財報候選做一次交叉比對。

優先找：

1. 昨夜族群同步異動，
   而今天／未來3天有同族群重要公司財報。

2. 最近剛公布的財報，
   是否正在造成昨夜同族群二次定價。

3. 昨夜族群異動是否由具體：
   財報、財測、產品、供應鏈或政策事件驅動。

若有交集：

事件尚未發生：

「昨夜價格行為
→ 具體催化劑
→ 今天要確認什麼」

事件已發生：

「昨夜價格行為
→ 已公布結果
→ 今天市場將重新定價什麼」

這類交叉事件優先於一般熱門科技新聞。

如果沒有交集：
停止，不追加搜尋。

────────────────────
5. 今日新增重大事件
────────────────────

使用最多 1 次廣泛搜尋補充：

「最近一個完整美股交易日至現在，
新發生且可能影響今日科技股定價的重大事件」

優先順序：

1. AI、GPU、HBM、記憶體、光通訊、
   資料中心、半導體設備、先進封裝。

2. 美國、中國、台灣的：
   關稅、出口管制、產業政策、監管。

3. 大型科技公司與重要供應鏈公司的：
   財測、CapEx、產品規格、重大訂單、M&A、融資。

4. 具明確新內容的重大分析師升降評。

5. 高盛、大摩、SemiAnalysis
   若剛好有具交易價值的新觀點，可納入。

Yahoo Finance、Reuters、Bloomberg、AP、CNBC、
公司公告、政府／監管機構、產業媒體，
都只是可用來源。

它們不是固定必搜 bucket。

不要為了找某一媒體或某一家券商而另外發動搜尋。

【新近性】

本日重點原則上必須是：

- 最近一個完整美股交易日至現在的新事件；或

- 過去數日已發生，
  但昨夜有新的價格反應、官方更新、
  財報 read-through 或新的交易催化。

只有舊新聞、沒有新進展：
不列。

────────────────────
6. 來源一致性
────────────────────

只使用目前已取得的搜尋結果與已開啟來源檢查：

- 公司／機構是否一致
- 日期／季度是否一致
- 事件動作是否一致
- 使用到的關鍵數字是否一致

此檢查不得自行啟動新搜尋。

來源若不支持文字：

1. 先看目前已取得的其他來源能否支持。
2. 不能支持就刪除該細節。
3. 核心事件無法支持就整則刪除。

只有當它是全日最重要的 ★★★★★ 事件，
且唯一一次追加搜尋額度尚未使用，
才可追加一次查證。

★★★★☆ 以下事件若來源不足：

直接降級或刪除。
不得追加搜尋。

禁止用以下頁面單獨支撐 ★★★★☆ 或 ★★★★★：

- Yahoo Finance topic page
- Yahoo Technology 首頁
- 一般新聞分類頁
- newsletter 首頁
- 搜尋結果頁
- 與事件無直接對應的聚合首頁

重大事件優先使用：

- 公司新聞稿／IR
- 政府／監管機構
- 交易所
- Reuters
- Bloomberg
- AP
- CNBC
- 可靠產業媒體

────────────────────
7. 數字與推論
────────────────────

檢查重要數字：

- 金額／幣別
- 百分比／股價
- 財報日期
- ticker
- 季度／會計年度
- million / billion / 億 / 兆

若不同來源數字矛盾：

1. 現有可靠來源可判斷 → 使用可靠值。
2. 無法判斷 → 刪除精確數字。
3. 核心事件都無法確認 → 不列。

禁止在同一句混用互相矛盾的金額或單位。

先寫可驗證事實，
再寫直接的一階影響。

可以寫：

- PPI 低於預期
  → 殖利率下降
  → 長久期科技股估值壓力減輕

- AMAT 上修 WFE 展望
  → 半導體設備族群可能重新定價

- Cisco AI orders 增長
  → AI networking 具 read-through

不要自行補二階、三階推論，例如：

- PPI 降溫 → 企業更有能力增加 AI CapEx
- 某公司融資 → 整個產業需求必然上升
- 某政策新聞 → 台灣所有相關供應鏈都受益
- 單一公司上漲 → 整個族群基本面轉強

報告寧可少，
不要用市場作文填滿。

────────────────────
8. 本日重點財經事項
────────────────────

★★★★★

可能影響：
- 整體市場
- 重大產業鏈
- 大型權值股

或：

異常族群與重大財報／催化劑直接交叉。

★★★★☆

可能明顯影響特定產業或多家公司。

★★★☆☆

值得今日追蹤，但影響較集中。

低於三星不要寫。

最多 5 則。

真正重要事件不足 5 則時：
可以少寫。

禁止湊數。

排序：

1. 已發生且剛公布的重大財報／政策結果
2. 異常族群 × 具體催化劑
3. 新發生的重大公司／政策事件
4. 尚未發生的近期重要催化劑
5. 其他明確有交易價值的事件

────────────────────
9. 未來48小時催化劑
────────────────────

只從前面已取得資訊整理。

不要重新搜尋。

只能列目前時間之後尚未發生的事件。

已經發生的：

- 財報
- 法說
- 經濟數據
- 公司活動

不得再寫成「即將發生」。

若財報已公布，
但市場尚未進入下一完整交易時段，
可以寫：

「市場對已公布財報的首個完整交易時段反應」

不足就少寫。

────────────────────
10. 今日觀察
────────────────────

只根據前面已取得資訊整理。

不要重新搜尋。

列 3~5 件今天可實際觀察／驗證的事項。

若財報／法說已經發生：

不要寫：

「觀察公司將說什麼」

要寫：

「觀察市場如何定價已公布的財測／毛利率／AI需求數據」

避免：

- 關注市場情緒
- 觀察 AI 趨勢

改成具體、可驗證事項。

【寫作規則】

- 句子短。
- 資訊密度高。
- 先寫具體事實，再寫直接影響。
- 標題包含具體公司、機構、政策或族群名稱。
- 容易取得的重要數字保留。
- 不為非必要數字追加搜尋。
- 不硬湊台灣供應鏈公司。
- 不固定列出台積電 ADR。
- 同一事件不要在不同區塊重複當成新事件。
- 不確定就少寫，不要補完。
- 不要在最終 HTML 提到搜尋次數、成本、工具或抓取限制。

【HTML 結構】

必須包含：

1. 隔夜市場速覽
2. 昨夜科技資金輪動
3. 本日重點財經事項
4. 未來48小時催化劑
5. 今日觀察

<article class="mb-brief">
  <header class="mb-header">
    <p class="mb-kicker">每日市場早報</p>
    <h2>每日市場早報｜{now.strftime('%Y/%m/%d')}</h2>
    <p class="mb-updated">更新時間：{now.strftime('%Y/%m/%d %H:%M')} 台北時間</p>
  </header>

  <section class="mb-section mb-overnight">
    <h3>隔夜市場速覽</h3>
    <ul>
      <li>用 2~4 點整理昨夜最重要且可核實的科技市場變化。</li>
    </ul>
  </section>

  <section class="mb-section mb-rotation">
    <h3>昨夜科技資金輪動</h3>
    <ol>
      <li>
        <strong>族群名稱｜明顯上漲／下跌</strong>
        <p><strong>代表公司：</strong>2~4 家代表公司與容易取得的漲跌幅。</p>
        <p><strong>異動原因：</strong>一至兩句說明最直接原因並附來源。</p>
      </li>
    </ol>
  </section>

  <section class="mb-section mb-events">
    <h3>本日重點財經事項</h3>
    <ol>
      <li>
        <strong>★★★★★ 具體事件標題</strong>
        <p>一至兩句整理事實與重要數字。</p>
        <p><strong>影響：</strong>只寫直接的一階市場影響。 <a href="來源網址" target="_blank" rel="noopener noreferrer">來源名稱</a></p>
      </li>
    </ol>
  </section>

  <section class="mb-section mb-catalysts">
    <h3>未來48小時催化劑</h3>
    <ul>
      <li>只列尚未發生且真正重要的具體催化劑。</li>
    </ul>
  </section>

  <section class="mb-section mb-watchlist">
    <h3>今日觀察</h3>
    <ul>
      <li>列 3~5 個今天可實際驗證的事項。</li>
    </ul>
  </section>
</article>

【輸出前最後檢查】

不得因此啟動任何新搜尋。

確認：

- 初始搜尋方向最多 3 個。
- 全份報告追加搜尋最多 1 次。
- 科技族群只做一次廣泛搜尋，不另外掃描。
- 財報優先只使用 TipRanks。
- TipRanks 可用時，不搜尋第二份財報名單。
- TipRanks 不可用時，最多使用 Investing.com 一次備援。
- 每個日期財報候選最多 1~3 家重要科技公司。
- 不因擔心漏掉財報公司而追加搜尋。
- 已發生／未發生事件沒有混用。
- 已發生財報沒有只拿預告頁當結果。
- 未來48小時沒有列入已發生事件。
- 來源的公司、日期／季度、動作、關鍵數字與文字一致。
- 來源不足時是刪除／降級，不是新增搜尋。
- 沒有未支持的二階／三階推論。
- 沒有舊新聞充數。
- 五個 section 都存在。
- 直接輸出 HTML。
""".strip()


def generate_with_openai(model: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing OPENAI_API_KEY. Put it in .env locally or GitHub Secrets."
        )

    max_web_search_calls = int(os.getenv("MAX_WEB_SEARCH_CALLS", "4"))

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
                        "text": (
                            "你是排程任務執行器。"
                            "必須直接完成任務，不得提問、不得請確認、"
                            "不得輸出對話式回覆。"
                        ),
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
        max_tool_calls=max_web_search_calls,
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