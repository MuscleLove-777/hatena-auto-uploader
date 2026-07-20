# Hatena Blog Auto Uploader

はてなブログへ毎日自動投稿するスクリプトです。
投稿されるのは **普通の個人ブログの日常雑記** だけ（散歩・コーヒー・料理・天気・ゲーム・読書 等）。
記事本文は `everyday_content.py` の固定コーパスから毎回違う組み合わせで作ります。
テーマ／言い回しを増やしたいときは同ファイルの `THEMES` に追記するだけです。

## 出さないもの（設計上の前提）

- 手元の作業メモ・資料から拾った内容、およびそこから導いた気づきの類。
  外部ディレクトリを読み込む仕組みそのものを廃止済みで、本文へ流れ込む経路がありません。
- 活動名・サービス名などの固有名詞。
- 成人向けを想起させる語、およびそれを想起させる画像。
- 自動生成・自動投稿であること自体を示す文言（フッターの定型文やカードの文字も無し）。

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
