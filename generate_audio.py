#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_audio.py — Shadowing N4 の音声を Azure ニューラル TTS で生成するスクリプト

data.json の各文について、日本語（jp）と インドネシア語（idn）の MP3 を
それぞれ audio/ と audio_id/ に書き出します。

使い方（詳しくは manual-steps.md の手順3を参照）:

    # キーを環境変数に設定（PowerShell の例）
    #   $env:AZURE_SPEECH_KEY="（コピーしたキー）"
    #   $env:AZURE_SPEECH_REGION="japaneast"

    python generate_audio.py --dry-run   # 生成せず、対象件数と文字数を確認するだけ
    python generate_audio.py             # 未生成・変更された文だけを生成
    python generate_audio.py --force     # 全件を作り直す（声を変えたときなど）

.audio_manifest.json に「どの文をどの声で生成済みか」を記録しているので、
2 回目以降は変更・追加された文だけが生成されます。
"""

import argparse
import hashlib
import json
import os
import sys
import time
from xml.sax.saxutils import escape

try:
    import requests
except ImportError:
    print("エラー: requests ライブラリが必要です。次を実行してください:\n"
          "    pip install requests", file=sys.stderr)
    sys.exit(1)

# ----------------------------------------------------------------------------
# 声の設定 — ここを書き換えれば声や読む速さを変えられます。
# 変更後は --force を付けて再実行してください。
#
#   日本語（女性）: ja-JP-NanamiNeural  … 落ち着いた標準的な声（既定）
#                   ja-JP-MayuNeural    … やわらかい印象
#   日本語（男性）: ja-JP-KeitaNeural / ja-JP-DaichiNeural（はきはき）
#   インドネシア語（女性）: id-ID-GadisNeural（既定） /（男性）: id-ID-ArdiNeural
#
#   rate … 読む速さ。'-8%' が既定。もっとゆっくりなら '-15%'。
# ----------------------------------------------------------------------------
VOICES = {
    "ja": {"voice": "ja-JP-NanamiNeural", "lang": "ja-JP", "rate": "-8%"},
    "id": {"voice": "id-ID-GadisNeural",  "lang": "id-ID", "rate": "-8%"},
}

# 出力音質。language learning 用なので少し高めのビットレートにしています。
OUTPUT_FORMAT = "audio-24khz-96kbitrate-mono-mp3"

# Free F0 は「1分あたり20回」の呼び出し制限があります。
# 1 件ごとに少し待つことで、制限（HTTP 429）に引っかからないようにします。
# 有料プラン（S0）に変えたら 0 にしても構いません。
THROTTLE_SECONDS = 3.5
MAX_RETRIES = 5  # 429 が出たときの再試行回数

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "data.json")
MANIFEST_PATH = os.path.join(HERE, ".audio_manifest.json")


def load_manifest():
    if os.path.exists(MANIFEST_PATH):
        try:
            with open(MANIFEST_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_manifest(manifest):
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)


def fingerprint(voice_cfg, text):
    """声・速さ・本文が変わったら作り直すためのハッシュ。"""
    key = f"{voice_cfg['voice']}|{voice_cfg['rate']}|{OUTPUT_FORMAT}|{text}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def build_items(data):
    """(出力パス, 本文, 声設定, 文ID, 言語ラベル) のリストを作る。"""
    items = []
    for s in data.get("sentences", []):
        sid = s.get("id", "?")
        pairs = (
            (s.get("audio"),    s.get("jp"),  VOICES["ja"], "🇯🇵"),
            (s.get("audio_id"), s.get("idn"), VOICES["id"], "🇮🇩"),
        )
        for out_path, text, voice_cfg, label in pairs:
            if not out_path or not text:
                continue
            items.append({
                "path": out_path,
                "abspath": os.path.join(HERE, out_path),
                "text": text.strip(),
                "voice": voice_cfg,
                "sid": sid,
                "label": label,
            })
    return items


def needs_generation(item, manifest, force):
    if force:
        return True
    if not os.path.exists(item["abspath"]):
        return True
    return manifest.get(item["path"]) != fingerprint(item["voice"], item["text"])


def synthesize(item, key, region, session):
    """Azure TTS を呼び出して MP3 を書き出す。成功で True。"""
    voice_cfg = item["voice"]
    ssml = (
        f"<speak version='1.0' xml:lang='{voice_cfg['lang']}'>"
        f"<voice name='{voice_cfg['voice']}'>"
        f"<prosody rate='{voice_cfg['rate']}'>{escape(item['text'])}</prosody>"
        f"</voice></speak>"
    )
    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
        "User-Agent": "shadowing-n4-tts",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        resp = session.post(url, headers=headers,
                            data=ssml.encode("utf-8"), timeout=30)

        if resp.status_code == 200:
            os.makedirs(os.path.dirname(item["abspath"]), exist_ok=True)
            with open(item["abspath"], "wb") as f:
                f.write(resp.content)
            return True

        if resp.status_code == 401:
            raise SystemExit("HTTP 401: キーが間違っています。"
                             "Azure ポータルからキーをコピーし直してください。")
        if resp.status_code == 403:
            raise SystemExit(f"HTTP 403: リージョン名が違う可能性があります"
                             f"（現在: {region}）。エンドポイントに合わせてください。")
        if resp.status_code == 429:
            # Free F0 のレート上限。Retry-After 秒（無ければ漸増）待って再試行。
            wait = int(resp.headers.get("Retry-After", 0)) or attempt * 15
            if attempt == MAX_RETRIES:
                raise SystemExit(
                    "HTTP 429: 呼び出し上限が続いています。少し時間をおいて"
                    "もう一度 `python generate_audio.py` を実行してください"
                    "（生成済みの分は自動でスキップされます）。")
            print(f"    レート上限のため {wait} 秒待機して再試行します "
                  f"({attempt}/{MAX_RETRIES})...", flush=True)
            time.sleep(wait)
            continue

        # その他のエラーは一時的なこともあるので数回だけ再試行
        if attempt == MAX_RETRIES:
            raise SystemExit(f"HTTP {resp.status_code}: {resp.text[:200]}")
        time.sleep(attempt * 3)

    return False


def main():
    parser = argparse.ArgumentParser(
        description="Shadowing N4 の音声を Azure ニューラル TTS で生成します。")
    parser.add_argument("--dry-run", action="store_true",
                        help="生成せず、対象件数と課金対象の文字数を表示するだけ")
    parser.add_argument("--force", action="store_true",
                        help="マニフェストを無視して全件を作り直す")
    args = parser.parse_args()

    if not os.path.exists(DATA_PATH):
        raise SystemExit(f"data.json が見つかりません: {DATA_PATH}")
    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)

    items = build_items(data)
    manifest = load_manifest()
    pending = [it for it in items if needs_generation(it, manifest, args.force)]

    # --- 一覧と集計 ---
    total_chars = sum(len(it["text"]) for it in pending)
    shown = {}  # sid -> label の順で軽く一覧表示
    for it in items:
        shown.setdefault(it["sid"], []).append(it["label"])
    for sid in sorted(shown):
        mark = "→ 生成" if any(
            p["sid"] == sid for p in pending) else "  済み"
        print(f"  {sid} {' '.join(shown[sid])}  {mark}")

    print(f"\n生成予定: {len(pending)} 件 / "
          f"課金対象の文字数: 約 {total_chars:,} 文字")
    if not args.force and len(pending) < len(items):
        print(f"（全 {len(items)} 件中、変更・未生成の分だけが対象です。"
              f"全件作り直すには --force）")

    if args.dry_run:
        print("\n--dry-run のため生成は行いませんでした。")
        return
    if not pending:
        print("\nすべて最新です。生成する音声はありません。")
        return

    # --- キーの確認 ---
    key = os.environ.get("AZURE_SPEECH_KEY", "").strip()
    region = os.environ.get("AZURE_SPEECH_REGION", "japaneast").strip()
    if not key:
        raise SystemExit(
            "AZURE_SPEECH_KEY が未設定です。\n"
            "  PowerShell:  $env:AZURE_SPEECH_KEY=\"（コピーしたキー）\"\n"
            "  Mac/Linux :  export AZURE_SPEECH_KEY=\"（コピーしたキー）\"\n"
            "を実行してから、もう一度お試しください。")

    # --- 生成 ---
    session = requests.Session()
    done = 0
    for i, it in enumerate(pending):
        print(f"  生成中 {it['sid']} {it['label']} -> {it['path']} "
              f"({i + 1}/{len(pending)}) ...", flush=True)
        synthesize(it, key, region, session)
        manifest[it["path"]] = fingerprint(it["voice"], it["text"])
        done += 1
        # 途中で失敗しても、そこまでの成果を残すためこまめに保存
        save_manifest(manifest)
        # Free F0 のレート上限に配慮して次の呼び出しまで少し待つ
        if i + 1 < len(pending) and THROTTLE_SECONDS:
            time.sleep(THROTTLE_SECONDS)

    print(f"\n完了: {done} 件の MP3 を生成しました。")
    print("audio/ と audio_id/ の中身を確認してください。"
          "（例: audio/s001.mp3 を再生）")


if __name__ == "__main__":
    main()
