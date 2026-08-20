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
主要資訊區間：最近一個完整美股交易日至現在。
必要時可回看至 {prior_session_start.strftime('%Y/%m/%d %H:%M')}（台北時間）
取得收盤後的新消息。

財報檢查區間：
最近一個完整美股交易日，以及今天至 {earnings_end.isoformat()}（未來3天）。

【核心任務】

這不是一般科技新聞早報。

只找可能改變以下 AI Infrastructure 產業鏈
供需、價格、訂單、CapEx、產能、競爭格局或市場定價的新訊號：

1. Memory
   HBM、DRAM、NAND、DDR5、AI server memory。

2. XPU
   GPU、TPU、AI accelerator、hyperscaler 自研加速器、
   training / inference accelerator。

3. ASIC
   custom AI ASIC、hyperscaler custom silicon、
   AI accelerator ASIC 及直接相關供應鏈。

4. CPO / Optical
   CPO、silicon photonics、800G / 1.6T、
   optical transceiver、DSP、laser、
   AI data center optical interconnect。

5. MLCC
   AI server、accelerator、data center 所需高階 MLCC，
   特別是高容值、高壓、高可靠度產品。

6. Power / Power Semiconductor
   AI server / rack power、PSU、BBU、HVDC、
   power delivery、PMIC、MOSFET、SiC、GaN、IGBT。

同時關注大型 CSP 與重要 Neocloud，因為它們是上述產業的需求端：

Microsoft、Amazon/AWS、Google/Alphabet、Meta、Oracle，
CoreWeave、Nebius（NBIS）及其他具重大 AI infrastructure
CapEx / capacity 的 Neocloud。

但 CSP / Neocloud 本身不是獨立主題。

只有當其：
- AI CapEx
- GPU / XPU 採購
- custom ASIC
- data center capacity
- memory demand
- optical networking
- power infrastructure
- architecture / supplier change

會對上述六條產業鏈產生直接 read-through 時才列。

【明確排除】

以下原則上不要寫：

- Apple edge AI
- smartphone AI
- handset NPU
- AI PC
- consumer AI
- chatbot / agent
- SaaS AI
- 一般 AI application
- 一般 consumer electronics
- 與六條主線無直接關係的科技新聞

例外：
若事件足以直接改變六條主線的供需、價格或訂單，才可納入。

Apple 發布 edge AI 功能 → 不寫。
Apple 大幅改變 DRAM / NAND 採購並影響市場供需 → 可以寫。

【最高輸出原則】

Coverage 不等於 Output。

可以搜尋到很多資訊，
但只有真正改變投資判斷的事件才寫入早報。

沒有重要事件就少寫。

禁止為了完成版面湊新聞。
禁止把「值得留意」「市場可能波動」當成事件。
禁止把搜尋過程寫給讀者。

────────────────────
搜尋預算
────────────────────

整份任務最多使用 4 次 web search。
正常情況應以 3 次完成。

固定流程：

A. 最多1次：
昨夜六條主線的市場異動。

B. 最多1次：
高價值情報來源 + 六條主線重大新消息。

C. 最多1次：
最近交易日到未來3天重大財報 safety-net。

D. 最多1次：
只用於：
1. 全日最重要的 ★★★★★ 事件補充查證；或
2. 通過 Earnings Admission Test 的重大財報，
   但現有資料不足以判斷六條主線 read-through。

兩者共用同一次追加搜尋額度。
整份任務仍然最多4次 web search。

不得：

- 每家公司各搜一次
- 每個產業各搜一次
- 每個來源各搜一次
- 建完整財報資料庫
- 建完整 AI 公司清單
- 逐家公司研究
- 為小數字追加搜尋
- 為確認是否漏公司而追加搜尋
- 某網站沒資料後不斷更換網站
- 為輸出前檢查再搜尋

資訊不足時：

刪除 > 少寫 > 省略細節 > 新增搜尋。

────────────────────
A｜昨夜產業異動
────────────────────

用最多1次廣泛搜尋判斷最近一個完整美股交易日：

