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
主要資訊區間：最近一個完整美股交易日至現在；必要時回看至 {prior_session_start.strftime('%Y/%m/%d %H:%M')}（台北時間）取得收盤後新消息。
財報檢查區間：最近一個完整美股交易日，以及今天至 {earnings_end.isoformat()}（未來3天）。

【核心任務】

這不是一般科技新聞早報。深度產業分析只找可能改變以下 AI Infrastructure 供需、價格、訂單、CapEx、產能、競爭格局或市場定價的新訊號：

1. Memory：HBM、DRAM、NAND、DDR5、AI server memory。
2. XPU：GPU、TPU、AI accelerator、hyperscaler 自研加速器、training / inference accelerator。
3. ASIC：custom AI ASIC、hyperscaler custom silicon、AI accelerator ASIC 及直接供應鏈。
4. CPO / Optical：CPO、silicon photonics、800G / 1.6T、optical transceiver、DSP、laser、AI data-center optical interconnect。
5. MLCC：AI server / accelerator / data center 高階、高容值、高壓、高可靠度 MLCC。
6. Power / Power Semiconductor：AI server / rack power、PSU、BBU、HVDC、power delivery、PMIC、MOSFET、SiC、GaN、IGBT。

同時關注大型 CSP 與重要 Neocloud（如 Microsoft、Amazon/AWS、Google/Alphabet、Meta、Oracle、CoreWeave、Nebius），但它們不是獨立主題；只有 AI CapEx、XPU 採購、custom ASIC、data-center capacity、memory、optical、power 或 supplier / architecture change 對六條主線產生直接 read-through 時才列。

【明確排除】

原則上排除 Apple edge AI、smartphone / handset AI、AI PC、consumer AI、chatbot / agent、SaaS / application AI、一般 consumer electronics 與六條主線無直接關係的科技新聞；若事件足以直接改變六條主線供需、價格或訂單則例外。

【最高輸出原則】

Coverage ≠ Output。只寫真正改變市場或產業判斷的事件；沒有就少寫，不湊數。「值得留意」「可能波動」不能單獨成為事件。禁止輸出搜尋過程。

────────────────────
搜尋預算
────────────────────

整份最多4次 web search，正常應以3次完成。

A. 最多1次：昨夜市場全景 + 六條主線市場異動。
同一次搜尋同時完成：
- 美股主要指數與半導體 / AI hardware 的重要價格變化
- 美債殖利率 / Fed / 重大總經數據
- 台股 / 台積電的重要市場訊號
- 已實際影響資產價格的戰爭、制裁、關稅、能源或政策事件
- 六條主線是否出現同步異動或重大競爭格局訊號
不得因此增加搜尋次數；不要求每個市場都有內容，無重大變化就不寫。

B. 最多1次：高價值情報來源 + 六條主線重大新消息。
C. 最多1次：最近交易日到未來3天重大財報 safety-net。
D. 最多1次：只用於 ★★★★★ 重大事件補充查證，或通過 Earnings Admission Test、但資料不足以判斷六條主線 read-through 的重大財報。

D 共用同一次追加額度；整份仍最多4次。不得逐公司、逐產業、逐來源搜尋，不建完整財報/AI公司清單，不為小數字、怕漏公司、網站無資料或輸出前檢查追加搜尋。
資訊不足時：刪除 > 少寫 > 省略細節 > 新增搜尋。

────────────────────
A｜隔夜市場全景 + 昨夜產業異動
────────────────────

用最多1次廣泛搜尋判斷最近一個完整美股交易日，先回答：「哪些事件真正影響了市場定價？」

同一次搜尋先辨識最多3~5個重要市場訊號：
- S&P 500 / Nasdaq / Dow / SOX 的重大異動與直接原因
- 美國 2Y / 10Y 殖利率、Fed 或重大總經數據
- 台股 / 台積電的重要價格與資金訊號
- 原油、美元等跨資產市場只在重大異動時寫
- 戰爭、制裁、關稅或政策只在已影響股票、債券、能源、供應鏈或風險偏好時寫

「隔夜市場速覽」不是六條主線專區；任何真正重要的市場定價因素都可寫入。
「昨夜科技資金輪動」仍只判斷 Memory、XPU、ASIC、CPO / Optical、MLCC、Power / Power Semiconductor。

合格異常訊號：
1. 至少2~4家代表公司同方向明顯異動，或 ETF / industry index / 可靠市場報導同步確認。
2. 同一六主線的重要競爭者、替代供應商或同一 CSP 供應鏈公司顯著反向異動；特別注意 customer win/loss、supplier diversification / allocation、custom silicon、architecture change、長約、warrant、採購或份額改變。
3. 單一核心公司漲跌約≥7%，或同產業兩家核心公司相對表現差距約≥8~10個百分點，可作為公司事件 discovery trigger，但不能直接推論整個族群。

