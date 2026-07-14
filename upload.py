# -*- coding: utf-8 -*-
"""
Hatena Blog 画像自動アップロード（GitHub Actions用）
Google Driveから画像取得 → Hatena Fotolifeに画像アップ → AtomPub APIでブログ記事投稿
WSSE認証使用
"""
import sys
import json
import os
import random
import hashlib
import base64
import html
from collections import Counter
from datetime import datetime, timezone, timedelta

import re
import xml.etree.ElementTree as ET

import requests
import everyday_content
try:
    import gdown
except ImportError:
    gdown = None
try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None
    ImageDraw = None

JST = timezone(timedelta(hours=9))

# --- 環境変数 ---
HATENA_ID = os.environ.get("HATENA_ID", "")
HATENA_API_KEY = os.environ.get("HATENA_API_KEY", "")
HATENA_BLOG_DOMAIN = os.environ.get("HATENA_BLOG_DOMAIN", "")
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID_HATENA", "")

PROFILE_LINK = os.environ.get("PROFILE_LINK", "").strip()
MANUAL_ARTICLE_PATH = os.environ.get("MANUAL_ARTICLE_PATH", "").strip()
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
UPLOADED_LOG = "uploaded_hatena.json"
CONTEXT_FILE_EXTENSIONS = {".md", ".txt", ".json"}
CONTEXT_SKIP_DIR_NAMES = {"__pycache__", "apex", "dlsite", "fanza"}
# 過去のMuscleLoveブランド画像(og.png)はフォールバックから外す。
# Drive画像が無い日は ensure_generated_image() の無難なカードのみを使う。
LOCAL_IMAGE_FILES = []
LOCAL_IMAGE_DIRS = ["images"]
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def env_or_default(key, default):
    """空文字を未設定扱いとしてデフォルトを返す"""
    value = os.environ.get(key)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


CONTEXT_MAX_FILES = int(env_or_default("CONTEXT_MAX_FILES", "15"))
CONTEXT_MAX_CHARS = int(env_or_default("CONTEXT_MAX_CHARS", "900"))
DRY_RUN = env_or_default("DRY_RUN", "0").lower() in {"1", "true", "yes", "on"}
DRY_RUN_OUTPUT = env_or_default("DRY_RUN_OUTPUT", "dry_run_hatena_article.html")
DEFAULT_CONTEXT_SOURCE_DIRS = ",".join(
    [
        "context",
        "../../../10_事業部/02_MuscleLove事業/ambient_agent_context.md",
        "../../../10_事業部/02_MuscleLove事業/content_queue/context_inbox.md",
        "../../../10_事業部/02_MuscleLove事業/reports/context_theme_radar_latest.md",
        "../../../10_事業部/02_MuscleLove事業/reports/ambient_status_latest.md",
        "../../../10_事業部/02_MuscleLove事業/hatena_drafts",
    ]
)
CONTEXT_SOURCE_DIRS = [
    p.strip()
    for p in env_or_default(
        "CONTEXT_SOURCE_DIRS",
        DEFAULT_CONTEXT_SOURCE_DIRS,
    ).split(",")
    if p.strip()
]

def build_profile_link_block():
    """任意のプロフィール/活動リンクを本文末に添える"""
    if not PROFILE_LINK:
        return ""
    return (
        "\n<p style=\"text-align:center;font-size:0.95em;\">"
        f'<a href="{PROFILE_LINK}" target="_blank" rel="noopener">プロフィール/活動リンクはこちら</a>'
        "</p>\n"
    )


# --- WSSE認証ヘッダー生成 ---
def wsse_header(username, api_key):
    """Hatena AtomPub用のWSSE認証ヘッダーを生成"""
    nonce = os.urandom(20)
    created = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    digest = hashlib.sha1(nonce + created.encode() + api_key.encode()).digest()
    return 'UsernameToken Username="{}", PasswordDigest="{}", Nonce="{}", Created="{}"'.format(
        username,
        base64.b64encode(digest).decode(),
        base64.b64encode(nonce).decode(),
        created
    )

def get_auth_headers():
    """WSSE認証ヘッダーを返す"""
    return {
        'X-WSSE': wsse_header(HATENA_ID, HATENA_API_KEY),
        'Accept': 'application/x.atom+xml, application/atom+xml',
    }

