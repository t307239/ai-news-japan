#!/usr/bin/env python3
"""
AI副業研究科 - 海外AIニュース日本語まとめサイト 自動生成スクリプト
使い方: python3 generate_website.py
毎日generate_daily_content.pyの後に実行される

GitHub Pages用の静的HTMLを生成してgit pushする
"""

import os, json, datetime, urllib.request, urllib.parse, urllib.error, time, subprocess, re

# ===== ~/.secrets からAPIキーを補完（環境変数が未設定の場合）=====
def _load_secrets():
    path = os.path.expanduser("~/.secrets")
    if not os.path.exists(path): return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            line = line.removeprefix("export").strip()
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
_load_secrets()

# ===== 設定 =====
# GitHub Actions では環境変数から取得、ローカルではハードコード値を使用
IS_CI          = os.environ.get("GITHUB_ACTIONS") == "true"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

if IS_CI:
    # GitHub Actions: スクリプトはscripts/にあるのでリポジトリルートは1階層上
    SITE_DIR   = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    CACHE_FILE = os.path.join(SITE_DIR, "website_cache.json")
else:
    SITE_DIR   = os.path.expanduser("~/Claude/website")
    CACHE_FILE = os.path.expanduser("~/Claude/website_cache.json")

GITHUB_REPO    = "t307239/ai-news-japan"
GA4_MEASUREMENT_ID = os.environ.get("GA4_ID", "G-XXXXXXXXXX")

# ===== キャッシュ管理（変更なしなら更新スキップ）=====
def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {"titles": []}

def save_cache(items):
    titles = [item.get("title", "") for item in items]
    with open(CACHE_FILE, "w") as f:
        json.dump({"titles": titles, "updated": datetime.datetime.now().isoformat()}, f)

def has_new_content(items, cache):
    new_titles = set(item.get("title", "") for item in items)
    old_titles = set(cache.get("titles", []))
    new_count = len(new_titles - old_titles)
    print(f"[INFO] 新着記事: {new_count}件")
    return new_count >= 2  # 2件以上新しければ更新

