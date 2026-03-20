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
from datetime import datetime, timezone, timedelta

import requests
import gdown

JST = timezone(timedelta(hours=9))

# --- 環境変数 ---
HATENA_ID = os.environ.get("HATENA_ID", "")
HATENA_API_KEY = os.environ.get("HATENA_API_KEY", "")
HATENA_BLOG_DOMAIN = os.environ.get("HATENA_BLOG_DOMAIN", "")
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID_HATENA", "")

PATREON_LINK = "https://www.patreon.com/cw/MuscleLove"
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
UPLOADED_LOG = "uploaded_hatena.json"

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
    'training': ['筋トレ', 'ワークアウト', 'トレーニング', 'ジム', 'フィットネス'],
    'workout': ['筋トレ', 'ワークアウト', 'トレーニング', 'ジム', 'フィットネス'],
    'pullups': ['懸垂', 'プルアップ', '背中トレ', '自重トレーニング'],
    'posing': ['ポージング', 'ボディビル', 'フィジーク'],
    'flex': ['フレックス', '筋肉', 'ボディビル'],
    'muscle': ['筋肉', 'マッスル', 'フィットネス'],
    'bicep': ['上腕二頭筋', '腕トレ', 'バイセップス'],
    'abs': ['腹筋', 'シックスパック', '体幹'],
    'leg': ['脚トレ', 'レッグデイ', 'スクワット'],
    'back': ['背中', '広背筋', '背中トレ'],
    'squat': ['スクワット', '脚トレ', 'レッグデイ'],
    'deadlift': ['デッドリフト', 'パワーリフティング'],
    'bench': ['ベンチプレス', '胸トレ'],
    'bikini': ['ビキニ', 'ビキニフィットネス', 'フィギュア'],
    'competition': ['大会', 'コンテスト', 'ボディビル'],
}

BASE_TAGS = [
    '筋肉女子', '筋トレ女子', 'フィットネス', 'マッスルガール',
    'ボディビル', 'ジム', 'ワークアウト', 'MuscleLove',
    'ワキフェチ', '腕フェチ', '筋肉美', 'AI美女', 'むちむち', '褐色美女',
]

# --- タイトルテンプレート ---
TITLE_TEMPLATES = [
    "{category} | MuscleLove",
    "{category} - 筋肉美の世界💪",
    "{category} | マッスルラブ🔥",
    "{category} - 美しき筋肉✨",
    "MuscleLove | {category}",
    "{category} - この身体、気になるでしょ？♡",
    "{category} - 特別に見せてあげる♡",
    "{category} | 今日もバキバキに仕上がったｗ💪",
    "{category} - 鍛え抜かれた美🔥",
    "{category} | 仕上がりえぐい✨",
    "{category} - 破壊力やばすぎ💪🔥",
    "{category} | むき出しの筋肉美♡",
    "{category} - じっとり汗ばむ筋肉✨",
    "{category} | 絞り上げたこの身体🔥",
    "{category} - 濡れツヤ筋肉の魅力♡",
    "{category} | 無防備なマッスルボディ💪",
    "{category} - 詰め込まれた筋肉美✨",
]

# --- カテゴリ名テンプレート ---
CATEGORY_TEMPLATES = [
    "筋肉美ギャラリー", "フィットネスの美学", "マッスルビューティー",
    "鍛え上げた肉体美", "筋トレ女子コレクション", "ボディビル女子の魅力",
    "筋肉女子フォトギャラリー", "美しき筋肉の世界", "フィットネスガール",
    "マッスルアート", "筋肉美の極致", "ストロングビューティー",
    "ワークアウトビューティー", "鍛え抜いた美ボディ", "パワフルビューティー",
    "むちむちマッスル", "褐色筋肉美女", "むき出しの肉体美",
    "汗ばむ筋肉女子", "絞り上げボディ", "濡れツヤマッスル",
    "無防備マッスルガール", "バキバキ女子", "褐色ボリュームボディ",
    "じっとり筋肉美", "詰め込みマッスル",
]

# --- 本文HTMLテンプレート ---
BODY_TEMPLATES = [
    """<div style="text-align:center;">
<p><img src="{image_url}" alt="{title}" style="max-width:100%;" /></p>
<p style="font-size:1.1em;"><strong>{title}</strong></p>
<p>{description}</p>
<p style="margin-top:20px;">{hashtags}</p>
<hr />
<p style="font-size:1.2em;">💪 <a href="{patreon_link}" target="_blank"><strong>もっと見たい？特別に見せてあげる♡ → MuscleLove</strong></a></p>
</div>""",
    """<div style="text-align:center;">
<p><img src="{image_url}" alt="{title}" style="max-width:100%;" /></p>
<p style="font-size:1.1em;"><strong>{title}</strong></p>
<p>{description}</p>
<p style="margin-top:20px;">{hashtags}</p>
<hr />
<p style="font-size:1.2em;">🔥 <a href="{patreon_link}" target="_blank"><strong>この身体、気になるでしょ？限定コンテンツはPatreonで♡</strong></a></p>
</div>""",
    """<div style="text-align:center;">
<p><img src="{image_url}" alt="{title}" style="max-width:100%;" /></p>
<p style="font-size:1.1em;"><strong>{title}</strong></p>
<p>{description}</p>
<p style="margin-top:20px;">{hashtags}</p>
<hr />
<p style="font-size:1.2em;">✨ <a href="{patreon_link}" target="_blank"><strong>ここでしか見れない筋肉美、Patreonで公開中💪🔥</strong></a></p>
<p><a href="{patreon_link}" target="_blank">{patreon_link}</a></p>
</div>""",
    """<div style="text-align:center;">
<p><img src="{image_url}" alt="{title}" style="max-width:100%;" /></p>
<p style="font-size:1.1em;"><strong>{title}</strong></p>
<p>{description}</p>
<p style="margin-top:20px;">{hashtags}</p>
<hr />
<p style="font-size:1.2em;">♡ <a href="{patreon_link}" target="_blank"><strong>むき出しの筋肉美、全部見せてあげる → Patreon</strong></a></p>
</div>""",
]