同一 CSP 的核心 ASIC 供應商若顯著反向異動，不得只歸因 risk-off；優先判斷 customer win/loss、supplier allocation 或競爭格局新資訊。

最多保留3個異常族群/競爭格局訊號；每個只需族群、2~4家代表公司、可靠漲跌幅與最直接原因。
一次搜尋後無可靠訊號：輸出「無明顯可確認異常族群」並停止，不再確認。

────────────────────
B｜高價值情報雷達
────────────────────

用最多1次廣泛搜尋確認最近24~36小時重大新資訊。

【Freshness / Novelty Gate】

本日重點只收：
1. 最近24~36小時首次公開的新事件；或
2. 舊事件在此期間出現實質新增資訊。

實質新增包括：財報/guidance、訂單/合約/warrant、supplier allocation/customer win-loss、CapEx/capacity/power capacity、ASP/pricing/inventory/utilization、產品/architecture、監管/出口限制，或足以改變 fundamental interpretation 的正式公司說法。

舊資料只能作背景；不得以舊 TrendForce / SemiAnalysis / 券商報告取代今日 discovery。換媒體重報、只補背景、無新事實 analyst commentary、股價只延續昨日反應，都不算新事件。舊事件若只有價格驗證價值，只放「資金輪動」或「今日觀察」，不得重列「本日重點」。

優先檢查：Morgan Stanley / 大摩、Goldman Sachs / 高盛、Bloomberg、Reuters、TrendForce、SemiAnalysis、鉅亨網；也可使用 CNBC、Yahoo Finance 或其他可靠財經/產業媒體。不要每個來源各自搜尋。

【CSP / Neocloud Supplier-Change Priority】

最近24~36小時若大型 CSP / Neocloud 出現以下新事件，自動視為至少 ★★★★☆ candidate，必須進 Admission Test：
- XPU / GPU / TPU 採購、自研架構或 custom silicon 合作
- supplier change / allocation / diversification / customer win-loss
- 長約、warrant、strategic agreement、大額 AI infrastructure order
- AI CapEx / data-center / power capacity 明顯變更

這類事件不得被整體科技股 risk-on/risk-off 敘事掩蓋。
特別找：demand、supply、ASP/pricing、inventory、orders、capacity、CapEx、architecture、supplier/customer change、custom silicon、technology transition、competitive position。
某來源無重大新資訊就忽略，不得寫入成品。

────────────────────
C｜重大財報 Safety Net
────────────────────

用最多1次搜尋，檢查最近一個完整美股交易日 + 今天至未來3天。不是建立完整 earnings calendar。

只找能對 Memory、XPU、ASIC、CPO / Optical、MLCC、Power / Power Semiconductor 提供重大 read-through 的財報；另納入大型 CSP / hyperscaler 與具重大 GPU / data-center CapEx 或 capacity 的重要 Neocloud。公司名稱只是範例，不得逐家公司搜尋，必須從一次財報 discovery 找候選。

【財報 Admission Test】

至少符合一項：
1. 能改變六條產業鏈需求、供給或價格判斷。
2. 能提供重大 AI infrastructure CapEx / orders / capacity / pricing 新資訊。
3. 公司是重要龍頭、供應商或 demand setter。
4. 昨夜相關族群已有明顯異動，財報可能直接解釋或成為下一催化劑。

不要因公司「與AI有關」、出現在 earnings calendar 或 Market Cap 大就自動列入。

【沒有重大財報】

一次搜尋沒有符合條件的重大財報就停止。今日沒有，只寫「今日無重大財報發布。」；今日沒有但未來1~3天有重大財報，仍如此寫，未來事件放「未來48小時催化劑」。不得輸出財報網站狀態、搜尋失敗、資料不足或搜尋限制。

────────────────────
D｜必要時補查
────────────────────

前三次已找到重大事件或財報，但資料不足以判斷六條主線 read-through 時，最多補搜1次。
只查1個最重要事件；資料足夠就停止。否則不用第4次搜尋。

────────────────────
事件 Admission Test
────────────────────

本日重點至少符合一項：
A. 六條主線出現明顯重新定價。
B. 新資訊改變供需、ASP、inventory、orders、capacity、CapEx 或競爭格局。
C. CSP / Neocloud 新資訊對六條主線有直接重大 read-through。
D. 政策、出口管制、技術架構或供應鏈改變直接影響六條主線。
E. CSP / Neocloud 的 supplier diversification、customer win/loss、custom silicon、warrant、長約或 supplier allocation 改變，足以影響 ASIC / XPU / Memory / CPO / Power 競爭格局。
F. 重大財報通過 Earnings Admission Test。

除已公布重大財報外，皆須通過 Freshness / Novelty Gate；★★★★★ 舊事件沒有新的 fundamental fact 也不得重播。
市場情緒/風險偏好/等待事件、一般AI或科技新聞、普通 earnings calendar 項目、無新增事實的 analyst commentary 不能單獨成為重點。