# ===== HackerNews + Reddit 収集（generate_daily_content.pyと同じ） =====
def fetch_hackernews_ai():
    items = []
    try:
        req = urllib.request.Request(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            headers={"User-Agent": "AI-News-Japan/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as res:
            story_ids = json.loads(res.read())[:80]
        ai_keywords = ["ai", "llm", "gpt", "claude", "gemini", "machine learning",
                       "neural", "openai", "anthropic", "deepmind", "agent",
                       "chatgpt", "copilot", "diffusion", "transformer", "robotics"]
        for sid in story_ids:
            if len(items) >= 6: break
            try:
                req = urllib.request.Request(
                    f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                    headers={"User-Agent": "AI-News-Japan/1.0"}
                )
                with urllib.request.urlopen(req, timeout=5) as res:
                    item = json.loads(res.read())
                title = (item.get("title") or "").lower()
                if any(kw in title for kw in ai_keywords):
                    items.append({
                        "source": "HackerNews",
                        "title":  item.get("title", ""),
                        "url":    item.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                        "score":  item.get("score", 0),
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"[WARNING] HackerNews: {e}")
    return items

def fetch_reddit_ai():
    """Reddit RSSフィード経由でAI記事を収集（JSON APIは403になるためRSSを使用）"""
    import xml.etree.ElementTree as ET
    BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    items = []
    for sub in ["MachineLearning", "LocalLLaMA", "artificial"]:
        if len(items) >= 6: break
        try:
            url = f"https://www.reddit.com/r/{sub}/hot.rss?limit=10"
            req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
            with urllib.request.urlopen(req, timeout=10) as res:
                root = ET.fromstring(res.read())
            NS = "{http://www.w3.org/2005/Atom}"
            entries = root.findall(f"{NS}entry")
            for entry in entries[:3]:
                if len(items) >= 6: break
                title = (entry.findtext(f"{NS}title") or "").strip()
                link = ""
                for lel in entry.findall(f"{NS}link"):
                    if lel.get("rel", "alternate") == "alternate":
                        link = lel.get("href", "").strip()
                        break
                if not title or not link: continue
                items.append({
                    "source": f"Reddit r/{sub}",
                    "title":  title,
                    "url":    link,
                    "score":  0,
                })
            print(f"[OK] Reddit r/{sub}: {len([x for x in items if f'r/{sub}' in x['source']])}件収集")
        except Exception as e:
            print(f"[WARNING] Reddit r/{sub}: {e}")
    return items

# ===== RSS（TechCrunch AI / MIT Tech Review / The Verge AI）=====
def fetch_rss_ai():
    """TechCrunch AI・MIT Tech Review・The VergeのRSSからAIニュースを収集"""
    import xml.etree.ElementTree as ET
    # (URL, ソース名, AI専用フィードかどうか)
    # AI専用フィード=Trueの場合はキーワードフィルタをスキップ
    RSS_SOURCES = [
        ("https://techcrunch.com/category/artificial-intelligence/feed/",         "TechCrunch AI",  True),
        ("https://www.technologyreview.com/topic/artificial-intelligence/feed/",  "MIT Tech Review", True),
        ("https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",     "The Verge AI",   True),
    ]
    ai_keywords = ["ai", "llm", "gpt", "claude", "gemini", "openai", "anthropic",
                   "machine learning", "neural", "model", "agent", "robot",
                   "chatbot", "language", "intelligence", "automation", "deepmind"]
    BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    items = []
    for rss_url, source_name, ai_only in RSS_SOURCES:
        before = len(items)
        try:
            req = urllib.request.Request(rss_url, headers={"User-Agent": BROWSER_UA})
            with urllib.request.urlopen(req, timeout=10) as res:
                root = ET.fromstring(res.read())
            entries = root.findall(".//item")
            if not entries:
                entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
            for entry in entries[:8]:
                if len(items) >= 9: break
                title = (entry.findtext("title") or
                         entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
                # ★Atom対応: rel="alternate"のlinkを優先、なければ最初のlinkを使う
                NS = "{http://www.w3.org/2005/Atom}"
                link = ""
                for lel in entry.findall(f"{NS}link") + entry.findall("link"):
                    rel  = lel.get("rel", "alternate")
                    href = (lel.text or lel.get("href") or "").strip()
                    if href and rel == "alternate":
                        link = href
                        break
                if not link:
                    for lel in entry.findall(f"{NS}link") + entry.findall("link"):
                        href = (lel.text or lel.get("href") or "").strip()
                        if href:
                            link = href
                            break
                if not title or not link: continue
                if not ai_only and not any(kw in title.lower() for kw in ai_keywords):
                    continue
                items.append({
                    "source": source_name,
                    "title":  title,
                    "url":    link,
                    "score":  0,
                })
            print(f"[OK] {source_name}: {len(items) - before}件収集")
        except Exception as e:
            print(f"[WARNING] {source_name}: {e}")
    return items

# ===== Geminiで日本語要約 =====
def translate_with_gemini(items):
    if not items:
        return []
    titles = "\n".join([f"{i+1}. {item['title']}" for i, item in enumerate(items)])
    prompt = f"""以下の英語のAIニュースタイトルを日本語に翻訳し、**日本語だけで読んで内容が完全に理解できる**詳しい要約を書いてください。
英語が読めない日本人ユーザー向けなので、元記事を読まなくても要点がわかるレベルにしてください。

必ず各項目を番号付きで「番号. 日本語タイトル｜要約文」の形式で返してください。
番号は元の番号と一致させてください。余分な説明文は不要です。

{titles}

返答形式（この形式のみ、他のテキスト不要）:
1. [日本語タイトル]｜[4〜5文の詳しい日本語要約。何が起きたか・なぜ重要か・どんな影響があるか・日本人にとって何が使えるかを含める]
2. [日本語タイトル]｜[4〜5文の詳しい日本語要約]
..."""

    data = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 5000}}
    # (モデル名, APIバージョン) の順に試す
    models = [
        ("gemini-2.5-flash",          "v1beta"),
        ("gemini-2.5-pro",            "v1beta"),
        ("gemini-2.0-flash",          "v1beta"),
        ("gemini-2.0-flash",          "v1"),
        ("gemini-2.0-flash-lite-001", "v1beta"),
        ("gemini-1.5-flash-latest",   "v1beta"),
    ]
    for model, api_ver in models:
        url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model}:generateContent?key={GEMINI_API_KEY}"
        req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                result = json.loads(res.read())
                text   = result["candidates"][0]["content"]["parts"][0]["text"]
                print(f"[DEBUG] Gemini応答:\n{text[:300]}")
                lines  = [l.strip() for l in text.strip().split("\n") if l.strip()]
                matched = 0
                for line in lines:
                    # 番号ベースで正確にマッチング（位置ではなく番号で紐付け）
                    m = re.match(r'^(\d+)\.\s*(.+)', line)
                    if not m:
                        continue
                    idx = int(m.group(1)) - 1  # 0始まりに変換
                    if idx < 0 or idx >= len(items):
                        continue
                    rest = m.group(2)
                    # 区切り文字は｜または| どちらでも対応
                    sep_match = re.split(r'[｜|]', rest, maxsplit=1)
                    if len(sep_match) == 2:
                        title_clean = sep_match[0].strip().strip('[]')
                        summary     = sep_match[1].strip().strip('[]')
                        if title_clean:
                            items[idx]["title_ja"]   = title_clean
                            items[idx]["summary_ja"] = summary
                            matched += 1
                print(f"[OK] Gemini翻訳完了（{matched}/{len(items)}件）")
                if matched == 0:
                    print(f"[WARNING] 翻訳結果がゼロ件 — モデルを変更してリトライ")
                    continue  # 次のモデルを試す
                return items
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"[WARNING] Gemini翻訳エラー ({model}/{api_ver}): {e} — 30秒待機してリトライ")
                time.sleep(30)
            else:
                print(f"[WARNING] Gemini翻訳エラー ({model}/{api_ver}): {e}")
        except Exception as e:
            print(f"[WARNING] Gemini翻訳エラー ({model}/{api_ver}): {e}")
    print("[WARNING] Gemini翻訳に失敗 — 英語タイトルのまま表示します")
    return items

# ===== ソースに応じたアイコン・色 =====
SOURCE_META = {
    "HackerNews":          {"icon": "🔶", "color": "#ff6314", "short": "HN"},
    "Reddit r/MachineLearning": {"icon": "🤖", "color": "#ff4500", "short": "ML"},
    "Reddit r/LocalLLaMA": {"icon": "🦙", "color": "#ff6b35", "short": "LLM"},
    "Reddit r/artificial":  {"icon": "🧠", "color": "#e74c3c", "short": "AI"},
    "TechCrunch AI":       {"icon": "🟢", "color": "#00d084", "short": "TC"},
    "MIT Tech Review":     {"icon": "🔵", "color": "#0072ce", "short": "MIT"},
    "The Verge AI":        {"icon": "🟣", "color": "#7c3aed", "short": "VG"},
}

def get_source_meta(source):
    for key, meta in SOURCE_META.items():
        if key in source:
            return meta
    return {"icon": "📰", "color": "#7c6de8", "short": source[:2].upper()}