# --- タグマッピング ---
CONTENT_TAG_MAP = {
    'ai': ['AI', '制作メモ', '気になったこと'],
    'blog': ['ブログ運営', '制作メモ', '雑記'],
    'work': ['作業ログ', '日常', '近況メモ'],
    'daily': ['日常', '雑記', '個人ブログ'],
    'life': ['暮らし', '日常', '雑記'],
    'memo': ['メモ', '近況メモ', '雑記'],
    'trend': ['話題メモ', '気になったこと', '雑記'],
    'news': ['話題メモ', '気になったこと', '雑記'],
    'sns': ['SNS', '話題メモ', '近況メモ'],
    'game': ['ゲーム', '趣味', '雑記'],
    'gaming': ['ゲーム', '趣味', '雑記'],
    'steam': ['PCゲーム', 'ゲーム', '趣味'],
    'switch': ['Nintendo Switch', 'ゲーム', '趣味'],
    'ps5': ['PS5', 'ゲーム', '趣味'],
    'mahjong': ['麻雀', '趣味', '雑記'],
    'majang': ['麻雀', '趣味', '雑記'],
    'mj': ['麻雀', '趣味', '雑記'],
    'anime': ['アニメ', '雑談', '趣味'],
    'manga': ['漫画', '雑談', '趣味'],
    'music': ['音楽', '雑談', '趣味'],
    'movie': ['映画', '雑談', '趣味'],
    'food': ['食べ物', '日常', '雑記'],
    'health': ['体調管理', '暮らし', '日常'],
    'travel': ['外出メモ', '暮らし', '雑記'],
    'context': ['近況メモ', '雑記', '個人ブログ'],
}

BASE_TAGS = [
    '雑記ブログ', '日常', '個人ブログ', '近況メモ',
    '気になったこと', '暮らし', '趣味', '雑談',
]

# --- タイトルテンプレート ---
TITLE_TEMPLATES = [
    "{category} | 今日の雑記メモ",
    "{category} - 最近ちょっと気になっている話",
    "{category} | 雑談しながら近況整理",
    "{category} - 日常のコンテキスト拾い読み",
    "{category} | 好きなものと気になる話",
    "{category} - その日の流れで書くメモ",
    "{category} | 生活ログから見えたこと",
    "{category} - いろんなテーマを少しずつ",
    "{category} | 今日の関心ごとまとめ",
    "{category} - こういう話がしたかった",
    "{category} | なんとなく残しておきたい話",
    "{category} - 日常、趣味、作業のあいだ",
]

# --- カテゴリ名テンプレート ---
CATEGORY_TEMPLATES = [
    "雑記ブログ", "日常メモ", "近況雑談",
    "好きなものログ", "話題メモ", "今日のメモ",
    "趣味の話", "コンテキスト整理", "ゆるい日記",
    "気になる話", "生活ログ", "作業と日常",
]

# --- 本文HTMLテンプレート ---
BODY_TEMPLATES = [
    """<div style="text-align:center;">
<p><img src="{image_url}" alt="{title}" style="max-width:100%;" /></p>
<p style="font-size:1.1em;"><strong>{title}</strong></p>
<p>{description}</p>
<p style="margin-top:20px;">{hashtags}</p>
<hr />
<p>日常のコンテキストから拾ったテーマを、雑記ブログとしてゆるく残していきます。</p>
</div>""",
    """<div style="text-align:center;">
<p><img src="{image_url}" alt="{title}" style="max-width:100%;" /></p>
<p style="font-size:1.1em;"><strong>{title}</strong></p>
<p>{description}</p>
<p style="margin-top:20px;">{hashtags}</p>
<hr />
<p>特定ジャンルに寄せすぎず、その日に気になったことを適当に拾っていく場所です。</p>
</div>""",
    """<div style="text-align:center;">
<p><img src="{image_url}" alt="{title}" style="max-width:100%;" /></p>
<p style="font-size:1.1em;"><strong>{title}</strong></p>
<p>{description}</p>
<p style="margin-top:20px;">{hashtags}</p>
<hr />
<p>趣味、作業、流行りもの、生活の小ネタをその日の流れで少しずつ。</p>
</div>""",
    """<div style="text-align:center;">
<p><img src="{image_url}" alt="{title}" style="max-width:100%;" /></p>
<p style="font-size:1.1em;"><strong>{title}</strong></p>
<p>{description}</p>
<p style="margin-top:20px;">{hashtags}</p>
<hr />
<p>今日の自分の興味を、あとから読み返せる形でメモしておきます。</p>
</div>""",
]

# --- 説明文テンプレート ---
DESCRIPTION_TEMPLATES = [
    "今日は手元のコンテキストから、気になった話を雑記っぽく拾っておきます。",
    "人気の話題を追いながら、自分ならどこに引っかかるかをメモする回です。",
    "深い考察というより、日常の中で引っかかったことを軽く並べる感じです。",
    "趣味の話、作業の話、生活の小ネタをその日の気分でまとめます。",
    "最近の関心ごとを、あとで読み返して笑えるくらいの温度でまとめます。",
    "制作の合間に見ていた話題から、個人的に残しておきたいところだけ拾いました。",
    "今日は少し雑談寄り。日常、趣味、流行りものを混ぜて近況整理です。",
    "こういう小さな趣味のログが、意外とあとからプロフィール代わりになります。",
    "テーマは固定せず、その日のコンテキストから自然に出てきたものを拾います。",
    "がっつり解説ではなく、日常と趣味の間にある話を軽く書いています。",
]

