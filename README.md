# Hatena Blog Auto Uploader

はてなブログへ毎日自動投稿するスクリプトです。
**2026-07-23 方針転換**（ユーザー決定）: 従来の「無味な汎用雑記＋誘導リンク無し」をやめ、
MuscleLoveのギャル系筋肉女子ボイス（Notion「コンテンツナレッジDB」由来の言葉遣い）の
日常つぶやき＋**軽セクシー画像**＋**Patreon/X 誘導カード**で投稿します。
記事本文は `everyday_content.py` のギャル系テーマ（トレ後／筋肉自慢／ジム／食事／照れデレ／オフ）
から毎回違う組み合わせで作ります。テーマ追加は同ファイルの `THEMES` に追記。人物実名・数字は書かない。

## 出さないもの（安全ガードで遮断）

- 露骨な性表現・成人向けを直接示す語（軽セクシー＝ワキ/汗/筋肉の匂わせまでは許容）。
- 内部インフラ語（GA4／GitHub／APIキー／トークン等）と、他ブランド名の誤露出。
- ※ 自分の公式導線（Patreon／X／MuscleLove）は **許容**（誘導カードで出す）。

これらは `safety_guard.py` が投稿直前に機械的に弾きます。
1語でも該当すれば投稿せず異常終了します（`create_blog_post()` にも同じチェックを二重に入れてある）。
語を足したいときは `safety_guard.py` の `_NG_TERMS` に追記してください。

自己点検（本文コーパス全体が禁止語に触れていないか）:

```
python safety_guard.py
```

GitHub Actions でも投稿前に同じチェックが走り、失敗した時点でジョブが止まります。

## 画像

- Drive から取得した画像のうち、ファイル名が `sfw_` で始まるものだけを投稿対象にします。
  供給元に古い画像が残っていても、この時点で除外されます。
- 該当が無い日は `card_YYYYmmdd_HHMMSS.png` を自動生成してフォールバックします。
  文字・ロゴを一切載せない抽象的な図形カードで、配色だけ日替わりです。
- 画像ファイル名も投稿前チェックの対象です。

## 必須環境変数

- `HATENA_ID`
- `HATENA_API_KEY`
- `HATENA_BLOG_DOMAIN`
- `GDRIVE_FOLDER_ID_HATENA`

## 任意環境変数

- `PROFILE_LINK`: 記事末尾にプロフィール/活動リンクを出したい場合だけ指定（未設定なら出ません）
- `MANUAL_ARTICLE_PATH`: 特定のMarkdown/HTML記事を投稿したい場合（手動記事も同じ安全チェックを通ります）
- `DRY_RUN=1`: 投稿せずプレビューHTMLだけ出力
- `DRY_RUN_OUTPUT`: プレビュー出力先（既定 `dry_run_hatena_article.html`）

## 仕組み

1. Drive から `sfw_` 画像を1枚取得（無ければ無地のカードを自動生成）
2. Hatena Fotolife へアップロード
3. `everyday_content.build_article()` が日常テーマの記事（タイトル・カテゴリ・タグ・本文）を生成
4. 画像を本文先頭に置き、記事本文を続ける（ハッシュタグ羅列やメタ文は付けない）
5. `safety_guard.assert_safe()` で最終チェック
6. はてなブログに画像付きで公開投稿

## 自動実行

- `.github/workflows/hatena-post.yml` — JST 14:30 / 00:30
- `.github/workflows/hatena-post-musclelove777.yml` — JST 12:10
- いずれも3回までリトライし、成否をLINEへ通知します。

## ローカル確認

```
$env:DRY_RUN='1'; python upload.py
```

出力は `dry_run_hatena_article.html`。
