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
from collections import Counter
from datetime import datetime, timezone, timedelta

import re
import xml.etree.ElementTree as ET

import requests
import gdown
from PIL import Image, ImageDraw

JST = timezone(timedelta(hours=9))

# --- 環境変数 ---
HATENA_ID = os.environ.get("HATENA_ID", "")
HATENA_API_KEY = os.environ.get("HATENA_API_KEY", "")
HATENA_BLOG_DOMAIN = os.environ.get("HATENA_BLOG_DOMAIN", "")
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID_HATENA", "")

PROFILE_LINK = os.environ.get("PROFILE_LINK", "").strip()
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
UPLOADED_LOG = "uploaded_hatena.json"
CONTEXT_FILE_EXTENSIONS = {".md", ".txt", ".json"}
def env_or_default(key, default):
    """空文字を未設定扱いとしてデフォルトを返す"""
    value = os.environ.get(key)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


CONTEXT_MAX_FILES = int(env_or_default("CONTEXT_MAX_FILES", "15"))
CONTEXT_MAX_CHARS = int(env_or_default("CONTEXT_MAX_CHARS", "900"))
CONTEXT_SOURCE_DIRS = [
    p.strip()
    for p in env_or_default(
        "CONTEXT_SOURCE_DIRS",
        "context,../../00_本部_オーケストレーター/80_コンテキスト倉庫,../../004_MuscleLove/dashboard/daily_ga4",
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


def collect_context_snippets():
    """設定ディレクトリからコンテキスト文字列を収集"""
    snippets = []
    for source_dir in CONTEXT_SOURCE_DIRS:
        if not os.path.isdir(source_dir):
            continue
        candidates = []
        for root, _, filenames in os.walk(source_dir):
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in CONTEXT_FILE_EXTENSIONS:
                    candidates.append(os.path.join(root, fname))
        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for path in candidates[:CONTEXT_MAX_FILES]:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = f.read().strip()
                if not raw:
                    continue
                clean = re.sub(r"\s+", " ", raw)
                snippets.append(
                    {
                        "path": os.path.basename(path),
                        "text": clean[:CONTEXT_MAX_CHARS],
                    }
                )
            except Exception as e:
                print(f"コンテキスト読込失敗: {path} ({e})")
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


def build_context_block(snippets, keywords):
    """本文に入れる公開向けコンテキストHTMLを作成"""
    if not snippets:
        return "<p>今日の文脈メモ: 日常、趣味、作業、気になる話題を少しずつ整理中です。</p>"
    keyword_items = "".join([f"<li>{kw}</li>" for kw in keywords[:5]]) or "<li>日常</li><li>趣味</li><li>雑記</li>"
    return (
        "<div style='text-align:left;max-width:760px;margin:0 auto;'>"
        "<h3>今日拾った文脈</h3>"
        "<p>手元のメモから、公開しても自然な関心テーマだけを拾っています。</p>"
        "<ul>" + keyword_items + "</ul>"
        "</div>"
    )


def ensure_generated_image(keywords):
    """入力画像がないときのフォールバック画像を生成"""
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


def download_images_from_gdrive():
    """Google Driveフォルダから画像一覧を取得してダウンロード"""
    if not GDRIVE_FOLDER_ID:
        print("Error: GDRIVE_FOLDER_ID_HATENA が設定されていません。")
        return []

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


def main():
    print("=" * 60)
    print("Hatena Blog 自動投稿スクリプト")
    print(f"実行時刻: {datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}")
    print("=" * 60)

    # 環境変数チェック
    if not HATENA_ID or not HATENA_API_KEY or not HATENA_BLOG_DOMAIN:
        print("Error: HATENA_ID, HATENA_API_KEY, HATENA_BLOG_DOMAIN を設定してください。")
        sys.exit(1)

    # 先にコンテキストを集める（画像フォールバックにも利用）
    snippets = collect_context_snippets()
    context_text = " ".join([s["text"] for s in snippets])
    context_keywords = extract_context_keywords(context_text)

    # 画像ダウンロード
    image_files = download_images_from_gdrive()
    if not image_files:
        print("入力画像が見つからないため、フォールバック画像を自動生成します。")
        image_files = [ensure_generated_image(context_keywords)]

    # アップロード済みリスト読み込み
    uploaded = load_uploaded()
    uploaded_names = set(uploaded)

    # 未投稿の画像をフィルタ
    unposted = [f for f in image_files if os.path.basename(f) not in uploaded_names]
    if not unposted:
        print("全画像が投稿済みです。ログをリセットします。")
        uploaded = []
        save_uploaded(uploaded)
        unposted = image_files

    # ランダムに1枚選択
    chosen = random.choice(unposted)
    chosen_name = os.path.basename(chosen)
    print(f"選択された画像: {chosen_name}")

    # 1. Hatena Fotolifeに画像アップロード
    image_url = upload_image_to_fotolife(chosen)
    if not image_url:
        print("Error: 画像アップロードに失敗しました。")
        sys.exit(1)

    # 2. コンテキスト反映 + ブログ記事生成
    tags = get_content_tags(chosen_name)
    for kw in context_keywords[:3]:
        if kw not in tags:
            tags.append(kw)
    category = random.choice(CATEGORY_TEMPLATES)
    title = random.choice(TITLE_TEMPLATES).format(category=category)
    description = random.choice(DESCRIPTION_TEMPLATES)
    if context_keywords:
        description += f" 今日の注目テーマ: {', '.join(context_keywords[:3])}。"
    hashtags = " ".join([f"#{t}" for t in tags[:8]])

    # 画像URLの処理（はてな記法の場合はHTMLに変換）
    if image_url.startswith('[f:'):
        # はてな記法の場合はそのままcontent内で使う
        img_html = image_url
        content_html = random.choice(BODY_TEMPLATES).format(
            image_url="",
            title=title,
            description=description,
            hashtags=hashtags,
        )
        # image_urlのプレースホルダーを置換
        content_html = content_html.replace(
            '<p><img src="" alt="{}" style="max-width:100%;" /></p>'.format(title),
            f'<p>{img_html}</p>'
        )
    else:
        content_html = random.choice(BODY_TEMPLATES).format(
            image_url=image_url,
            title=title,
            description=description,
            hashtags=hashtags,
        )

    # 個人文脈メモ + 任意プロフィールリンク
    context_block = build_context_block(snippets, context_keywords)
    content_html = (
        content_html.rstrip()
        + "\n"
        + context_block
        + build_profile_link_block()
    )

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