Memory
XPU
ASIC
CPO / Optical
MLCC
Power / Power Semiconductor

是否有真正的同步異動。

合格異常訊號包括：

1. 同方向族群異動
- 至少2~4家代表公司同方向明顯異動；或
- ETF / industry index / 可靠市場報導可同步確認。

2. 核心競爭者顯著反向異動
- 同一六主線內的重要競爭者、替代供應商或同一 CSP 供應鏈公司，
  若出現明顯相反方向的價格反應，也視為重大異常訊號。
- 特別注意 customer win/loss、supplier diversification、
  custom silicon deal、architecture change、長約、warrant、
  採購轉移或供應份額改變。
- 若單一核心公司漲跌約 ≥7%，或同產業兩家核心公司
  相對表現差距約 ≥8~10 個百分點，優先檢查是否存在直接公司事件。
- 例如 MRVL 明顯上漲而 AVGO 明顯下跌，
  不得只歸因為「科技股 risk-off」；
  必須優先判斷是否存在 Google / hyperscaler custom ASIC
  supplier allocation 或競爭格局的新資訊。

單一股票大漲大跌仍不能直接推論整個族群，
但若它是六主線核心公司且異動幅度顯著，
可作為「公司事件 discovery trigger」。

最多保留3個真正異常族群或重大競爭格局訊號。

每個族群只需：

- 族群名稱
- 2~4家代表公司
- 容易可靠取得的漲跌幅
- 最直接原因

若一次搜尋後沒有可靠異常族群：

直接輸出：

「無明顯可確認異常族群」

並停止此方向。

不得為確認「真的沒有嗎」再搜尋。

────────────────────
B｜高價值情報雷達
────────────────────

用最多1次廣泛搜尋確認最近24~36小時是否有重大新資訊。

【Freshness Hard Gate】

B bucket 的主要 discovery window 固定為最近24~36小時。

- 早於此區間的研究、報告或新聞，不得單獨進入「本日重點財經事項」。
- 舊資料只能作背景。
- 只有最近24~36小時出現新的價格反應、公司更新、供需變化或重大 follow-up 時，舊資料才可輔助解釋。
- 不得因找到日期較舊的 TrendForce / SemiAnalysis / 券商報告，就視為完成今日情報 discovery。
- 新舊資訊競爭版面時，優先保留最近24~36小時且直接改變六主線判斷的新事件。

優先檢查：

- Morgan Stanley / 大摩
- Goldman Sachs / 高盛
- Bloomberg
- Reuters
- TrendForce
- SemiAnalysis
- 鉅亨網

也可使用 CNBC、Yahoo Finance 或其他可靠財經／產業媒體作 discovery。

不要每個來源各自搜尋。

一次搜尋的目的，是確認上述來源最近是否出現與六條主線直接相關的新訊號。

【CSP / Neocloud Supplier-Change Priority】

最近24~36小時若 Microsoft、Google/Alphabet、Amazon/AWS、Meta、Oracle
或重要 Neocloud 出現以下任一新事件，自動視為至少 ★★★★☆ candidate，
必須進行 Admission Test：

- XPU / GPU / TPU 採購或自研架構變更
- custom ASIC / custom silicon 合作
- 新增或更換 ASIC / networking / memory / optical / power supplier
- supplier diversification / allocation change
- 長約、warrant、strategic agreement
- 大額 AI infrastructure order
- AI CapEx / data-center capacity / power capacity 明顯變更
- 可能改變既有供應商份額或競爭格局的合作

例如 Google 與 MRVL / AVGO 等 custom silicon 供應商之間
若出現新合作、warrant、訂單或 supplier allocation 變化，
屬於 ASIC / XPU 的高優先級 discovery，
不得因當日整體科技股下跌而忽略。

特別找：

- demand
- supply
- ASP / pricing
- inventory
- utilization
- orders
- capacity
- CapEx
- architecture
- supplier allocation
- supplier diversification
- customer win / loss
- custom silicon
- warrant / strategic agreement
- technology transition
- competitive position

