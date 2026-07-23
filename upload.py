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
from datetime import datetime, timezone, timedelta

import re
import xml.etree.ElementTree as ET

import requests
import everyday_content
import safety_guard
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
# 過去のブランド画像(og.png)はフォールバックから外す。
# Drive画像が無い日は ensure_generated_image() の無難なカードのみを使う。
LOCAL_IMAGE_FILES = []
LOCAL_IMAGE_DIRS = ["images"]
# 自動生成するフォールバックカードの接頭辞。
# ファイル名も投稿前チェックの対象なので、内部用語を含まない中立な名前にする
# （旧 "auto_context_" は名前自体が内部の仕組みを示すため廃止）。
GENERATED_CARD_PREFIX = "card_"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def env_or_default(key, default):
    """空文字を未設定扱いとしてデフォルトを返す"""
    value = os.environ.get(key)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


DRY_RUN = env_or_default("DRY_RUN", "0").lower() in {"1", "true", "yes", "on"}
DRY_RUN_OUTPUT = env_or_default("DRY_RUN_OUTPUT", "dry_run_hatena_article.html")

# --- 画像ポリシー（2026-07-23変更: 微エロまで許容） ---
# 旧: sfw_ 接頭辞の無難画像のみ → 新: 露骨タグを含むファイルだけ除外。
# マイクロビキニ/水着/筋肉グラビア調はOK。GIF(動画枠)も対象。
# ファイル名で判定できない無名ファイル(image_001_* 等)は通す。
NSFW_BLOCK_TERMS = [
    'nsfw', 'r18', 'r-18', 'xxx', 'hentai',
    'sex', 'fuck', 'fella', 'blowjob', 'handjob', 'paizuri', 'titjob',
    'cum', 'bukkake', 'creampie', 'orgasm', 'ahegao',
    'penis', 'peniss', 'cock', 'dick', 'pussy', 'vagina', 'genital',
    'nipple', 'areola', 'topless', 'nude', 'naked', 'no bra', 'no panties',
    'insertion', 'dildo', 'vibrator', 'bondage', 'shibari', 'gangbang',
    'spread legs', 'spread pussy', 'masturbat', 'squirt', 'mosaic',
    'licking armpit', 'armpit lick', 'armpit hold', 'armpit fucking',
]


def is_mild_ok_image(path_or_name):
    """露骨タグを含むファイルだけ弾く（含まなければ安全側で通す）"""
    s = str(path_or_name).lower().replace('_', ' ').replace('-', ' ')
    return not any(term in s for term in NSFW_BLOCK_TERMS)


# 画像の直下に添える一言（日常記事と画像の橋渡し。安全語のみ）
IMAGE_CAPTION_LINES = [
    "本題の前に、今日のお気に入りの一枚から。",
    "最近いいなと思ったビジュアルを貼っておきます。目の保養にどうぞ。",
    "今日の一枚。鍛え上げた美しさって、見ていて元気が出ます。",
    "まずは今日のベストショットから。それでは本題へ。",
]

# 作業メモ等の外部ディレクトリを読み込む仕組みは廃止した。
# 記事本文・タグ・画像は everyday_content の固定コーパスと
# SFW画像だけで完結させ、手元の資料が本文へ流れ込む経路自体を持たない。


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



def write_dry_run_preview(title, content_html, tags):
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
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(preview)
    print(f"DRY_RUN preview wrote: {output_path}")