STOP_WORDS = {
    "https", "http", "www", "com", "note", "with", "from", "that", "this",
    "file", "latest", "public", "current", "updated", "generated", "status",
    "queue", "ready", "rows", "line",
    "する", "して", "ある", "ない", "よう", "ため", "こと", "もの", "また", "です",
    "ます", "から", "まで", "など", "より", "れる", "られ", "できる", "いる",
    "github", "token", "secret", "password", "api", "apikey", "key", "env", "json",
}

SENSITIVE_KEYWORD_PATTERNS = [
    re.compile(r"^[a-f0-9]{16,}$"),
    re.compile(r"^[A-Za-z0-9_\-]{24,}$"),
    re.compile(r"(secret|token|password|apikey|api_key|credential|cookie)", re.I),
]

SENSITIVE_CONTEXT_PATTERNS = [
    re.compile(
        r"(secret|token|password|api[_-]?key|credential|cookie)"
        r"\s*[:=]?\s*[A-Za-z0-9_\-./+=]{3,}",
        re.I,
    ),
]


def resolve_context_path(source_path):
    """環境変数の相対パスを、このスクリプトの場所から解決する"""
    expanded = os.path.expandvars(os.path.expanduser(source_path))
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    return os.path.normpath(os.path.join(SCRIPT_DIR, expanded))


def iter_context_candidates(source_path):
    resolved = resolve_context_path(source_path)
    if os.path.isfile(resolved):
        return [resolved]
    if not os.path.isdir(resolved):
        print(f"Context source not found: {source_path} -> {resolved}")
        return []

    candidates = []
    for root, dirs, filenames in os.walk(resolved):
        dirs[:] = [d for d in dirs if d.lower() not in CONTEXT_SKIP_DIR_NAMES]
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in CONTEXT_FILE_EXTENSIONS:
                candidates.append(os.path.join(root, fname))
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[:CONTEXT_MAX_FILES]


def sanitize_context_text(raw):
    text = raw.replace("\ufeff", "")
    for pattern in SENSITIVE_CONTEXT_PATTERNS:
        text = pattern.sub("[redacted]", text)

    public_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped in {"---", "```"}:
            continue
        lowered = stripped.lower()
        if stripped.startswith("#"):
            continue
        if lowered.startswith(("updated:", "generated:", "last updated:", "this file is intentionally")):
            continue
        if any(word in lowered for word in ["secret", "token", "password", "credential", "cookie"]):
            continue
        if stripped.startswith(("```", "|")):
            continue
        public_lines.append(stripped)

    clean = re.sub(r"https?://\S+", "", " ".join(public_lines))
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def collect_context_snippets():
    """設定ファイル/ディレクトリから公開向けコンテキスト文字列を収集"""
    snippets = []
    seen_paths = set()
    for source in CONTEXT_SOURCE_DIRS:
        for path in iter_context_candidates(source):
            norm = os.path.normcase(os.path.abspath(path))
            if norm in seen_paths:
                continue
            seen_paths.add(norm)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = f.read().strip()
                if not raw:
                    continue
                clean = sanitize_context_text(raw)
                if not clean:
                    continue
                snippets.append(
                    {
                        "path": os.path.basename(path),
                        "text": clean[:CONTEXT_MAX_CHARS],
                        "mtime": os.path.getmtime(path),
                    }
                )
            except Exception as e:
                print(f"コンテキスト読込失敗: {path} ({e})")
    snippets.sort(key=lambda item: item["mtime"], reverse=True)
    return snippets


def extract_context_keywords(context_text):
    """コンテキストから頻出キーワードを抽出"""
    safe_context_text = context_text
    for pattern in SENSITIVE_CONTEXT_PATTERNS:
        safe_context_text = pattern.sub(" ", safe_context_text)
    tokens = re.findall(r"[A-Za-z0-9_]{3,}|[ぁ-んァ-ヶ一-龥]{2,}", safe_context_text.lower())
    words = [
        t for t in tokens
        if t not in STOP_WORDS
        and not t.isdigit()
        and not any(pattern.search(t) for pattern in SENSITIVE_KEYWORD_PATTERNS)
    ]
    ranked = [w for w, _ in Counter(words).most_common(8)]
    return ranked


def select_public_excerpt(text, max_chars=180):
    segments = re.split(r"(?<=[。.!?])\s+|\s+-\s+", text)
    for segment in segments:
        excerpt = segment.strip(" -#\t")
        if len(excerpt) < 18:
            continue
        lowered = excerpt.lower()
        if any(word in lowered for word in ["secret", "token", "password", "credential", "cookie"]):
            continue
        if "\\" in excerpt or "/" in excerpt[:80]:
            continue
        if len(excerpt) > max_chars:
            excerpt = excerpt[: max_chars - 1].rstrip() + "…"
        return excerpt
    return text[:max_chars].rstrip() + ("…" if len(text) > max_chars else "")