# --- 説明文テンプレート ---
DESCRIPTION_TEMPLATES = [
    "筋肉質の女がエロい。鍛え上がった身体、見せてあげる♡💪",
    "じっとり汗ばむ褐色の筋肉美。この破壊力、やばくない？🔥",
    "むき出しの筋肉、絞り上げたボディライン。目が離せないでしょ？✨",
    "むちむちボリューム×バキバキ筋肉。この組み合わせ、最強すぎる💪🔥",
    "濡れツヤの肌に浮かぶ筋肉の陰影。鍛え抜かれた身体は芸術♡",
    "無防備なマッスルボディ、特別に見せてあげる。Patreonで待ってるよ✨",
    "褐色×筋肉×ボリューム体型。神評価の仕上がり、見逃さないで💪",
    "詰め込まれた筋肉美。この身体に釘付けになるはず🔥♡",
    "仕上がりえぐい筋肉女子。鍛え上がった肉体美をどうぞ✨",
    "今日もバキバキに仕上がった筋肉美。この身体、気になるでしょ？💪",
    "絞り上げたボディから溢れるパワー。強く美しい筋肉の世界🔥",
    "じっとり光る筋肉、むき出しの美しさ。全部見せてあげる♡✨",
]


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
            remaining_ok=True,
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
    tags = list(BASE_TAGS)
    for keyword, keyword_tags in CONTENT_TAG_MAP.items():
        if keyword in fname_lower:
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
        import xml.etree.ElementTree as ET
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
            import re
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


def create_blog_post(title, content_html, categories):
    """Hatena Blog AtomPub APIでブログ記事を作成"""
    endpoint = f"https://blog.hatena.ne.jp/{HATENA_ID}/{HATENA_BLOG_DOMAIN}/atom/entry"

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
    print(f"HATENA_ID長さ: {len(HATENA_ID)}")
    print(f"HATENA_BLOG_DOMAIN長さ: {len(HATENA_BLOG_DOMAIN)}")
    print(f"ドメインにhatenablog含む: {'hatenablog' in HATENA_BLOG_DOMAIN}")
    print(f"ドメイン末尾: ...{HATENA_BLOG_DOMAIN[-15:]}")
    print(f"API_KEY長さ: {len(HATENA_API_KEY)}")

    # まずGETでエンドポイント確認
    print("GETでエンドポイント確認中...")
    get_resp = requests.get(endpoint, headers=get_auth_headers())
    print(f"GET結果: {get_resp.status_code}")
    if get_resp.status_code != 200:
        print(f"GETレスポンス: {get_resp.text[:300]}")
        # Basic認証でGET
        get_resp2 = requests.get(endpoint, auth=(HATENA_ID, HATENA_API_KEY))
        print(f"GET(Basic)結果: {get_resp2.status_code}")
        if get_resp2.status_code != 200:
            print(f"GET(Basic)レスポンス: {get_resp2.text[:300]}")

    # まずWSSE認証で試行
    resp = requests.post(endpoint, headers=headers, data=xml_body.encode('utf-8'))

    if resp.status_code in (200, 201):
        print("ブログ記事投稿成功! (WSSE認証)")
    else:
        print(f"WSSE認証で失敗: {resp.status_code} - {resp.text[:300]}")
        # Basic認証でフォールバック
        print("Basic認証でリトライ...")
        headers_basic = {'Content-Type': 'application/x.atom+xml'}
        resp = requests.post(endpoint, headers=headers_basic, data=xml_body.encode('utf-8'),
                             auth=(HATENA_ID, HATENA_API_KEY))
        if resp.status_code in (200, 201):
            print("ブログ記事投稿成功! (Basic認証)")
        else:
            print(f"Basic認証でも失敗: {resp.status_code} - {resp.text[:300]}")

    if resp.status_code in (200, 201):
        # レスポンスからURLを抽出
        import xml.etree.ElementTree as ET
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

    # 画像ダウンロード
    image_files = download_images_from_gdrive()
    if not image_files:
        print("Error: 画像ファイルが見つかりません。")
        sys.exit(1)

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

    # 2. ブログ記事を生成
    tags = get_content_tags(chosen_name)
    category = random.choice(CATEGORY_TEMPLATES)
    title = random.choice(TITLE_TEMPLATES).format(category=category)
    description = random.choice(DESCRIPTION_TEMPLATES)
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
            patreon_link=PATREON_LINK,
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
            patreon_link=PATREON_LINK,
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