def ensure_generated_image():
    """入力画像がないときのフォールバック画像を生成。

    以前は手元のキーワードや生成時刻をカードへ描き込んでいたが、
    それ自体が作業内容の露出になり、自動生成であることも一目で分かってしまう。
    現在は文字を一切載せず、日替わりの配色だけを変える抽象的な図形カードにしている。
    """
    if Image is None or ImageDraw is None:
        raise RuntimeError("Pillow is required to generate a fallback image when no local image exists.")

    os.makedirs("images", exist_ok=True)
    image_path = os.path.join("images", f"{GENERATED_CARD_PREFIX}{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}.png")

    width, height = 1200, 630
    # 落ち着いた無地の配色パターン。日付でゆるく回して毎回同じ絵にならないようにする。
    palettes = [
        ((238, 236, 230), (206, 200, 188), (176, 168, 152)),
        ((232, 238, 240), (198, 214, 220), (162, 186, 196)),
        ((240, 234, 236), (216, 200, 206), (186, 164, 174)),
        ((234, 238, 232), (202, 216, 198), (168, 188, 164)),
        ((240, 238, 232), (214, 206, 190), (182, 172, 152)),
    ]
    bg, mid, deep = palettes[datetime.now(JST).timetuple().tm_yday % len(palettes)]

    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)
    # ゆるやかな帯と円だけの抽象パターン（文字・ロゴ・記号は一切入れない）
    draw.ellipse([(width - 420, -180), (width + 120, 360)], fill=mid)
    draw.ellipse([(-160, height - 300), (320, height + 180)], fill=mid)
    draw.rectangle([(0, height - 96), (width, height - 88)], fill=deep)
    draw.rectangle([(96, 88), (104, height - 160)], fill=deep)

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
                if fname.startswith(GENERATED_CARD_PREFIX) or fname.startswith("auto_context_"):
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
    # 公開へ出る唯一の口なので、ここでも必ず安全チェックを通す（多重防御）
    safety_guard.assert_safe(title, categories, content_html)

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
    """指定記事を画像つきで公開投稿する（手動記事も同じ安全チェックを通す）"""
    if not os.path.exists(manual_path):
        print(f"Error: MANUAL_ARTICLE_PATHが見つかりません: {manual_path}")
        return False

    with open(manual_path, "r", encoding="utf-8") as f:
        raw = f.read()

    title, categories, body_markdown = parse_manual_article(raw)
    safety_guard.assert_safe(title, categories, body_markdown)
    local_images = scan_local_image_assets()
    chosen_image = local_images[0] if local_images else ensure_generated_image()
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

    # 画像ダウンロード
    image_files = scan_local_image_assets() if DRY_RUN else download_images_from_gdrive()

    # 微エロOKポリシー(2026-07-23): sfw_接頭辞限定を廃止し、
    # 露骨タグを含むファイルだけを除外する。マイクロビキニ/水着/筋肉グラビア調、
    # 旧来の筋肉画像(image_001_* 等)、GIF(動画枠)はすべて投稿対象。
    mild_ok = [f for f in image_files if is_mild_ok_image(f)]
    blocked = len(image_files) - len(mild_ok)
    if blocked:
        print(f"露骨タグのため除外した画像: {blocked}件")
    if mild_ok:
        image_files = mild_ok
    else:
        print("投稿可能な画像が見つからないため、無難カードにフォールバックします。")
        image_files = []

    if not image_files:
        print("入力画像が見つからないため、フォールバック画像を自動生成します。")
        image_files = [ensure_generated_image()]

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

    # 画像直下の一言キャプション（日常記事と画像の橋渡し）
    caption_html = (
        '<p style="text-align:center;color:#888;font-size:0.9em;">'
        f'{random.choice(IMAGE_CAPTION_LINES)}</p>'
    )

    # 画像 + キャプション + 記事本文 + （任意）プロフィールリンク。
    # 以前の定型ハッシュタグ羅列や、手元のメモから拾った内容を要約して
    # 載せるブロックは廃止済み。
    content_html = (
        image_html
        + "\n"
        + caption_html
        + "\n"
        + article["body_html"]
        + build_profile_link_block()
    )

    # 投稿直前の最終チェック。タイトル・タグ・本文に加えて画像ファイル名も見る。
    # 1件でも引っかかったら投稿せずに異常終了する（握りつぶさない）。
    safety_guard.assert_safe(title, tags, article["body_html"], chosen_name)

    if DRY_RUN:
        write_dry_run_preview(title, content_html, tags)
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
