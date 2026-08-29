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
先換算目前美東時間。已發生資訊範圍＝最近一個美股交易日前一日收盤後至現在。
已公布財報必須找實際 results / guidance；尚未公布才列未來催化劑。
事件 freshness 以實際發生／公告時間判斷，不以新聞刊登日期判斷。
更早事件只能作背景；若沒有新 fundamental fact，不得重列「本日重點」。

【核心任務】

這不是一般科技新聞早報。深度分析只找可能改變以下 AI Infrastructure 供需、價格、訂單、CapEx、產能或競爭格局的新訊號：

1. Memory：HBM、DRAM、NAND、DDR5、AI server memory。
2. XPU：GPU、TPU、AI accelerator、hyperscaler 自研加速器。
3. ASIC：custom AI ASIC、hyperscaler custom silicon 及直接供應鏈。
4. CPO / Optical：CPO、silicon photonics、800G / 1.6T、transceiver、DSP、laser。
5. MLCC：AI server / accelerator / data center 高階 MLCC。
6. Power：AI server / rack power、PSU、BBU、HVDC、PMIC、MOSFET、SiC、GaN、IGBT。

大型 CSP / Neocloud 只有在 AI CapEx、XPU、custom ASIC、data-center capacity、memory、optical、power 或 supplier / architecture change 對六條主線有直接影響時才列。

原則上排除 Apple edge AI、smartphone、AI PC、consumer AI、chatbot、SaaS 與六條主線無直接關係的科技新聞。

【最高原則】

Coverage ≠ Output。只寫真正改變市場或產業判斷的事件；沒有就少寫，不湊數。
隔夜行情只能使用最近一個完整交易日的實際價格；更早行情不得冒充昨夜行情。
分析只做一步直接影響，不做多層推測。

────────────────────
搜尋預算
────────────────────

整份最多4次 web search，正常3次完成。

A. 最多1次：最近完整交易日市場全景 + 六主線市場異動。
B. 最多1次：已發生資訊範圍內六主線重大新消息。
C. 最多1次：已公布重大財報結果 + 今天至未來3天重大財報。
D. 最多1次：只補查一個已找到但資訊不足的 ★★★★★ 事件或重大財報。

不得逐公司、逐產業、逐來源搜尋。資訊不足時：刪除 > 少寫 > 省略細節 > 新增搜尋。

────────────────────
A｜隔夜市場全景 + 資金輪動
────────────────────

用1次廣泛搜尋確認最近一個完整美股交易日及最新完整台股交易日。

「隔夜市場速覽」最多3~5則，只寫真正影響定價的：

* S&P 500 / Nasdaq / Dow / SOX 與直接原因
* 美國 2Y / 10Y、Fed、重大總經
* 台股 / 台積電
* 已實際影響資產價格的地緣、制裁、關稅、能源或政策

「昨夜科技資金輪動」只判斷六條主線。合格訊號：

1. 至少2~4家代表公司同方向明顯異動，或 ETF / industry index / 可靠報導同步確認。
2. 同一供應鏈重要公司顯著反向異動，且有 customer win/loss、supplier allocation、custom silicon、architecture change 等直接原因。
3. 單一核心公司約≥7%異動可作 discovery trigger，但不能直接推論整個族群。

最多3個異常族群。一次搜尋無可靠訊號就寫「無明顯可確認異常族群」，不再追加搜尋。

────────────────────
B｜高價值情報
────────────────────

用1次廣泛搜尋找「已發生資訊範圍」內首次公開或有實質新增資訊的六主線事件。

實質新增包括：guidance、訂單/合約、supplier/customer change、CapEx/capacity、ASP/pricing、inventory/utilization、architecture、監管/出口限制或正式公司說法。

換媒體重報、只補背景、無新事實 analyst commentary、單純延續前一日股價反應，都不算新事件。
更早事件若只有價格驗證價值，只放「資金輪動」或「今日觀察」，不得重列「本日重點」。

優先來源：公司 IR / 官方公告、政府、Reuters / Bloomberg、Morgan Stanley / Goldman Sachs 公開轉述、TrendForce、SemiAnalysis、鉅亨網及其他可靠財經媒體。不要逐來源搜尋。

大型 CSP / Neocloud 若出現 XPU / custom silicon、supplier change、長約、AI CapEx、data-center / power capacity 重大變更，必須進事件篩選。

────────────────────
C｜重大財報結果 + 未來財報
────────────────────

用1次搜尋同時完成：