# ===== アフィリエイトバナー定義（A8.net登録後にURLを差し替える）=====
# ※ URLはA8.net/もしもアフィリエイトで発行したリンクに変更してください
AFFILIATE_BANNERS = [
    {
        "icon": "🎓",
        "title": "AIスキル習得ならスタビジアカデミー",
        "desc": "データサイエンス・AI・Python を実践的に学べるオンラインスクール。副業・転職を本気で目指す方に。",
        "cta": "無料で詳細を見る →",
        "url": "https://af.moshimo.com/af/c/click?a_id=5419800&p_id=3953&pc_id=9863&pl_id=54677&url=https%3A%2F%2Ftoukei-lab.com%2Fachademy%2F",
        "pixel": "https://i.moshimo.com/af/i/impression?a_id=5419800&p_id=3953&pc_id=9863&pl_id=54677",
        "color": "#00b4d8",
    },
    {
        "icon": "📈",
        "title": "DMM FX — 口座開設で最大40,000円キャッシュバック",
        "desc": "業界最狭水準のスプレッドで取引できるDMM FX。新規口座開設＋1回の取引で豪華特典プレゼント！",
        "cta": "無料で口座開設 →",
        "url": "https://px.a8.net/svt/ejp?a8mat=4AZD88+9SGMWI+1WP2+6JC82",
        "pixel": "https://www18.a8.net/0.gif?a8mat=4AZD88+9SGMWI+1WP2+6JC82",
        "color": "#f5a623",
    },
]

def make_affiliate_banner(banner):
    pixel_tag = f'<img src="{banner["pixel"]}" width="1" height="1" style="border:none;position:absolute;" alt="" loading="lazy">' if banner.get("pixel") else ""
    return f"""
        <a class="aff-card" href="{banner['url']}" target="_blank" rel="nofollow noopener noreferrer" referrerpolicy="no-referrer-when-downgrade">
            {pixel_tag}
            <span class="aff-pr">PR</span>
            <span class="aff-icon">{banner['icon']}</span>
            <div class="aff-body">
                <p class="aff-title">{banner['title']}</p>
                <p class="aff-desc">{banner['desc']}</p>
            </div>
            <span class="aff-cta" style="color:{banner['color']}">{banner['cta']}</span>
        </a>"""

