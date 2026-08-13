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
    earnings_end = (now + dt.timedelta(days=3)).date()

    return f"""
你是無人值守的每日財經早報產生器。這是排程任務，不是對話。

今天台北時間：{now.strftime('%Y/%m/%d %H:%M')}
請整理最近一個完整美股交易日的市場資料。
財報只需檢查「最近一個完整美股交易日」以及今天至 {earnings_end.isoformat()}（未來3天）。

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

【搜尋原則】

這是一份每日市場速報，不是深度研究報告。

目標是用少量搜尋快速找出真正值得今天注意的資訊。

不要建立完整產業資料庫。
不要建立完整財報候選池。
不要逐家公司進行研究。
不要逐個科技產業展開搜尋。
不要搜尋公司 IR。
不要搜尋 SEC filing。
不要搜尋交易所文件。
不要為查不到的小數字反覆搜尋。
已取得足夠資訊後立即停止延伸搜尋。

不要因為本提示中出現 AI、GPU、HBM、光通訊等字眼，
就逐一搜尋這些產業。

────────────────────
1. 昨夜科技股異常族群
────────────────────

優先使用：

https://finviz.com/map

查看最近一個完整美股交易日的科技股表現。

只關注科技相關股票。
非科技類股不用整理。

找出是否存在「多家公司同步且明顯大漲或大跌」的科技族群。

最多列出真正最明顯的 1~3 個族群。

例如可能是：
光通訊、半導體、記憶體、AI 基礎設施等。

以上只是分類範例，
不准為了檢查這些例子而逐一發動搜尋。

只有實際看到多家公司同步異動，
才能判定為異常族群。

每個入選族群只需要：

- 族群名稱
- 2~4 家代表公司
- 代表公司漲跌幅
- 最直接的異動原因

若 Finviz 不足以判斷哪些族群異常，
也不要再搜尋其他網站重新掃描全部科技股。直接跳過任務。

不得因單一股票大漲或大跌，
自行推論整個族群異常。

沒有明顯異常的族群不要列。
不要為沒有異常的族群追加搜尋。

────────────────────
2. 未來3天重大科技財報
────────────────────

只查看：
https://seekingalpha.com/earnings/earnings-calendar

財報只使用 Earnings Calendar 月曆格子上「直接顯示的代表公司」。

不要點開當日全部公司名單。
不要掃描當日數百家公司。
不要重新建立重要公司清單。
不要使用產業關鍵字反向搜尋公司。

檢查兩個範圍：

1. 最近一個完整美股交易日月曆格子上直接顯示的代表公司。
2. 今天至未來3天月曆格子上直接顯示的代表公司。

其中若有科技、半導體、AI、光通訊、記憶體、雲端、資料中心等明顯重要公司，
直接視為重大科技財報候選。

例如月曆格子直接顯示 COHR、AMAT、LITE，
就直接納入，不需要再搜尋其他公司。

如果「昨夜科技異常族群」與這些代表公司屬於同一族群，
必須特別列出。

不要為了確認是否還有其他重要公司而展開完整財報清單或追加搜尋。

【異常族群 × 財報】

把第1項已經找出的異常科技族群，
直接與這份財報日曆做一次交叉比對。

如果異常族群中的重要公司
在最近一個完整美股交易日已公布，
或今天至未來3天即將公布財報，
必須特別列出。

例如：

昨夜光通訊多家公司同步大跌，
而 Lumentum 等同族群重要公司即將公布財報，

則需要指出：

- 昨夜光通訊族群的價格異常
- 哪家公司即將公布財報
- 財報日期
- 為何這份財報成為近期重要催化劑
- 市場最需要注意哪些需求、財測或產業訊號

若 Earnings Calendar 已提供日期，
直接使用即可。

不要求另外搜尋 IR、SEC 或其他官方文件確認。

如果沒有任何異常族群與近期財報產生交集，
不要為了找交集而繼續搜尋。

────────────────────
3. Goldman Sachs / 高盛、Morgan Stanley / 大摩、SemiAnalysis
────────────────────

搜尋最近的新消息。

只整理與以下主題真正有交易價值的內容：

- AI
- 半導體
- GPU
- HBM
- 資料中心
- 大型科技公司
- 科技產業
- 總體市場

不要固定要求 Goldman Sachs、Morgan Stanley、
SemiAnalysis 三家都一定有內容。

若其中某一家沒有真正重要的新消息，
直接跳過，不要寫「沒有重大消息」。

不要為了填滿這個區塊而反覆搜尋。

不要搜尋歷史舊報告充數。

只有近期而且對當日市場有意義的內容才寫。

────────────────────
4. Yahoo Finance / Yahoo 財經
────────────────────

搜尋 Yahoo Finance 上近期真正重要的：

- AI
- 半導體
- 大型科技公司
- 科技供應鏈
- 科技政策
- 重大公司事件

只保留對當日市場可能具有交易影響力的新聞。

不要把 Yahoo Finance 的一般科技新聞全部整理。

不要重複前面已經寫過的事件。

────────────────────
5. 本日重點財經事項
────────────────────

綜合上述資訊，
挑出今天最重要的具體事件。

合格事件必須至少包含明確主體與動作，例如：

- 公司公布財報或財測
- 公司發布重要產品
- 公司宣布併購
- 公司進行重大融資
- 重大分析師升降評
- 關稅
- 出口管制
- 政策或法規
- 重大資本支出
- 重要供應鏈變化
- 重要產業需求變化

以下不能單獨列為事件：

- 市場觀望
- 投資人等待
- 風險偏好
- 市場情緒
- 漲跌互見
- 受到多重因素影響

【重要性評分】

★★★★★

可能影響整體市場、重大產業鏈、大型權值股；

或：

昨夜某科技族群出現明顯同步異動，
且今天有同族群重要公司公布財報。

★★★★☆

可能明顯影響特定產業或多家公司。

★★★☆☆

值得今日追蹤，但影響較集中。

低於三星不要寫。

最多 10 則。

真正重要的事件不足 10 則時，
可以少於 10 則。

禁止為了湊滿 10 則繼續搜尋。

────────────────────
6. 未來48小時催化劑
────────────────────

列出未來48小時真正值得注意的少數催化劑。
只從前面已完成的搜尋結果中整理，不要為本節重新搜尋。
從既有結果中挑出未來48小時真正值得注意的少數催化劑。

優先：

- 重要科技財報
- 昨夜異常族群相關財報
- 重要產品發布
- 重要政策
- 重要監管事件
- 重大公司活動

若前面搜尋結果不足以支持某項催化劑，直接不列，不要為補足本節而新增搜尋。
不足時可以少寫，禁止湊數。

────────────────────
7. 今日觀察
────────────────────

只根據前面已取得的資訊整理，不要為本節重新搜尋。
列出今天交易時段最值得追蹤的 3～5 件事。

優先包括：

- 重要財報
- 昨夜異常科技族群後續價格
- 重大公司新聞
- 重要科技政策
- 高盛／大摩／SemiAnalysis 有交易價值的新觀點
- AI / 半導體重大消息

每一項都必須可以在今天實際觀察或驗證。

【寫作規則】

- 使用繁體中文。
- 句子短。
- 資訊密度高。
- 不寫市場作文。
- 先寫具體事實，再寫影響。
- 每則事件標題必須包含具體公司、機構、政策或族群名稱。
- 已經容易取得的重要數字盡量保留。
- 不要為了取得非必要數字另外搜尋。
- 不硬湊台灣供應鏈公司。
- 不固定列出台積電 ADR。
- 同一事件不要重複出現在不同新聞區塊。
- 若同一事件同時符合多個來源，只在最適合的位置寫一次。

【HTML 絕對規則】

只輸出以下形式的 WordPress HTML。

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
      <li>
        用 2~4 點簡短整理昨夜最重要的科技市場變化。
        可包含主要科技指數、重要大型科技股、重大政策或市場催化劑。
        不需要為了補齊所有市場指數而追加搜尋。
      </li>
    </ul>
  </section>

  <section class="mb-section mb-rotation">
    <h3>昨夜科技資金輪動</h3>
    <ol>
      <li>
        <strong>族群名稱｜明顯上漲／下跌</strong>
        <p><strong>代表公司：</strong>列出 2~4 家代表公司與漲跌幅。</p>
        <p><strong>異動原因：</strong>用一至兩句說明最直接原因，並附來源。</p>
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
      <li>列出真正重要的近期科技財報、政策、產品或其他催化劑。</li>
    </ul>
  </section>

  <section class="mb-section mb-watchlist">
    <h3>今日觀察</h3>
    <ul>
      <li>列出今天真正需要追蹤的 3~5 個事項。</li>
    </ul>
  </section>
</article>

【輸出前內部檢查】

不要輸出以下檢查過程。

確認：

- 是否先使用 Finviz Map 判斷昨夜科技異常族群。
- 是否沒有逐個科技產業搜尋。
- 是否只查看 Seeking Alpha Earnings Calendar 取得「最近一個完整美股交易日＋未來3天」的代表公司。
- 是否沒有用產業關鍵字反向搜尋財報公司。
- 是否沒有逐家公司搜尋 earnings。
- 是否沒有搜尋 IR、SEC 或交易所文件。
- 是否沒有建立完整財報候選池。
- 是否已完成異常族群與近期財報的一次交叉比對。
- 若有異常族群碰上近期重大財報，是否已特別列出。
- 是否只有真正重要的高盛、大摩、SemiAnalysis 新消息才寫。
- 是否只整理 Yahoo Finance 真正重要的科技新聞。
- 是否沒有為了湊滿 10 則事件而繼續搜尋。
- 是否沒有把同一事件重複寫多次。
- 是否包含「隔夜市場速覽」。
- 是否包含「昨夜科技資金輪動」。
- 是否包含「本日重點財經事項」。
- 是否包含「未來48小時催化劑」。
- 是否包含「今日觀察」。
- 是否直接輸出 HTML。
- 是否完全沒有 Markdown、搜尋流程、token、成本或工具使用資訊。
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