1. 找「已發生資訊範圍」內已公布且對六主線重要的財報實際結果。
2. 找今天至未來3天尚未公布的重要財報。

已公布財報優先寫 Revenue/EPS、guidance、CapEx、orders、capacity、AI data-center / XPU / ASIC / memory / optical / power commentary。
不得用 earnings calendar、preview 或 conference-call 預告代替已公布結果。

財報至少符合一項才列：

* 改變六條主線需求、供給或價格判斷
* 提供重大 AI infrastructure CapEx / orders / capacity / pricing 新資訊
* 公司是重要龍頭、供應商或 demand setter
* 能直接解釋最近交易日相關族群異動

尚未公布者只放「未來48小時催化劑」；沒有就自然寫無重大催化劑。

────────────────────
事件篩選
────────────────────

本日重點至少符合一項：
A. 六條主線明顯重新定價。
B. 新資訊改變 demand / supply / ASP / inventory / orders / capacity / CapEx。
C. CSP / Neocloud 新資訊直接改變六主線判斷。
D. 政策、出口管制、architecture 或供應鏈改變直接影響六主線。
E. supplier diversification、customer win/loss、custom silicon、長約或 allocation 改變競爭格局。
F. 重大財報通過上述條件。

★★★★★ 改變產業鏈、重要 demand setter 或核心市場假設。
★★★★☆ 明顯影響一條主線或多家公司。
★★★☆☆ 有具體新資訊、影響較集中。
低於三星不寫。本日重點最多5則。

────────────────────
分析與去重
────────────────────

只寫「事實 → 一步直接影響」。需要兩個以上額外假設才能成立就不寫。
正文禁止出現「read-through」「一階 read-through」「Admission Test」「Freshness Gate」「safety-net」「candidate」等內部分析詞。

同一事件只能完整描述一次：

* 隔夜市場速覽：價格 + 最重要事實
* 本日重點：完整新事件 + 直接影響
* 未來48小時：只列尚未發生事件
* 今日觀察：只列新的可驗證條件

────────────────────
今日觀察
────────────────────

只寫今天可驗證的具體條件，例如「若 X 發生，確認 Y 是否同步出現」。
禁止空泛的市場情緒、AI股、期貨、殖利率、成交量觀察。沒有就少寫。

────────────────────
來源與輸出
────────────────────

來源優先：公司 IR / 官方公告 > 政府 / regulator > Reuters / Bloomberg > MS / GS 公開研究或可靠轉述 > TrendForce > SemiAnalysis > 鉅亨網 > 其他可靠財經媒體。
取得足以支持核心事實的可靠來源後停止，不為來源層級追加搜尋。
來源只作證據，正文直接陳述事實；相關內容末尾放： <a href="..." target="_blank" rel="noopener noreferrer">來源名稱</a>

使用繁中、短句、高資訊密度；先事實，再影響。
不給買賣建議，不預測必然漲跌。
不輸出 Markdown、code fence、搜尋流程、資料限制、內部編輯語言或未納入原因。
section 無重大內容時用自然市場語言簡短表達。
直接輸出可嵌入 WordPress 的 HTML；不要輸出 <html>、<head>、<body>，不要使用表格。

固定結構：

<article class="mb-brief">
  <header class="mb-header">
    <p class="mb-kicker">每日市場早報</p>
    <h2>每日市場早報｜{now.strftime('%Y/%m/%d')}</h2>
    <p class="mb-updated">更新時間：{now.strftime('%Y/%m/%d %H:%M')} 台北時間</p>
  </header>

  <section class="mb-section mb-overnight">
    <h3>隔夜市場速覽</h3>
    <ul></ul>
  </section>

  <section class="mb-section mb-rotation">
    <h3>昨夜科技資金輪動</h3>
  </section>

  <section class="mb-section mb-events">
    <h3>本日重點財經事項</h3>
    <ol></ol>
  </section>

  <section class="mb-section mb-catalysts">
    <h3>未來48小時催化劑</h3>
    <ul></ul>
  </section>

  <section class="mb-section mb-watchlist">
    <h3>今日觀察</h3>
    <ul></ul>
  </section>
</article>

最後自行確認：

* 行情是最近完整交易日，不是前一日。
* 已公布重大財報寫的是實際結果。
* 本日重點沒有重播舊事件。
* CSP / Neocloud 重大 supplier/custom silicon/CapEx 變化沒有漏掉。
* 沒有多層推測、內部分析詞、重複或湊數。
* 五個 section 完整，直接輸出 HTML。
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