如果某來源沒有重大新資訊：
直接忽略。

絕對禁止在輸出中寫：

- 今日未找到大摩報告
- 高盛沒有新消息
- Bloomberg 搜尋不足
- TrendForce 沒有發布
- SemiAnalysis 今日沒有更新
- 鉅亨沒有相關新聞
- 搜尋結果不足

這些都是內部搜尋過程，不是市場資訊。

────────────────────
C｜重大財報 Safety Net
────────────────────

用最多1次搜尋。

檢查：

最近一個完整美股交易日
+
今天至未來3天。

不是建立完整 earnings calendar。

只找能對下列主題提供重大 read-through 的財報：

Memory
XPU
ASIC
CPO / Optical
MLCC
Power / Power Semiconductor。

另外納入：

大型 CSP：
Microsoft、Amazon/AWS、Google/Alphabet、Meta、Oracle
及其他重大 hyperscaler。

重要 Neocloud：
CoreWeave、Nebius / NBIS
及其他具重大 GPU / data center CapEx 或 capacity 的 AI cloud 公司。

公司名稱只是範例。

禁止把這些公司逐一搜尋。
必須從一次財報 discovery 中找候選。

【財報 Admission Test】

財報至少符合一項才可列：

1. 能改變六條產業鏈的需求、供給或價格判斷。

2. 能提供重大 AI infrastructure
   CapEx / orders / capacity / pricing 新資訊。

3. 公司是該產業的重要龍頭、供應商或 demand setter。

4. 昨夜相關族群已有明顯異動，
   此財報可能成為直接解釋或下一個催化劑。

不要因為公司「與 AI 有關」就列。

不要因為 earnings calendar 顯示公司名稱就列。

不要因 Market Cap 大就自動列。

【沒有重大財報】

如果這一次搜尋沒有發現符合 Admission Test 的重大財報：

立即停止財報搜尋。

若今天沒有重大財報：
最終報告只寫：

「今日無重大財報發布。」

若今天沒有、但未來1~3天有重大財報：
今日仍寫：

「今日無重大財報發布。」

未來的重大財報放入「未來48小時催化劑」
或適當的未來事件位置。

禁止寫：

- TipRanks 找不到資料
- Investing.com 沒有顯示
- 財報日曆未提供
- 搜尋結果不足
- 無法確認其他公司
- 因搜尋限制沒有更多資料

讀者不需要知道搜尋過程。

────────────────────
D｜唯一 Deep Dive
────────────────────

前三次搜尋完成後，
最多只允許1次追加搜尋。

只有以下兩種情況可以使用：

情況1｜全日重大事件

- 是全日最重要事件之一；且
- 重要性達 ★★★★★；且
- 現有資料不足以確認核心事實。

例如：

- 重大產業報告需要確認關鍵數字
- 重大政策需要確認正式內容
- 已公布重大事件但 discovery 只有二手摘要

情況2｜重大財報補充確認

今日至未來3天，
若只有極少數公司通過 Earnings Admission Test，
但目前財報 discovery 只取得：

- 公司名稱
- 財報日期
- earnings calendar 基本資料

而不足以判斷該公司對六條主線的直接 read-through，

可以使用唯一一次追加搜尋確認其中最重要的一家公司。

情況2不要求事件先達 ★★★★★。

但必須確認該公司本身已通過 Earnings Admission Test。

例如：

- MU → Memory / HBM
- NVDA → XPU / Memory / CPO / Power
- AMD → XPU
- AVGO → ASIC / CPO
- MRVL → ASIC / CPO
- LITE / COHR → CPO / Optical
- AMAT → Memory / advanced packaging / AI semiconductor equipment
- 大型 CSP → AI CapEx / ASIC / XPU / infrastructure demand
- CoreWeave / Nebius → XPU / Power / Optical infrastructure demand

以上只是判斷範例，
禁止因此逐家公司搜尋。

追加搜尋仍然必須遵守：

