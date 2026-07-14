# Hatena Blog Auto Uploader

Google Drive画像を使って、はてなブログへ自動投稿するスクリプトです。
投稿内容は外部販売や支援サイトへの誘導ページではなく、**普通の個人ブログ**らしい日常雑記として生成します（散歩・コーヒー・料理・天気・ゲーム・読書 等のテーマを、毎回違う自然な文章で）。
記事本文は `everyday_content.py` の記事エンジンが組み立てます。テーマ／言い回しを増やしたいときは同ファイルの `THEMES` に追記するだけです。
画像は Drive フォルダ（無難な画像を供給する用途）から取得します。Drive画像が無い日は、ブランド画像を使わず無難なテキストカードを自動生成して保険にします（旧 `og.png` はフォールバックから除外済み）。

## 必須環境変数

- `HATENA_ID`
- `HATENA_API_KEY`
- `HATENA_BLOG_DOMAIN`
- `GDRIVE_FOLDER_ID_HATENA`

## 任意環境変数

- `PROFILE_LINK`:
  - 個人プロフィールや活動リンクを記事末尾に出したい場合だけ指定
  - 未設定ならプロフィールリンク欄は出ません

## コンテキスト連携（任意）

- `CONTEXT_SOURCE_DIRS`:
  - カンマ区切りで、文脈取得対象ディレクトリを指定
  - 例: `context,../../00_本部_オーケストレーター/80_コンテキスト倉庫,../../004_MuscleLove/dashboard/daily_ga4`
- `CONTEXT_MAX_FILES`:
  - 各ディレクトリから読む最大ファイル数（デフォルト: `15`）
- `CONTEXT_MAX_CHARS`:
  - 1ファイルから読む最大文字数（デフォルト: `900`）
  - 未設定または空文字でもデフォルトが自動適用されます

## 仕組み

1. Google Driveから未投稿画像を1枚取得（無ければ無難なテキストカードを自動生成）
2. Hatena Fotolifeへアップロード
3. `everyday_content.build_article()` が日常テーマの記事（タイトル・カテゴリ・タグ・本文）を毎回ランダムに生成
4. 画像を本文先頭に置き、記事本文を続ける（ハッシュタグ羅列やメタ文は付けない）
5. `PROFILE_LINK` があればプロフィール/活動リンクだけを添える
6. はてなブログに画像付きで公開投稿

※ 旧仕様（`CONTEXT_SOURCE_DIRS` からキーワードを拾って本文へ反映）は本文生成には使わなくなりました。コンテキスト収集はフォールバックカードのキーワード程度にのみ残っています。

## Local preview / context source notes

- `context/public_context_latest.md` is the safe in-repo fallback context source.
- Local MuscleLove workspace context is also read when available from `../../../10_事業部/02_MuscleLove事業/...`.
- The generated context block expands the findings into roughly 1,000 Japanese characters, not just a short memo list.
- Preview without posting:
  - PowerShell: `$env:DRY_RUN='1'; python upload.py`
  - The preview file is `dry_run_hatena_article.html`.