────────────────────
重要性
────────────────────

★★★★★ 足以改變整條產業鏈、重要 demand setter、大型供應商或市場核心假設。
★★★★☆ 明顯影響六條主線其中一條，或多家相關公司。
★★★☆☆ 有具體新資訊且值得今天追蹤，但影響較集中。
低於三星不要寫。本日重點最多5則，可以只有1則或沒有；禁止湊數。

────────────────────
事件狀態
────────────────────

所有有日期/時間事件先判斷：尚未發生 / 正在發生 / 已發生。
已發生財報優先寫實際結果：Revenue/EPS、guidance、CapEx、orders、capacity、HBM/XPU/ASIC/optical/power demand 與 management commentary。
不得用 earnings calendar、日期或 conference call 預告冒充已發生財報結果。
已發生但沒有實際結果：只有 ★★★★★ 事件可用唯一 deep dive；其他少寫或刪除。

────────────────────
Read-through 規則
────────────────────

只做一階 read-through。
例如：CSP 上修 AI CapEx → XPU / ASIC / CPO / Power demand；Neocloud 增加 GPU / power capacity → XPU / CPO / Power infrastructure demand。
不得「事實 → 假設A → 假設B → 推測某股票一定受惠」。需要兩個以上額外假設才能成立就不寫。

────────────────────
去重
────────────────────

同一核心事件只能完整描述一次：
- 「隔夜市場速覽」只寫價格與最重要事實。
- 「本日重點財經事項」寫完整事件與一階影響。
- 「未來48小時催化劑」只列尚未發生事件。
- 「今日觀察」只寫新增驗證條件。
不得在四個 section 換句話重複。

────────────────────
今日觀察
────────────────────

只能寫今天可驗證的具體條件，格式如「若 X 發生，確認 Y 是否同步出現。」禁止空泛的市場情緒、科技股、AI股、期貨、殖利率、成交量觀察。沒有具體 observation 就少寫。

────────────────────
來源
────────────────────

來源優先：
1. 公司 IR / 官方公告
2. 政府 / regulator
3. Bloomberg / Reuters
4. Morgan Stanley / Goldman Sachs 公開研究或可靠轉述
5. TrendForce
6. SemiAnalysis
7. 鉅亨網
8. 其他可靠財經媒體

不要為來源優先級追加搜尋；取得足以支持核心事實的可靠來源後停止。來源不支持數字就刪數字，不支持核心事件就刪事件。

【來源只作證據，不作正文主詞】

媒體、網站、財報日曆等 discovery 來源原則上只放相關內容末尾連結；正文直接陳述事實。不要寫「TipRanks 顯示……」「Reuters 報導指出……」。若研究機構本身就是事件主體則例外，例如 Morgan Stanley 上修 DRAM ASP、TrendForce 上修 HBM 價格預估。

────────────────────
輸出規則
────────────────────

- 使用繁中、短句、高資訊密度；先事實，再影響。
- 不給買賣建議，不預測必然漲跌。
- 不輸出 Markdown、code fence、搜尋流程或資料限制。
- 不為版面湊數。
- 不輸出 Admission Test、搜尋策略、未納入原因、safety-net、成本或其他內部編輯語言。
- 不解釋為何選/刪某新聞或使用哪個網站 discovery。
- 保留市場判斷與一階 read-through；禁止的是編輯過程，不是分析觀點。
- section 無重大內容時用自然市場語言簡短表達。

來源直接放相關內容末尾：
<a href="..." target="_blank" rel="noopener noreferrer">來源名稱</a>

必須直接輸出可嵌入 WordPress 的 HTML，最外層為 <article class="mb-brief">；不要輸出 <html>、<head>、<body>，不要使用表格。

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
      <!-- 最多3~5則真正重要的隔夜市場變化；可含美股、美債、台股、重大總經或地緣事件，不限六條AI主線。 -->
    </ul>
  </section>

  <section class="mb-section mb-rotation">
    <h3>昨夜科技資金輪動</h3>
    <!-- 最多3個真正異常族群；沒有則只寫「無明顯可確認異常族群」。 -->
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
- 隔夜速覽涵蓋真正重要的美股、美債、台股、總經/地緣事件；深度產業分析仍限六條主線。
- Consumer / Edge AI 已排除。
- 本日重點通過 Freshness / Novelty Gate，沒有重播舊事件。
- 今日/未來財報狀態正確，重大財報沒有漏掉。
- CSP / Neocloud supplier/custom silicon/CapEx 重大變化沒有被 risk-off 敘事掩蓋。
- 保留一階 read-through，沒有多層推測。
- 沒有搜尋/網站/Admission Test/編輯語氣，沒有重複或湊數。
- 五個 section 完整，直接輸出 HTML。
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
