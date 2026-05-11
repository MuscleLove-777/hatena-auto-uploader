# Hatena Blog Auto Uploader

Google Drive画像を使って、はてなブログへ自動投稿するスクリプトです。  
現在は「コンテキスト倉庫」から複数ファイルを読み込み、記事本文に文脈メモを自動で差し込めます。  
さらに、入力画像が無い日でもフォールバック画像を自動生成して投稿を止めません。

## 必須環境変数

- `HATENA_ID`
- `HATENA_API_KEY`
- `HATENA_BLOG_DOMAIN`
- `GDRIVE_FOLDER_ID_HATENA`

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

1. Google Driveから未投稿画像を1枚取得（無ければ自動生成画像）
2. Hatena Fotolifeへアップロード
3. コンテキスト元ファイル（`.md/.txt/.json`）を収集
4. キーワードを抽出して本文/タグへ反映
5. はてなブログに画像付きで公開投稿