def build_context_paragraphs(excerpts, keywords):
    focus = "、".join(keywords[:4]) if keywords else "日常の作業ログ、気になった話、発信の改善"
    primary = excerpts[0] if excerpts else "手元のメモを見直すと、ただの作業報告ではなく、判断の理由や迷った点まで残すことが大事だと分かります。"
    secondary = excerpts[1] if len(excerpts) > 1 else primary
    tertiary = excerpts[2] if len(excerpts) > 2 else secondary

    paragraphs = [
        (
            f"今日のコンテキストを拾ってみると、中心にあるのは「{focus}」でした。"
            "ただ出来事を並べるだけだと、あとから読んだときに何が変わったのか分かりにくい。"
            "なので今日は、手元のメモに残っていた断片をそのまま貼るのではなく、そこから見える発見を少し長めに整理しておきます。"
        ),
        (
            f"まず気になったのは、「{primary}」というメモです。"
            "これは単なる作業ログというより、発信を続けるときの厚みの作り方に近い話だと思いました。"
            "情報を増やすだけなら自動化でかなり進められますが、読んだ人に残るのは、どこで迷ったのか、何を試して、どこを直したのかという判断の筋道です。"
        ),
        (
            f"次に見えたのは、「{secondary}」という流れです。"
            "ここから分かるのは、コンテンツ運用も一回作って終わりではなく、欠けている部分を見つけて少しずつ補うものだということです。"
            "大きな改善を一気に狙うより、本文の情報量、タグの自然さ、読者が読み進めやすい構成を毎回少しだけ良くする方が、長く続ける運用には合っています。"
        ),
        (
            f"もう一つの断片は、「{tertiary}」です。"
            "ここは地味ですが重要で、同じ言い回しや同じタグばかりになると、記事が更新されていても中身が動いていないように見えます。"
            "だから、今日のようにコンテキストから拾った発見を本文に反映して、毎回少し違う視点を入れることが必要になります。"
        ),
        (
            "全体としての発見は、MuscleLoveの発信は筋トレやAI活用の話題そのものだけではなく、"
            "その裏側にある試行錯誤、判断、改善のログを見せた方が読み物として成立しやすいということです。"
            "短いメモだけだと「何を見ればいいのか」が伝わりませんが、背景と次のアクションまで書くと、読者も自分の作業や発信に置き換えやすくなります。"
        ),
        (
            "次に見るポイントは、この記事を読んだあとに何が残るかです。"
            "今日なら、量を増やすだけではなく判断の理由を混ぜること、欠損を定期的に直すこと、同じ型を使い回しすぎないこと。"
            "この3つを小さな改善テーマとして残しておけば、次回の投稿でもコンテキストから新しい発見を拾いやすくなります。"
        ),
    ]
    return paragraphs


def build_context_block(snippets, keywords):
    """本文に入れる公開向けコンテキストHTMLを作成"""
    if not snippets:
        fallback_paragraphs = build_context_paragraphs([], keywords)
        return (
            "<div style='text-align:left;max-width:760px;margin:24px auto 0;'>"
            "<h3>コンテキストからの発見</h3>"
            + "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in fallback_paragraphs)
            + "</div>"
        )

    keyword_items = "".join(
        [f"<li>{html.escape(kw)}</li>" for kw in keywords[:5]]
    ) or "<li>日常</li><li>趣味</li><li>雑記</li>"
    excerpt_items = []
    excerpts = []
    for idx, snippet in enumerate(snippets[:3], start=1):
        excerpt = select_public_excerpt(snippet["text"])
        if not excerpt:
            continue
        excerpts.append(excerpt)
        label = html.escape(f"メモ{idx}: {snippet['path']}")
        excerpt_items.append(
            f"<li><strong>{label}</strong><br />{html.escape(excerpt)}</li>"
        )
    if not excerpt_items:
        excerpt_items.append("<li>今日は手元のメモから、公開できる話題だけを軽く整理しています。</li>")
    paragraphs = build_context_paragraphs(excerpts, keywords)

    return (
        "<div style='text-align:left;max-width:760px;margin:24px auto 0;'>"
        "<h3>コンテキストからの発見</h3>"
        "<p>手元のメモから拾った断片を、読み物として残るように少し長めに整理しています。</p>"
        + "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)
        + "<h4>拾った断片</h4>"
        + "<ul>" + "".join(excerpt_items) + "</ul>"
        "<p>今日の主なキーワード</p>"
        "<ul>" + keyword_items + "</ul>"
        "</div>"
    )