- 最多1次
- 最多研究1家公司／1個事件
- 找到足以判斷 read-through 的資訊後立即停止
- 不得因此搜尋同業
- 不得建立供應鏈名單
- 不得再確認是否還有其他財報
- 不得為取得完整 EPS / Revenue consensus 追加搜尋
- 不得因第一個結果不理想而重新搜尋第二家公司

如果現有 discovery 已足以判斷：

不要使用第4次搜尋。

如果沒有任何事件符合情況1或情況2：

不要使用第4次搜尋。

────────────────────
事件 Admission Test
────────────────────

一則事件要進「本日重點財經事項」，
至少符合一項：

A. 已造成六條主線其中一條明顯重新定價。

B. 新資訊改變供需、ASP、inventory、orders、
   capacity、CapEx 或競爭格局。

C. CSP / Neocloud 新資訊對六條主線
   有直接且重要的 read-through。

D. 政策、出口管制、技術架構或供應鏈改變
   直接影響六條主線。

E. CSP / Neocloud 的 supplier diversification、customer win/loss、
   custom silicon deal、warrant、長約或 supplier allocation 改變，
   足以影響 ASIC / XPU / Memory / CPO / Power 的競爭格局。

F. 重大財報通過 Earnings Admission Test。

以下不能單獨成為重點：

- 市場情緒
- 風險偏好
- 投資人等待
- 關注殖利率
- 關注期貨
- AI 類股可能波動
- 一般 AI 新聞
- 一般科技新聞
- 普通 earnings calendar 項目
- 沒有新增事實的 analyst commentary

────────────────────
重要性
────────────────────

★★★★★
足以改變整條產業鏈、重要 demand setter、
大型供應商或市場核心假設。

★★★★☆
明顯影響六條主線其中一條，
或多家相關公司。

★★★☆☆
有具體新資訊且值得今天追蹤，
但影響較集中。

低於三星不要寫。

本日重點最多5則。

可以只有1則。
也可以沒有重大事件。

禁止湊數。

────────────────────
事件狀態
────────────────────

所有有日期／時間的事件先判斷：

A. 尚未發生
B. 正在發生
C. 已經發生

已發生的財報：
優先寫實際結果：

- Revenue
- EPS
- guidance
- CapEx
- AI orders
- capacity
- HBM / XPU / ASIC / optical / power demand
- management commentary

不要拿：

- earnings calendar
- earnings date
- conference call 預告
- event 預告頁

冒充已發生事件的實際結果。

若事件已發生，
但現有搜尋沒有實際結果：

只有 ★★★★★ 全日重大事件
可以使用唯一一次 deep dive。

其他少寫或刪除。

────────────────────
Read-through 規則
────────────────────

只做一階 read-through。

事實：
Micron 上修 HBM guidance。

可以：
→ Memory / HBM demand read-through。

事實：
Google 上修 AI CapEx。

可以：
→ XPU / ASIC / CPO / Power demand read-through。

事實：
Nebius 大幅增加 GPU / power capacity。

可以：
→ XPU、CPO、Power infrastructure demand read-through。

不要：

事實
→ 假設A
→ 假設B
→ 推測某台股一定受惠。

若需要兩個以上額外假設才能成立：
不要寫。

────────────────────
去重
────────────────────

同一核心事件只能完整描述一次。

「隔夜市場速覽」
只寫價格與最重要事實。

「本日重點財經事項」
寫完整事件與一階影響。

「未來48小時催化劑」
只列尚未發生的事件。

「今日觀察」
只寫新增的驗證條件。

禁止同一事件在四個 section
換句話說重複四次。

────────────────────
今日觀察
────────────────────

只能寫今天可以驗證的具體條件。

格式概念：

「若 X 發生，確認 Y 是否同步出現。」

禁止：

- 關注市場情緒
- 觀察科技股
- 留意 AI 股
- 關注期貨
- 關注殖利率
- 觀察成交量

如果沒有具體 observation：
可以只寫一項或簡短寫無新增觀察。

────────────────────
來源
────────────────────

事件來源優先級：