# ===== HTML生成 =====
def generate_html(items, today):
    date_str  = today.strftime("%Y年%m月%d日")
    date_iso  = today.strftime("%Y-%m-%d")
    now_str   = datetime.datetime.now().strftime("%H:%M")
    ga4_id    = GA4_MEASUREMENT_ID

    cards_html = ""
    aff_index  = 0  # 使用するバナーのインデックス
    for rank, item in enumerate(items, 1):
        title_ja   = item.get("title_ja", item.get("title", ""))
        summary_ja = item.get("summary_ja", "")
        source     = item.get("source", "")
        url        = item.get("url", "#")
        score      = item.get("score", 0)
        meta       = get_source_meta(source)

        src_key = "hn" if "HackerNews" in source else "reddit"
        hot_badge = '<span class="hot-badge">🔥 HOT</span>' if score >= 800 else ""

        rank_colors = {1: "#f5c518", 2: "#b0b0b0", 3: "#cd7f32"}
        rank_color  = rank_colors.get(rank, "#444466")
        rank_style  = f"color:{rank_color}; border-color:{rank_color}44; background:{rank_color}11"

        # アコーディオン用のユニークID
        card_id = f"summary-{rank}"
        # 要約があればアコーディオン展開ボタン、なければ非表示
        if summary_ja:
            summary_accordion = f"""
            <div class="summary-accordion" id="{card_id}" style="display:none">
                <p class="card-summary">{summary_ja}</p>
            </div>"""
            btn_summary = f"""<button class="btn-translate" onclick="toggleSummary('{card_id}', this)" aria-expanded="false">
                    📖 要約を読む
                </button>"""
        else:
            summary_accordion = ""
            btn_summary = ""
        delay = (rank - 1) * 0.07

        cards_html += f"""
        <article class="card" data-source="{src_key}" style="animation-delay:{delay:.2f}s">
            <div class="card-top-row">
                <span class="rank-badge" style="{rank_style}">#{rank}</span>
                <div class="card-badges">
                    {hot_badge}
                    <span class="source-badge" style="background:{meta['color']}18; color:{meta['color']}; border-color:{meta['color']}40">
                        {meta['icon']} {meta['short']}
                    </span>
                </div>
                <span class="score-badge">▲ {score:,}</span>
            </div>
            <h2 class="card-title">
                <a href="{url}" target="_blank" rel="noopener noreferrer">{title_ja}</a>
            </h2>
            {summary_accordion}
            <div class="card-footer">
                {btn_summary}
                <a class="btn-original" href="{url}" target="_blank" rel="noopener noreferrer">
                    元記事 →
                </a>
            </div>
        </article>"""

        # 3件ごとにアフィリエイトバナーを挿入（バナーはループして繰り返す）
        if rank % 3 == 0 and AFFILIATE_BANNERS:
            banner = AFFILIATE_BANNERS[aff_index % len(AFFILIATE_BANNERS)]
            cards_html += make_affiliate_banner(banner)
            aff_index += 1

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="海外の最新AIニュースを毎日日本語でまとめてお届け。HackerNews・TechCrunch・MIT Tech Reviewから厳選した情報をAIが翻訳・要約。">
    <meta name="google-site-verification" content="Rzb7d4uOtZVeyRmI4nqMmR3LVT5ODz4h00oXC2bYP58">
    <!-- Google Analytics 4 -->
    <script async src="https://www.googletagmanager.com/gtag/js?id={ga4_id}"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){{dataLayer.push(arguments);}}
      gtag('js', new Date());
      gtag('config', '{ga4_id}');
    </script>
    <!-- OGP -->
    <meta property="og:title" content="AI速報ジャパン｜{date_str}の海外AIニュース日本語まとめ">
    <meta property="og:description" content="HackerNews・TechCrunch・MIT Tech ReviewのAIニュースをAIが日本語に翻訳・要約してお届け。毎日更新。">
    <meta property="og:type" content="website">
    <meta property="og:url" content="https://t307239.github.io/ai-news-japan/">
    <meta property="og:image" content="https://t307239.github.io/ai-news-japan/ogp.png">
    <meta property="og:site_name" content="AI速報ジャパン">
    <meta property="og:locale" content="ja_JP">
    <!-- Twitter Card -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:site" content="@ai_fukugyo_ken7">
    <meta name="twitter:title" content="AI速報ジャパン｜{date_str}の海外AIニュース">
    <meta name="twitter:description" content="HackerNews・TechCrunch・MIT Tech ReviewのAIニュースをAIが日本語に翻訳・要約してお届け。毎日更新。">
    <meta name="twitter:image" content="https://t307239.github.io/ai-news-japan/ogp.png">
    <title>AI速報ジャパン｜{date_str}の海外AIニュース日本語まとめ</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
    <style>
        /* ===== CSS変数 ===== */
        :root {{
            --bg:        #09090f;
            --bg-card:   #111119;
            --bg-card2:  #17172200;
            --border:    #1e1e2e;
            --border-h:  #7c6de8;
            --text:      #e2e2ee;
            --text-sub:  #7a7a9a;
            --text-dim:  #44445a;
            --accent:    #7c6de8;
            --accent-lt: #a78bfa;
            --gold:      #f5c518;
            --hot:       #ff5f5f;
        }}
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        html {{ scroll-behavior:smooth; }}

        /* ===== アニメーション定義 ===== */
        @keyframes gradientShift {{
            0%   {{ background-position: 0% 50%; }}
            50%  {{ background-position: 100% 50%; }}
            100% {{ background-position: 0% 50%; }}
        }}
        @keyframes fadeUp {{
            from {{ opacity:0; transform:translateY(18px); }}
            to   {{ opacity:1; transform:translateY(0); }}
        }}
        @keyframes pulse {{
            0%, 100% {{ opacity:1; }}
            50%       {{ opacity:0.5; }}
        }}

        /* ===== ベース ===== */
        body {{
            font-family: 'Noto Sans JP', -apple-system, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            line-height: 1.65;
        }}

        /* ===== ヘッダー ===== */
        header {{
            position: sticky; top: 0; z-index: 200;
            background: rgba(9,9,15,0.85);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-bottom: 1px solid var(--border);
            padding: 22px 16px 18px;
            text-align: center;
        }}
        .logo {{
            font-size: 1.8rem;
            font-weight: 700;
            letter-spacing: -1px;
            line-height: 1;
        }}
        .logo-ai {{
            background: linear-gradient(135deg, #a78bfa, #7c6de8, #60a5fa);
            background-size: 200% 200%;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            animation: gradientShift 4s ease infinite;
        }}
        .tagline {{
            color: var(--text-sub);
            font-size: 0.8rem;
            margin-top: 5px;
            letter-spacing: 0.3px;
        }}
        .header-meta {{
            display: flex;
            justify-content: center;
            gap: 8px;
            margin-top: 12px;
            flex-wrap: wrap;
        }}
        .hbadge {{
            display: inline-flex; align-items: center; gap: 4px;
            background: #13131e; border: 1px solid #2a2a3e;
            border-radius: 20px; padding: 3px 11px;
            font-size: 0.74rem; color: var(--text-sub);
        }}
        .hbadge.live {{
            border-color: #4a3a8a;
            color: var(--accent-lt);
        }}
        .live-dot {{
            width: 6px; height: 6px;
            background: var(--accent-lt);
            border-radius: 50%;
            animation: pulse 1.8s ease-in-out infinite;
        }}

        /* ===== フィルタータブ ===== */
        .filter-wrap {{
            display: flex;
            justify-content: center;
            gap: 8px;
            padding: 16px 16px 4px;
        }}
        .filter-btn {{
            background: #13131e;
            border: 1px solid #2a2a3e;
            border-radius: 24px;
            padding: 5px 18px;
            font-size: 0.78rem;
            font-family: 'Noto Sans JP', sans-serif;
            color: var(--text-sub);
            cursor: pointer;
            transition: all 0.18s;
        }}
        .filter-btn:hover {{ border-color: var(--accent); color: var(--text); }}
        .filter-btn.active {{
            background: linear-gradient(135deg, #2d1f6e, #1d2855);
            border-color: var(--accent);
            color: var(--accent-lt);
            font-weight: 700;
        }}

        /* ===== コンテンツ ===== */
        .container {{
            max-width: 800px;
            margin: 0 auto;
            padding: 20px 16px 50px;
        }}
        .section-header {{
            display: flex; align-items: center; gap: 10px;
            margin-bottom: 20px;
        }}
        .section-label {{
            font-size: 0.66rem; font-weight: 700;
            color: var(--accent); letter-spacing: 2.5px;
            text-transform: uppercase;
        }}
        .section-line {{ flex:1; height:1px; background:var(--border); }}
        .count-text {{ font-size: 0.7rem; color: var(--text-dim); }}

        /* ===== カード ===== */
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 20px 22px 16px;
            margin-bottom: 12px;
            position: relative; overflow: hidden;
            transition: border-color 0.2s, transform 0.18s, box-shadow 0.2s;
            animation: fadeUp 0.45s ease both;
        }}
        /* カード上部ラインをhover時に光らせる */
        .card::before {{
            content: '';
            position: absolute; top:0; left:0; right:0; height:2px;
            background: linear-gradient(90deg, var(--accent), var(--accent-lt), transparent);
            opacity: 0; transition: opacity 0.2s;
        }}
        .card:hover {{
            border-color: #3a2a6a;
            transform: translateY(-3px);
            box-shadow: 0 12px 40px rgba(124,109,232,0.14);
        }}
        .card:hover::before {{ opacity: 1; }}
        .card.hidden {{ display: none !important; }}

        /* カード上段 */
        .card-top-row {{
            display: flex; align-items: center; gap: 8px;
            margin-bottom: 12px; flex-wrap: wrap;
        }}
        .rank-badge {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.8rem; font-weight: 700;
            border: 1px solid; border-radius: 8px;
            padding: 2px 8px; line-height: 1.4;
            flex-shrink: 0;
        }}
        .card-badges {{ display: flex; gap: 6px; flex: 1; }}
        .hot-badge {{
            display: inline-flex; align-items: center; gap: 3px;
            background: rgba(255,95,95,0.12);
            border: 1px solid rgba(255,95,95,0.35);
            border-radius: 6px; padding: 2px 8px;
            font-size: 0.68rem; font-weight: 700;
            color: var(--hot); letter-spacing: 0.3px;
        }}
        .source-badge {{
            display: inline-flex; align-items: center; gap: 3px;
            border: 1px solid; border-radius: 6px;
            padding: 2px 8px; font-size: 0.68rem; font-weight: 700;
            letter-spacing: 0.3px;
        }}
        .score-badge {{
            margin-left: auto; font-size: 0.7rem; color: var(--text-dim);
            white-space: nowrap;
        }}

        /* カードタイトル・本文 */
        .card-title {{
            font-size: 1.05rem; font-weight: 700;
            line-height: 1.55; margin-bottom: 9px;
        }}
        .card-title a {{
            color: var(--text); text-decoration: none;
            transition: color 0.15s;
        }}
        .card-title a:hover {{ color: var(--accent-lt); }}
        .card-summary {{
            font-size: 0.86rem; color: var(--text-sub);
            line-height: 1.75; margin-bottom: 14px;
        }}
        .card-footer {{
            display: flex; justify-content: flex-end; gap: 8px;
            border-top: 1px solid var(--border); padding-top: 10px;
            margin-top: 4px; flex-wrap: wrap;
        }}
        .btn-translate {{
            display: inline-flex; align-items: center; gap: 4px;
            background: rgba(124,109,232,0.12);
            border: 1px solid rgba(124,109,232,0.35);
            border-radius: 7px; padding: 4px 12px;
            font-size: 0.76rem; font-weight: 700;
            color: var(--accent-lt);
            font-family: 'Noto Sans JP', sans-serif;
            cursor: pointer;
            transition: background 0.15s, border-color 0.15s;
        }}
        .btn-translate:hover {{
            background: rgba(124,109,232,0.22);
            border-color: rgba(124,109,232,0.6);
        }}
        .btn-translate.open {{
            background: rgba(124,109,232,0.25);
            border-color: var(--accent);
            color: #fff;
        }}

        /* アコーディオン展開エリア */
        .summary-accordion {{
            overflow: hidden;
            transition: max-height 0.35s ease, opacity 0.3s ease;
            margin-bottom: 4px;
        }}
        .btn-original {{
            display: inline-flex; align-items: center;
            font-size: 0.76rem; color: var(--text-dim);
            text-decoration: none; font-weight: 500;
            transition: color 0.15s; padding: 4px 4px;
        }}
        .btn-original:hover {{ color: var(--text-sub); }}

        /* ===== 空状態 ===== */
        .empty-state {{
            text-align: center; padding: 60px 20px;
            color: var(--text-dim);
        }}
        .empty-state .icon {{ font-size: 3rem; margin-bottom: 12px; }}

        /* ===== フッター ===== */
        footer {{
            text-align: center; padding: 36px 16px;
            border-top: 1px solid var(--border);
            color: var(--text-dim); font-size: 0.78rem;
        }}
        footer p {{ margin-bottom: 5px; }}
        .sns-links {{
            display: flex; justify-content: center; gap: 10px;
            margin: 16px 0; flex-wrap: wrap;
        }}
        .sns-links a {{
            display: inline-flex; align-items: center; gap: 6px;
            background: #111119; border: 1px solid var(--border);
            border-radius: 10px; padding: 8px 18px;
            color: var(--text-sub); text-decoration: none;
            font-size: 0.82rem; font-weight: 500;
            transition: border-color 0.2s, color 0.2s;
        }}
        .sns-links a:hover {{ border-color: var(--accent); color: var(--accent-lt); }}
        .footer-copy {{
            margin-top: 10px; font-size: 0.72rem; color: var(--text-dim);
        }}

        /* ===== アフィリエイトカード ===== */
        .aff-card {{
            display: flex; align-items: center; gap: 12px;
            background: linear-gradient(135deg, #131320, #1a1a2a);
            border: 1px solid #2e2e50;
            border-radius: 14px; padding: 14px 18px;
            margin-bottom: 12px; text-decoration: none;
            transition: border-color 0.2s, transform 0.15s, box-shadow 0.2s;
            cursor: pointer;
            position: relative;
        }}
        .aff-card:hover {{
            border-color: #5a4aaa;
            transform: translateY(-2px);
            box-shadow: 0 8px 28px rgba(100,80,200,0.15);
        }}
        .aff-pr {{
            position: absolute; top: 8px; right: 10px;
            font-size: 0.6rem; color: #55557a;
            background: #1e1e30; border: 1px solid #33335a;
            border-radius: 4px; padding: 1px 5px;
            letter-spacing: 0.5px;
        }}
        .aff-icon {{ font-size: 1.8rem; flex-shrink: 0; line-height: 1; }}
        .aff-body {{ flex: 1; min-width: 0; }}
        .aff-title {{
            font-size: 0.9rem; font-weight: 700;
            color: var(--text); margin-bottom: 3px;
        }}
        .aff-desc {{
            font-size: 0.76rem; color: var(--text-sub); line-height: 1.5;
        }}
        .aff-cta {{
            font-size: 0.78rem; font-weight: 700;
            white-space: nowrap; flex-shrink: 0;
        }}

        /* ===== AIツール紹介セクション ===== */
        .tools-section {{
            margin-top: 40px;
            padding-top: 28px;
            border-top: 1px solid var(--border);
        }}
        .tools-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
            gap: 12px;
            margin-top: 16px;
        }}
        .tool-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px; padding: 16px;
            text-decoration: none;
            transition: border-color 0.2s, transform 0.15s;
            display: block;
        }}
        .tool-card:hover {{
            border-color: var(--border-h);
            transform: translateY(-2px);
        }}
        .tool-card-header {{
            display: flex; align-items: center; gap: 10px; margin-bottom: 8px;
        }}
        .tool-icon {{ font-size: 1.5rem; }}
        .tool-name {{ font-size: 0.9rem; font-weight: 700; color: var(--text); }}
        .tool-tag {{
            display: inline-block; font-size: 0.65rem; font-weight: 700;
            background: rgba(124,109,232,0.15); color: var(--accent);
            border: 1px solid rgba(124,109,232,0.3); border-radius: 4px;
            padding: 1px 6px; margin-bottom: 6px;
        }}
        .tool-desc {{ font-size: 0.78rem; color: var(--text-sub); line-height: 1.55; }}

        /* ===== レスポンシブ ===== */
        @media (max-width: 600px) {{
            header {{ padding: 18px 14px 14px; }}
            .logo {{ font-size: 1.5rem; }}
            .card {{ padding: 16px 15px 13px; border-radius: 13px; }}
            .card-title {{ font-size: 0.96rem; }}
            .card-summary {{ font-size: 0.83rem; }}
            .aff-card {{ flex-wrap: wrap; }}
            .aff-cta {{ width: 100%; text-align: right; }}
            .tools-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <!-- ヘッダー -->
    <header>
        <div class="logo">
            <span class="logo-ai">AI速報</span><span>ジャパン</span>
        </div>
        <p class="tagline">海外最新AIニュースを毎日日本語でお届け</p>
        <div class="header-meta">
            <span class="hbadge live"><span class="live-dot"></span> {date_str} {now_str} 更新</span>
            <span class="hbadge">🔶 HackerNews</span>
            <span class="hbadge">🤖 Reddit</span>
        </div>
    </header>

    <!-- フィルタータブ -->
    <div class="filter-wrap">
        <button class="filter-btn active" onclick="filterCards('all',this)">すべて ({len(items)})</button>
        <button class="filter-btn" onclick="filterCards('hn',this)">🔶 HackerNews</button>
        <button class="filter-btn" onclick="filterCards('reddit',this)">🤖 Reddit</button>
    </div>

    <!-- メインコンテンツ -->
    <main class="container">
        <div class="section-header">
            <span class="section-label">Today's AI News</span>
            <span class="section-line"></span>
            <span class="count-text" id="countText">{len(items)}件</span>
        </div>
        <div id="cardList">
            {cards_html}
        </div>
        <div class="empty-state" id="emptyState" style="display:none">
            <div class="icon">🔍</div>
            <p>該当する記事がありません</p>
        </div>

        <!-- おすすめAI書籍セクション -->
        <section class="tools-section">
            <div class="section-header">
                <span class="section-label">AI Books</span>
                <span class="section-line"></span>
                <span class="count-text">おすすめAI書籍</span>
            </div>
            <div style="display:flex; gap:12px; flex-wrap:wrap; margin-top:16px;">
                <a href="https://rpx.a8.net/svt/ejp?a8mat=4AZD88+9T22IA+2HOM+BWGDT&rakuten=y&a8ejpredirect=https%3A%2F%2Fhb.afl.rakuten.co.jp%2Fhgc%2Fg00q0724.2bo11c45.g00q0724.2bo12179%2Fa26031088273_4AZD88_9T22IA_2HOM_BWGDT%3Fpc%3Dhttps%253A%252F%252Fitem.rakuten.co.jp%252Fbook%252F17890842%252F%26amp%3Bm%3Dhttp%253A%252F%252Fm.rakuten.co.jp%252Fbook%252Fi%252F21282617%252F%26amp%3Brafcid%3Dwsc_i_is_33f72da33714639c415e592c9633ecd7" rel="nofollow" referrerpolicy="no-referrer-when-downgrade" target="_blank"
                   style="display:flex; align-items:center; gap:10px; background:#111119; border:1px solid #1e1e2e; border-radius:12px; padding:12px 16px; text-decoration:none; transition:border-color 0.2s; flex:1; min-width:240px;">
                    <img src="https://thumbnail.image.rakuten.co.jp/@0_mall/book/cabinet/6068/9784815626068_1_5.jpg?_ex=64x64" width="48" height="48" style="border-radius:6px; flex-shrink:0;" alt="ChatGPT書籍">
                    <div>
                        <p style="font-size:0.82rem; font-weight:700; color:#e2e2ee; margin-bottom:3px;">この一冊で全部わかる ChatGPT &amp; Copilotの教科書</p>
                        <p style="font-size:0.75rem; color:#7a7a9a;">楽天ブックス ・ <span style="color:#f5c518; font-weight:700;">1,980円</span> ・ 感想18件</p>
                        <span style="font-size:0.65rem; color:#55557a; background:#1e1e30; border:1px solid #33335a; border-radius:4px; padding:1px 5px;">PR</span>
                    </div>
                </a>
                <a href="https://rpx.a8.net/svt/ejp?a8mat=4AZD88+9T22IA+2HOM+BWGDT&rakuten=y&a8ejpredirect=https%3A%2F%2Fhb.afl.rakuten.co.jp%2Fhgc%2Fg00q0724.2bo11c45.g00q0724.2bo12179%2Fa26031088273_4AZD88_9T22IA_2HOM_BWGDT%3Fpc%3Dhttps%253A%252F%252Fitem.rakuten.co.jp%252Fbook%252F17923392%252F%26amp%3Bm%3Dhttp%253A%252F%252Fm.rakuten.co.jp%252Fbook%252Fi%252F21314579%252F%26amp%3Brafcid%3Dwsc_i_is_33f72da33714639c415e592c9633ecd7" rel="nofollow" referrerpolicy="no-referrer-when-downgrade" target="_blank"
                   style="display:flex; align-items:center; gap:10px; background:#111119; border:1px solid #1e1e2e; border-radius:12px; padding:12px 16px; text-decoration:none; transition:border-color 0.2s; flex:1; min-width:240px;">
                    <img src="https://thumbnail.image.rakuten.co.jp/@0_mall/book/cabinet/3510/9784297143510_1_2.jpg?_ex=64x64" width="48" height="48" style="border-radius:6px; flex-shrink:0;" alt="ChatGPT書籍">
                    <div>
                        <p style="font-size:0.82rem; font-weight:700; color:#e2e2ee; margin-bottom:3px;">図解即戦力 ChatGPTのしくみと技術がこれ1冊でしっかりわかる教科書</p>
                        <p style="font-size:0.75rem; color:#7a7a9a;">楽天ブックス ・ <span style="color:#f5c518; font-weight:700;">2,640円</span></p>
                        <span style="font-size:0.65rem; color:#55557a; background:#1e1e30; border:1px solid #33335a; border-radius:4px; padding:1px 5px;">PR</span>
                    </div>
                </a>
            </div>
            <img border="0" width="1" height="1" src="https://www18.a8.net/0.gif?a8mat=4AZD88+9T22IA+2HOM+BWGDT" alt="">
        </section>

        <!-- AIツール紹介セクション -->
        <section class="tools-section">
            <div class="section-header">
                <span class="section-label">AI Tools</span>
                <span class="section-line"></span>
                <span class="count-text">おすすめAIツール</span>
            </div>
            <div class="tools-grid">
                <a class="tool-card" href="https://chat.openai.com" target="_blank" rel="noopener noreferrer">
                    <div class="tool-card-header">
                        <span class="tool-icon">🤖</span>
                        <span class="tool-name">ChatGPT</span>
                    </div>
                    <span class="tool-tag">無料〜</span>
                    <p class="tool-desc">OpenAIの対話AI。文章生成・コード作成・翻訳など万能。副業の出発点に最適。</p>
                </a>
                <a class="tool-card" href="https://claude.ai" target="_blank" rel="noopener noreferrer">
                    <div class="tool-card-header">
                        <span class="tool-icon">✳️</span>
                        <span class="tool-name">Claude</span>
                    </div>
                    <span class="tool-tag">無料〜</span>
                    <p class="tool-desc">Anthropicの高性能AI。長文の要約・分析・コーディング支援が得意。</p>
                </a>
                <a class="tool-card" href="https://www.canva.com" target="_blank" rel="noopener noreferrer">
                    <div class="tool-card-header">
                        <span class="tool-icon">🎨</span>
                        <span class="tool-name">Canva</span>
                    </div>
                    <span class="tool-tag">無料〜</span>
                    <p class="tool-desc">AIを使った画像生成・SNSバナー作成が誰でも簡単に。副業コンテンツ制作に必須。</p>
                </a>
                <a class="tool-card" href="https://www.notion.so" target="_blank" rel="noopener noreferrer">
                    <div class="tool-card-header">
                        <span class="tool-icon">📝</span>
                        <span class="tool-name">Notion AI</span>
                    </div>
                    <span class="tool-tag">無料〜</span>
                    <p class="tool-desc">メモ・タスク管理にAIが統合。記事のアウトライン生成や要約も一瞬でできる。</p>
                </a>
            </div>
        </section>
    </main>

    <!-- フッター -->
    <footer>
        <p>HackerNews・Reddit から厳選し、AI が日本語に翻訳・要約しています。</p>
        <p style="font-size:0.73rem; margin-top:3px;">情報の正確性は保証されません。必ず元記事をご確認ください。</p>
        <div class="sns-links">
            <a href="https://x.com/ai_fukugyo_ken7" target="_blank" rel="noopener">𝕏 @ai_fukugyo_ken7</a>
            <a href="https://note.com/" target="_blank" rel="noopener">📝 note</a>
        </div>
        <p class="footer-copy">© 2026 AI速報ジャパン</p>
    </footer>

    <!-- フィルター & アコーディオン スクリプト -->
    <script>
        function filterCards(src, btn) {{
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const cards = document.querySelectorAll('#cardList .card');
            let visible = 0;
            cards.forEach(c => {{
                const show = src === 'all' || c.dataset.source === src;
                c.classList.toggle('hidden', !show);
                if (show) visible++;
            }});
            document.getElementById('countText').textContent = visible + '件';
            document.getElementById('emptyState').style.display = visible ? 'none' : 'block';
        }}

        function toggleSummary(id, btn) {{
            const el = document.getElementById(id);
            const isOpen = el.style.display !== 'none';
            if (isOpen) {{
                el.style.display = 'none';
                btn.textContent = '📖 要約を読む';
                btn.classList.remove('open');
                btn.setAttribute('aria-expanded', 'false');
            }} else {{
                el.style.display = 'block';
                btn.textContent = '📖 要約を閉じる';
                btn.classList.add('open');
                btn.setAttribute('aria-expanded', 'true');
            }}
        }}
    </script>
</body>
</html>"""
    return html

# ===== Telegram 通知（generate_daily_content.py と同じ Bot を流用）=====
TELEGRAM_TOKEN   = "8707035299:AAG_8NRnRb86zNBXLXZAo8asdhpPmK-9mIQ"
TELEGRAM_CHAT_ID = "8738265696"

def notify_telegram(items):
    """サイト更新時にTelegramへ上位3記事を通知する"""
    if not items:
        return
    top3 = items[:3]
    lines = ["📡 <b>AI速報ジャパン 更新しました！</b>", ""]
    for i, item in enumerate(top3, 1):
        title = item.get("title_ja", item.get("title", ""))
        url   = item.get("url", "#")
        score = item.get("score", 0)
        lines.append(f"#{i} <b>{title}</b>")
        lines.append(f"   ▲{score:,}  <a href='{url}'>元記事</a>")
        lines.append("")
    lines.append("👉 <a href='https://t307239.github.io/ai-news-japan/'>サイトを見る</a>")
    message = "\n".join(lines)

    try:
        tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data   = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }).encode()
        req = urllib.request.Request(tg_url, data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as res:
            result = json.loads(res.read())
            if result.get("ok"):
                print("[OK] Telegram通知を送信しました！")
            else:
                print(f"[WARNING] Telegram通知失敗: {result}")
    except Exception as e:
        print(f"[WARNING] Telegram通知エラー: {e}")

# ===== sitemap.xml 生成（Google Search Console 用）=====
SITE_URL = "https://t307239.github.io/ai-news-japan"

def generate_sitemap(today):
    """過去30日分のアーカイブページを含むsitemap.xmlを生成する"""
    urls = [f"{SITE_URL}/"]  # トップページ
    for i in range(30):
        d = today - datetime.timedelta(days=i)
        urls.append(f"{SITE_URL}/{d.strftime('%Y-%m-%d')}.html")

    entries = "\n".join([
        f"  <url><loc>{u}</loc><changefreq>daily</changefreq><priority>{'1.0' if u.endswith('/') else '0.8'}</priority></url>"
        for u in urls
    ])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{entries}
</urlset>"""

# ===== ファイル保存 & Git push =====
def save_and_push(html, today):
    os.makedirs(SITE_DIR, exist_ok=True)
    index_path = os.path.join(SITE_DIR, "index.html")
    archive_path = os.path.join(SITE_DIR, f"{today.strftime('%Y-%m-%d')}.html")

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write(html)

    # sitemap.xml を生成
    sitemap_path = os.path.join(SITE_DIR, "sitemap.xml")
    with open(sitemap_path, "w", encoding="utf-8") as f:
        f.write(generate_sitemap(today))
    print(f"[OK] sitemap.xml生成: {sitemap_path}")
    print(f"[OK] HTML生成: {index_path}")

    # GitHub Pagesにpush（GitHub Actions環境ではワークフローが担当するためスキップ）
    if IS_CI:
        print("[INFO] GitHub Actions環境のためgit pushをスキップ（ワークフローが処理）")
    elif GITHUB_REPO:
        try:
            for cmd in [
                ["git", "-C", SITE_DIR, "add", "."],
                ["git", "-C", SITE_DIR, "commit", "-m", f"Update: {today.strftime('%Y-%m-%d')}"],
                ["git", "-C", SITE_DIR, "push"],
            ]:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if r.returncode != 0:
                    print(f"[WARNING] git: {r.stderr.strip()}")
            print("[OK] GitHub Pagesに公開しました！")
        except Exception as e:
            print(f"[WARNING] Git push失敗: {e}")
    else:
        print("[INFO] GITHUB_REPOが未設定のため、git pushをスキップします")
        print(f"[INFO] ファイルを確認: {index_path}")

# ===== メイン =====
def main():
    today = datetime.date.today()
    print("=" * 40)
    print(f"AI速報ジャパン サイト生成 | {today.strftime('%Y年%m月%d日')}")
    print("=" * 40)

    import sys
    force = "--force" in sys.argv  # 強制更新オプション

    print("[INFO] ニュース収集中...")
    items = fetch_hackernews_ai() + fetch_reddit_ai() + fetch_rss_ai()
    print(f"[OK] {len(items)}件収集")

    # キャッシュと比較して変更がなければスキップ
    if not force and items:
        cache = load_cache()
        if not has_new_content(items, cache):
            print("[INFO] 新着ニュースが少ないため更新をスキップします（--forceで強制更新）")
            return

    if items:
        print("[INFO] Geminiで日本語に翻訳・要約中...")
        items = translate_with_gemini(items)
        save_cache(items)  # キャッシュ更新

    print("[INFO] HTML生成中...")
    html = generate_html(items, today)
    save_and_push(html, today)
    notify_telegram(items)   # Telegram通知
    print("[OK] 完了！更新しました")

if __name__ == "__main__":
    main()