def write_dry_run_preview(title, content_html, tags, snippets):
    output_path = DRY_RUN_OUTPUT
    if not os.path.isabs(output_path):
        output_path = os.path.join(SCRIPT_DIR, output_path)
    preview = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: sans-serif; max-width: 860px; margin: 32px auto; line-height: 1.7; }}
    img {{ max-width: 100%; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p>tags: {html.escape(', '.join(tags))}</p>
  {content_html}
  <hr />
  <p>context snippets used: {len(snippets)}</p>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(preview)
    print(f"DRY_RUN preview wrote: {output_path}")


def ensure_generated_image(keywords):
    """入力画像がないときのフォールバック画像を生成"""
    if Image is None or ImageDraw is None:
        raise RuntimeError("Pillow is required to generate a fallback image when no local image exists.")

    os.makedirs("images", exist_ok=True)
    image_path = os.path.join("images", f"auto_context_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}.png")

    width, height = 1200, 630
    bg = (20, 24, 38)
    accent = (240, 84, 84)
    txt = (245, 245, 245)

    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)
    draw.rectangle([(0, 0), (width, 20)], fill=accent)
    draw.rectangle([(0, height - 20), (width, height)], fill=accent)

    title = "Daily Zakkiblog Notes"
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    kw_text = ", ".join(keywords[:6]) if keywords else "daily notes, hobbies, topics"
    lines = [
        title,
        "",
        f"generated: {ts}",
        f"keywords: {kw_text}",
        "",
        "auto-created by hatena uploader",
    ]
    y = 110
    for line in lines:
        draw.text((80, y), line, fill=txt)
        y += 70 if line else 35

    image.save(image_path, format="PNG")
    print(f"フォールバック画像を生成: {image_path}")
    return image_path


def load_uploaded():
    """アップロード済みファイルリストを読み込む"""
    if os.path.exists(UPLOADED_LOG):
        with open(UPLOADED_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_uploaded(uploaded):
    """アップロード済みファイルリストを保存する"""
    with open(UPLOADED_LOG, "w", encoding="utf-8") as f:
        json.dump(uploaded, f, ensure_ascii=False, indent=2)


def scan_local_image_assets():
    """Drive未設定時に使うリポジトリ内のローカル画像を探す"""
    image_files = []
    for path in LOCAL_IMAGE_FILES:
        if os.path.exists(path) and os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS:
            image_files.append(path)

    for output_dir in LOCAL_IMAGE_DIRS:
        if not os.path.exists(output_dir):
            continue
        for root, _, filenames in os.walk(output_dir):
            for fname in filenames:
                if fname.startswith("auto_context_"):
                    continue
                fpath = os.path.join(root, fname)
                ext = os.path.splitext(fname)[1].lower()
                if ext in IMAGE_EXTENSIONS:
                    image_files.append(fpath)

    print(f"ローカル画像ファイル数: {len(image_files)}")
    return image_files


def download_images_from_gdrive():
    """Google Driveフォルダから画像一覧を取得してダウンロード"""
    if not GDRIVE_FOLDER_ID:
        print("GDRIVE_FOLDER_ID_HATENA が未設定のため、ローカル画像を使います。")
        return scan_local_image_assets()
    if gdown is None:
        print("gdown is not installed; using local images instead.")
        return scan_local_image_assets()

    url = f"https://drive.google.com/drive/folders/{GDRIVE_FOLDER_ID}"
    output_dir = "images"
    os.makedirs(output_dir, exist_ok=True)

    print(f"Google Driveフォルダからダウンロード中: {GDRIVE_FOLDER_ID}")
    try:
        files = gdown.download_folder(
            url=url,
            output=output_dir,
            quiet=False,
        )
    except Exception as e:
        print(f"gdownダウンロードエラー: {e}")
        # フォルダ内に既にダウンロード済みファイルがあればそれを使う
        files = []

    # ダウンロードしたファイルからイメージファイルを取得
    image_files = []
    if files:
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                image_files.append(f)

    # gdownが空リストを返した場合、フォルダを再帰的にスキャン
    if not image_files and os.path.exists(output_dir):
        for root, dirs, filenames in os.walk(output_dir):
            for fname in filenames:
                fpath = os.path.join(root, fname)
                ext = os.path.splitext(fname)[1].lower()
                if ext in IMAGE_EXTENSIONS:
                    image_files.append(fpath)

    if not image_files:
        print("Drive画像が見つからないため、ローカル画像に切り替えます。")
        image_files = scan_local_image_assets()

    print(f"画像ファイル数: {len(image_files)}")
    return image_files


def get_content_tags(filename):
    """ファイル名からコンテンツに適したタグを推定"""
    fname_lower = filename.lower()
    fname_tokens = set(re.split(r"[^a-z0-9]+", fname_lower))
    tags = list(BASE_TAGS)
    for keyword, keyword_tags in CONTENT_TAG_MAP.items():
        if (len(keyword) <= 3 and keyword in fname_tokens) or (len(keyword) > 3 and keyword in fname_lower):
            for t in keyword_tags:
                if t not in tags:
                    tags.append(t)
            break
    return tags[:10]  # はてなブログのカテゴリは適度な数に


def upload_image_to_fotolife(image_path):
    """Hatena Fotolife APIに画像をアップロードし、画像URLを返す"""
    with open(image_path, 'rb') as f:
        image_data = f.read()

    encoded_image = base64.b64encode(image_data).decode()
    filename = os.path.basename(image_path)
    ext = os.path.splitext(filename)[1].lower()

    # Content-Typeを判定
    content_type_map = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
    }
    content_type = content_type_map.get(ext, 'image/jpeg')

    # Atom XMLでPOST
    xml_body = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://purl.org/atom/ns#">
  <title>{filename}</title>
  <content mode="base64" type="{content_type}">{encoded_image}</content>
</entry>"""

    headers = get_auth_headers()
    headers['Content-Type'] = 'application/x.atom+xml'

    fotolife_url = "https://f.hatena.ne.jp/atom/post"
    print(f"Hatena Fotolifeに画像アップロード中: {filename}")

    resp = requests.post(fotolife_url, headers=headers, data=xml_body.encode('utf-8'))

    if resp.status_code in (200, 201):
        print("Fotolife画像アップロード成功!")
        # レスポンスXMLから画像URLを抽出
        try:
            root = ET.fromstring(resp.text)
            # hatena:imageurl タグから取得
            ns = {'hatena': 'http://www.hatena.ne.jp/info/xmlns#'}
            img_url_elem = root.find('.//hatena:imageurl', ns)
            if img_url_elem is not None and img_url_elem.text:
                image_url = img_url_elem.text
                print(f"画像URL: {image_url}")
                return image_url

            # syntax-urlから取得を試みる
            syntax_elem = root.find('.//hatena:syntax', ns)
            if syntax_elem is not None and syntax_elem.text:
                # [f:id:username:YYYYMMDD:image:plain] 形式から抽出
                syntax = syntax_elem.text
                print(f"Fotolife syntax: {syntax}")
                # はてな記法をそのまま使える
                return syntax

            # fallback: レスポンス全体から画像URLを探す
            url_match = re.search(r'https?://cdn-ak\.f\.st-hatena\.com/images/[^\s<"]+', resp.text)
            if url_match:
                return url_match.group(0)

            print(f"Warning: 画像URLが取得できませんでした。レスポンス: {resp.text[:500]}")
            return None
        except ET.ParseError as e:
            print(f"XMLパースエラー: {e}")
            print(f"レスポンス: {resp.text[:500]}")
            return None
    else:
        print(f"Fotolifeアップロード失敗: {resp.status_code}")
        print(f"レスポンス: {resp.text[:500]}")
        return None


def get_blog_domain():
    """ブログ一覧APIから正しいブログドメインを自動取得"""
    blog_list_url = f"https://blog.hatena.ne.jp/{HATENA_ID}/atom"
    print(f"ブログ一覧APIからドメイン取得中...")
    resp = requests.get(blog_list_url, auth=(HATENA_ID, HATENA_API_KEY))
    if resp.status_code != 200:
        print(f"ブログ一覧API失敗: {resp.status_code}")
        return HATENA_BLOG_DOMAIN

    # collectionのhrefからドメインを抽出
    match = re.search(r'collection href="https://blog\.hatena\.ne\.jp/[^/]+/([^/]+)/atom/entry"', resp.text)
    if match:
        domain = match.group(1)
        print(f"正しいブログドメイン取得: {domain}")
        if domain != HATENA_BLOG_DOMAIN:
            print(f"WARNING: 環境変数のドメインと不一致! 環境変数={HATENA_BLOG_DOMAIN}, 実際={domain}")
        return domain

    print("ドメイン抽出失敗、環境変数のドメインを使用")
    return HATENA_BLOG_DOMAIN


def create_blog_post(title, content_html, categories):
    """Hatena Blog AtomPub APIでブログ記事を作成"""
    # ブログ一覧APIから正しいドメインを取得
    blog_domain = get_blog_domain()
    endpoint = f"https://blog.hatena.ne.jp/{HATENA_ID}/{blog_domain}/atom/entry"

    # カテゴリXMLを生成
    category_xml = ""
    for cat in categories:
        category_xml += f'  <category term="{cat}" />\n'

    xml_body = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:app="http://www.w3.org/2007/app">
  <title>{title}</title>
  <content type="html"><![CDATA[{content_html}]]></content>
{category_xml}  <app:control>
    <app:draft>no</app:draft>
  </app:control>
</entry>"""

    headers = get_auth_headers()
    headers['Content-Type'] = 'application/x.atom+xml'

    print(f"はてなブログに記事投稿中: {title}")
    print(f"エンドポイント: {endpoint}")

    # WSSE認証で投稿
    resp = requests.post(endpoint, headers=headers, data=xml_body.encode('utf-8'))

    if resp.status_code not in (200, 201):
        print(f"WSSE認証で失敗: {resp.status_code}")
        # Basic認証でフォールバック
        print("Basic認証でリトライ...")
        headers_basic = {'Content-Type': 'application/x.atom+xml'}
        resp = requests.post(endpoint, headers=headers_basic, data=xml_body.encode('utf-8'),
                             auth=(HATENA_ID, HATENA_API_KEY))

    if resp.status_code in (200, 201):
        print("ブログ記事投稿成功!")
        try:
            root = ET.fromstring(resp.text)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for link in root.findall('.//atom:link', ns):
                if link.get('rel') == 'alternate':
                    post_url = link.get('href')
                    print(f"投稿URL: {post_url}")
                    return post_url
        except Exception:
            pass
        return True
    else:
        print(f"ブログ投稿失敗: {resp.status_code}")
        print(f"レスポンス: {resp.text[:500]}")
        return None


def parse_manual_article(raw):
    """Markdown/HTML fileからtitle/categories/bodyを取り出す"""
    title = "MuscleLove テスト投稿"
    categories = ["MuscleLove", "ブログ運営", "事業メモ"]
    body = raw.strip()

    if body.startswith("---"):
        parts = body.split("---", 2)
        if len(parts) == 3:
            frontmatter = parts[1]
            body = parts[2].strip()
            parsed_categories = []
            current_key = None
            for line in frontmatter.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("title:"):
                    title = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                    current_key = "title"
                elif stripped.startswith("categories:"):
                    current_key = "categories"
                elif current_key == "categories" and stripped.startswith("-"):
                    cat = stripped[1:].strip().strip('"').strip("'")
                    if cat:
                        parsed_categories.append(cat)
                else:
                    current_key = None
            if parsed_categories:
                categories = parsed_categories

    return title, categories, body


def markdown_to_html(markdown_text):
    """最小限のMarkdownをはてな投稿用HTMLへ変換する"""
    html_lines = []
    paragraph = []
    list_items = []

    def flush_paragraph():
        nonlocal paragraph
        if paragraph:
            text = "<br />".join(html.escape(line) for line in paragraph)
            html_lines.append(f"<p>{text}</p>")
            paragraph = []

    def flush_list():
        nonlocal list_items
        if list_items:
            html_lines.append("<ul>")
            for item in list_items:
                html_lines.append(f"<li>{html.escape(item)}</li>")
            html_lines.append("</ul>")
            list_items = []

    for raw_line in markdown_text.splitlines():
        stripped = raw_line.strip()

        if not stripped:
            flush_paragraph()
            flush_list()
            continue

        if stripped.startswith("## "):
            flush_paragraph()
            flush_list()
            html_lines.append(f"<h2>{html.escape(stripped[3:].strip())}</h2>")
        elif stripped.startswith("### "):
            flush_paragraph()
            flush_list()
            html_lines.append(f"<h3>{html.escape(stripped[4:].strip())}</h3>")
        elif stripped.startswith("- "):
            flush_paragraph()
            list_items.append(stripped[2:].strip())
        else:
            flush_list()
            paragraph.append(stripped)

    flush_paragraph()
    flush_list()
    return "\n".join(html_lines)


def publish_manual_article(manual_path):
    """指定記事をMuscleLove画像つきで公開投稿する"""
    if not os.path.exists(manual_path):
        print(f"Error: MANUAL_ARTICLE_PATHが見つかりません: {manual_path}")
        return False

    with open(manual_path, "r", encoding="utf-8") as f:
        raw = f.read()

    title, categories, body_markdown = parse_manual_article(raw)
    local_images = scan_local_image_assets()
    chosen_image = local_images[0] if local_images else ensure_generated_image(["MuscleLove", "ブログ運営"])
    image_url = upload_image_to_fotolife(chosen_image)
    if not image_url:
        print("Error: 手動記事用画像のアップロードに失敗しました。")
        return False

    if image_url.startswith("[f:"):
        image_html = f'<p style="text-align:center;">{image_url}</p>'
    else:
        image_html = (
            '<p style="text-align:center;">'
            f'<img src="{html.escape(image_url)}" alt="{html.escape(title)}" style="max-width:100%;" />'
            "</p>"
        )

    content_html = image_html + "\n" + markdown_to_html(body_markdown) + build_profile_link_block()
    return create_blog_post(title, content_html, categories)


def main():
    print("=" * 60)
    print("Hatena Blog 自動投稿スクリプト")
    print(f"実行時刻: {datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}")
    print("=" * 60)

    if MANUAL_ARTICLE_PATH:
        print(f"手動記事モード: {MANUAL_ARTICLE_PATH}")
        if not publish_manual_article(MANUAL_ARTICLE_PATH):
            print("Error: 手動記事の投稿に失敗しました。")
            sys.exit(1)
        print("=" * 60)
        print("手動記事の投稿完了！")
        print("=" * 60)
        return

    # 環境変数チェック
    if not HATENA_ID or not HATENA_API_KEY or not HATENA_BLOG_DOMAIN:
        if not DRY_RUN:
            print("Error: HATENA_ID, HATENA_API_KEY, HATENA_BLOG_DOMAIN を設定してください。")
            sys.exit(1)
        print("DRY_RUN: Hatena credentials are missing, so no upload/post will be attempted.")

    # 先にコンテキストを集める（画像フォールバックにも利用）
    snippets = collect_context_snippets()
    context_text = " ".join([s["text"] for s in snippets])
    context_keywords = extract_context_keywords(context_text)

    # 画像ダウンロード
    image_files = scan_local_image_assets() if DRY_RUN else download_images_from_gdrive()

    # 無難画像(SFW)だけを投稿対象にする。SFW供給パイプラインが入れる画像は
    # ファイル名が "sfw_" で始まる（run_sfw_hatena.py の dest_name 規則）。
    # 供給元フォルダに旧来の筋肉/ビキニ画像(image_001_* 等)が残っていても、
    # ここで除外されるため投稿されない。該当が無ければ無難カードにフォールバック。
    sfw_files = [f for f in image_files if os.path.basename(f).lower().startswith("sfw_")]
    if sfw_files:
        image_files = sfw_files
    else:
        print("SFW画像(sfw_*)が見つからないため、無難カードにフォールバックします。")
        image_files = []

    if not image_files:
        print("入力画像が見つからないため、フォールバック画像を自動生成します。")
        image_files = [ensure_generated_image(context_keywords)]

    # アップロード済みリスト読み込み
    uploaded = load_uploaded()
    uploaded_names = set(uploaded)

    # 未投稿の画像をフィルタ
    unposted = [f for f in image_files if os.path.basename(f) not in uploaded_names]
    if not unposted:
        if DRY_RUN:
            print("全画像が投稿済みです。DRY_RUNのためログはリセットせずプレビューします。")
        else:
            print("全画像が投稿済みです。ログをリセットします。")
            uploaded = []
            save_uploaded(uploaded)
        unposted = image_files

    # ランダムに1枚選択
    chosen = random.choice(unposted)
    chosen_name = os.path.basename(chosen)
    print(f"選択された画像: {chosen_name}")

    # 1. Hatena Fotolifeに画像アップロード
    if DRY_RUN:
        image_url = chosen
    else:
        image_url = upload_image_to_fotolife(chosen)
        if not image_url:
            print("Error: 画像アップロードに失敗しました。")
            sys.exit(1)

    # 2. 日常記事エンジンで「普通の雑記ブログ」記事を生成（毎回テーマ・文章が変わる）
    article = everyday_content.build_article()
    title = article["title"]
    # テーマのカテゴリを先頭に、残りをタグとして付与（重複は除去）
    tags = [article["category"]] + [t for t in article["tags"] if t != article["category"]]

    # 画像は本文の先頭に中央寄せで置く（はてな記法 [f:...] とURLの両対応）
    if image_url.startswith('[f:'):
        image_html = f'<p style="text-align:center;">{image_url}</p>'
    else:
        image_html = (
            '<p style="text-align:center;">'
            f'<img src="{html.escape(image_url)}" alt="{html.escape(title)}" style="max-width:100%;" />'
            "</p>"
        )

    # 画像 + 記事本文 + （任意）プロフィールリンク。以前の定型ハッシュタグ羅列や
    # 「コンテキストからの発見」メタブロックは、いかにも自動投稿に見えるため廃止。
    content_html = (
        image_html
        + "\n"
        + article["body_html"]
        + build_profile_link_block()
    )

    if DRY_RUN:
        write_dry_run_preview(title, content_html, tags, snippets)
        print("DRY_RUN complete: skipped Hatena Fotolife upload, blog post, and uploaded log update.")
        return

    # 3. ブログ記事投稿
    result = create_blog_post(title, content_html, tags)
    if not result:
        print("Error: ブログ記事の投稿に失敗しました。")
        sys.exit(1)

    # 4. アップロード済みに追加
    uploaded.append(chosen_name)
    save_uploaded(uploaded)
    print(f"uploaded_hatena.json更新: {len(uploaded)}件")

    print("=" * 60)
    print("投稿完了！")
    print("=" * 60)


if __name__ == "__main__":
    main()