1. 公司 IR / 官方公告
2. 政府 / regulator
3. Bloomberg / Reuters
4. Morgan Stanley / Goldman Sachs 公開研究或可靠轉述
5. TrendForce
6. SemiAnalysis
7. 鉅亨網
8. 其他可靠財經媒體

但不要為了來源優先級追加搜尋。

Discovery 已取得可靠來源，
足以支持核心事實後立即停止。

來源不支持某個數字：
刪掉數字。

來源不支持核心事件：
刪掉事件。

────────────────────
輸出規則
────────────────────

- 使用繁體中文。
- 句子短。
- 資訊密度高。
- 先事實，再影響。
- 不提供買賣建議。
- 不預測必然漲跌。
- 不輸出 Markdown。
- 不輸出 code fence。
- 不輸出搜尋流程。
- 不描述哪些網站查不到。
- 不描述搜尋次數或資料限制。
- 不為版面湊數。
- 禁止將篩選規則、Admission Test、搜尋策略、未納入原因、
  「不湊數」「不以 earnings calendar 填空」等編輯判斷寫入最終報告。
- 若某 section 沒有重大事件，只用自然、讀者可見的市場語言簡短表達，
  不得暴露內部 prompt 或篩選流程。

來源直接放在相關內容末尾。

連結只能：

<a href="..." target="_blank" rel="noopener noreferrer">來源名稱</a>

必須直接輸出可嵌入 WordPress 的 HTML。

最外層必須：

<article class="mb-brief">

不要輸出：
<html>
<head>
<body>

不要使用表格。

────────────────────
固定 HTML 結構
────────────────────

<article class="mb-brief">
  <header class="mb-header">
    <p class="mb-kicker">每日市場早報</p>
    <h2>每日市場早報｜{now.strftime('%Y/%m/%d')}</h2>
    <p class="mb-updated">更新時間：{now.strftime('%Y/%m/%d %H:%M')} 台北時間</p>
  </header>

  <section class="mb-section mb-overnight">
    <h3>隔夜市場速覽</h3>
    <ul>
      <!-- 只寫真正重要的隔夜變化 -->
    </ul>
  </section>

  <section class="mb-section mb-rotation">
    <h3>昨夜科技資金輪動</h3>
    <!-- 最多3個真正異常族群。
         沒有則只寫「無明顯可確認異常族群」。 -->
  </section>

  <section class="mb-section mb-events">
    <h3>本日重點財經事項</h3>
    <ol>
      <!-- 最多5則，禁止湊數 -->
    </ol>
    <!-- 今日若無重大財報，只寫「今日無重大財報發布。」 -->
  </section>

  <section class="mb-section mb-catalysts">
    <h3>未來48小時催化劑</h3>
    <ul>
      <!-- 只列已知且尚未發生的重要催化劑 -->
    </ul>
  </section>

  <section class="mb-section mb-watchlist">
    <h3>今日觀察</h3>
    <ul>
      <!-- 只寫具體可驗證條件 -->
    </ul>
  </section>
</article>

最後自行確認：

- 只聚焦 Memory / XPU / ASIC / CPO / MLCC / Power。
- CSP / Neocloud 只作為 AI infrastructure demand signal。
- Consumer / Edge AI 已排除。
- 今日與未來財報沒有混淆。
- B bucket 的本日重點沒有被24~36小時以前的舊研究取代。
- 已檢查六主線核心競爭者是否存在顯著反向異動。
- CSP / Neocloud 的 custom silicon、supplier allocation、warrant、長約與供應商多元化事件沒有被一般 risk-off 敘事掩蓋。
- 沒有輸出搜尋失敗、網站狀態、Admission Test 或內部編輯規則。
- 沒有重複同一事件。
- 沒有湊數。
- 五個 section 都存在。
- 直接輸出 HTML。
""".strip()


def generate_with_openai(model: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing OPENAI_API_KEY. Put it in .env locally or GitHub Secrets."
        )

    max_web_search_calls = MAX_WEB_SEARCH_CALLS

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
