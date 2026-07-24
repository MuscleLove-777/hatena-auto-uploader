# -*- coding: utf-8 -*-
"""投稿直前の最終安全チェック。

この自動投稿は「普通の個人ブログの日常雑記」以外を絶対に外へ出さない。
本文エンジン(everyday_content)は固定コーパスなので本来安全だが、
将来の改修・手動記事・画像ファイル名経由で危険な語が混ざる事故を止めるため、
投稿する直前のタイトル／タグ／本文HTMLをここで必ず通す。

分類:
  adult    : 性的・成人向けを想起させる語
  brand    : 活動名・サービス名などの固有名詞（発信物は固有名詞ゼロが原則）
  internal : 内部作業やコンテキスト由来の「発見」を外に出す語
             （自動生成であること自体を露出させる語も含む）

違反が1件でもあれば SafetyViolation を投げ、呼び出し側は投稿せずに終了する。
検出語そのものはログにも伏せる（画面に出さない）。
"""

import re
import unicodedata


class SafetyViolation(Exception):
    """公開してはいけない語が含まれていた"""


# --- 禁止語（すべて小文字・正規化後の部分一致で判定） ---
# 2026-07-23 ポリシー変更: 「微エロまで許容」へ緩和。
#   許容(リストから外した): ビキニ/水着/セクシー/美女/筋肉美女/むちむち
#   → マイクロビキニ・グラビア調の画像とその説明文までは投稿OK
#   引き続き禁止: 露骨な性表現・成人向けを直接示す語・部位強調語
_NG_TERMS = {
    "adult": [
        "エロ", "えろ", "ero", "アダルト", "adult", "18禁", "r18", "r-18",
        "成人向け", "官能", "порн", "porn", "xxx", "nsfw",
        "ヌード", "nude", "下着", "ランジェリー", "lingerie",
        "巨乳", "美乳", "おっぱい", "谷間", "actress", "av女優",
        "同人", "fanza", "dlsite", "dmm", "アフィリ", "affiliate",
        "えち", "抜ける", "抜けた",
    ],
    "brand": [
        "musclelove", "muscle love", "マッスルラブ", "musclegirllove",
        "patreon", "パトレオン", "eronavi", "musclelove-777",
    ],
    "internal": [
        "コンテキスト", "context", "発見", "気づきメモ", "拾い読み",
        "自動投稿", "自動生成", "auto post", "auto-post", "bot投稿",
        "ga4", "indexnow", "アフィリエイト", "収益", "売上", "kpi",
        "github", "actions", "api key", "apikey", "token", "secret",
        "プロンプト", "llm", "生成ai", "chatgpt", "claude", "codex",
    ],
}

# 誤検知させたくない一般語（禁止語の部分文字列になり得るもの）を先に伏せる
_SAFE_EXCEPTIONS = [
    "水着のような",  # 例外が必要になったらここに足す
]


def _normalize(text):
    """全角/半角・大文字小文字・記号ゆれを吸収して判定用文字列にする"""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKC", str(text)).lower()
    # HTMLタグは本文の意味ではないので落とす。
    # 画像URL・ファイル名は呼び出し側から別の引数として渡してチェックすること。
    norm = re.sub(r"<[^>]+>", " ", norm)
    for exc in _SAFE_EXCEPTIONS:
        norm = norm.replace(exc, " ")
    return norm


def find_violations(*parts):
    """禁止語に触れた (カテゴリ, 語) の一覧を返す。空なら安全。"""
    haystack = " ".join(_normalize(p) for p in _flatten(parts))
    hits = []
    for category, terms in _NG_TERMS.items():
        for term in terms:
            if term in haystack:
                hits.append((category, term))
    return hits


def _flatten(parts):
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (list, tuple, set)):
            for sub in _flatten(part):
                yield sub
        else:
            yield part


def _mask(term):
    """検出語を画面に出さないための伏せ字表現"""
    return term[0] + "*" * max(len(term) - 1, 1)


def assert_safe(*parts):
    """違反があれば SafetyViolation。呼び出し側はこれを握りつぶさないこと。"""
    hits = find_violations(*parts)
    if not hits:
        return
    summary = ", ".join(f"{cat}:{_mask(term)}" for cat, term in hits)
    raise SafetyViolation(f"公開不可の語を検出したため投稿を中止しました ({summary})")


def assert_safe_filename(name):
    """画像ファイル名専用のチェック（brand / internal のみ）。

    本文と同じ基準を当てると、微エロ許容ポリシー(2026-07-23)で投稿OKにしたはずの
    素材名（水着まわりの語を含むもの等）で投稿ごと落ちる。露骨な素材の除外は
    upload.py の is_mild_ok_image が担当しているので、ここでは
    「外に出ると困る固有名詞・内部用語」だけを見る。
    """
    hits = [h for h in find_violations(name) if h[0] in ("brand", "internal")]
    if not hits:
        return
    summary = ", ".join(f"{cat}:{_mask(term)}" for cat, term in hits)
    raise SafetyViolation(f"画像ファイル名に公開不可の語があるため投稿を中止しました ({summary})")


if __name__ == "__main__":
    # 本文コーパス全体が禁止語に触れていないかを自己点検する
    import everyday_content

    problems = []
    for theme in everyday_content.THEMES:
        pool = (
            theme["titles"] + theme["intros"] + theme["bodies"] + theme["closes"]
            + theme["tags"] + [theme["category"]]
        )
        hits = find_violations(pool)
        if hits:
            problems.append((theme["key"], hits))

    if problems:
        for key, hits in problems:
            print(f"NG theme: {key} -> {[(c, _mask(t)) for c, t in hits]}")
        raise SystemExit(1)
    print(f"OK: {len(everyday_content.THEMES)}テーマすべて安全語のみ")
