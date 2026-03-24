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

import re
import xml.etree.ElementTree as ET

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
    "{category} | 凛花が魅せる筋肉の芸術💪",
    "{category} - カイの背中、今日も鬼仕上がり🔥",
    "{category} | ましろの柔肌×鋼の筋肉♡",
    "MuscleLove | {category}が止まらない✨",
    "{category} - 紫苑の本気トレ、覗いちゃう？🔥",
    "{category} | 脱いだら凄かった…💪♡",
    "アヤネ流{category}、これが答え✨",
    "{category} - 1日3分で変わる？嘘でしょ…🔥",
    "{category} | もう戻れない筋肉沼💪",
    "{category} - 「え、触っていい？」って聞かれた話♡",
    "MuscleLove厳選 | {category}ハイライト🔥",
    "{category} - 凛花のワークアウト日記✨",
    "{category} | 褐色肌に映える汗の雫💪♡",
    "話題沸騰！{category}がSNSで大バズり🔥",
    "{category} - ましろ、限界突破しました✨",
    "{category} | ジムで二度見された筋肉美♡",
    "カイの{category}記録 | 進化が止まらない💪",
    "{category} - この腹筋、何パックか数えてみて🔥",
    "{category} | 紫苑の秘密のトレーニングルーム✨",
    "{category} - ボディラインが物語る努力の結晶♡💪",
]

# --- カテゴリ名テンプレート ---
CATEGORY_TEMPLATES = [
    "筋肉美ギャラリー", "フィットネスの美学", "マッスルビューティー",
    "鍛え上げた肉体美", "筋トレ女子コレクション", "ボディビル女子の魅力",
    "フィットネスガール", "マッスルアート", "ストロングビューティー",
    "ワークアウトビューティー", "パワフルビューティー", "褐色筋肉美女",
    "バキバキ女子", "褐色ボリュームボディ", "アイアンレディ",
    "筋肉の彫刻美", "汗と努力のカタチ", "ダイヤモンドボディ",
    "鋼鉄ガールズ", "マッスルクイーン", "フレックス女子",
    "背中で語る女", "パンプアップ美学", "ビースト系美女",
    "黄金比ボディ", "筋トレ女神", "限界突破ガール",
    "アスリートビューティー", "チカラの美", "トレーニーの誇り",
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
    "凛花が朝イチでパンプさせた腕、近くで見る？ぷりっぷりだよ💪♡",
    "カイのデッドリフト200kg達成記念。背中の厚み、ちょっとおかしいｗ🔥",
    "ましろの太もも、触ったら硬すぎて驚く人続出らしい。試してみる？✨",
    "汗が滴る褐色肌、浮き出る血管。これが毎日の努力の答え💪🔥",
    "紫苑が減量期ラスト1週間。絞り切ったウエスト、芸術品でしかない♡",
    "ジムのミラー越しに撮った1枚。ライティングが完璧すぎて震えた✨",
    "アヤネのポージング練習風景。さりげないフレックスが一番かっこいい💪",
    "休息日のリラックスショット。力抜いてても筋肉は嘘つかない🔥♡",
    "増量期のむちむちボディも、減量期のキレキレも、どっちも最高でしょ✨",
    "「筋トレ始めたきっかけは？」→この写真見せれば一発で伝わる💪🔥",
    "夜トレ後の1枚。薄暗いジムで浮かび上がる筋肉のシルエットがエモい♡",
    "三角筋のカット、広背筋の広がり。細部に宿る鍛錬の美しさ✨",
    "「強い女が好き」って人、ここに集合。期待を裏切らない仕上がり💪♡",
    "凛花とましろのツーショット。パワーの化学反応がすごすぎる🔥✨",
    "週6トレーニングの成果、写真で全部見せます。覚悟して💪🔥♡",
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
