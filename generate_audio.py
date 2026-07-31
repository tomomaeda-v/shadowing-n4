#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_audio.py — Shadowing N4 の音声を edge-tts（無料）で生成するスクリプト

data.json の各文について MP3 を書き出します。
  日本語（jp）      → audio/    （--lang ja）
  インドネシア語（idn）→ audio_id/ （--lang id）

使い方:
    pip install edge-tts

    python generate_audio.py --lang ja                     # 日本語のみ（既定の声）
    python generate_audio.py --lang ja --voice ja-JP-KeitaNeural
    python generate_audio.py --lang id                     # インドネシア語のみ
    python generate_audio.py --lang both                   # 両方

主な声:
  日本語   女性: ja-JP-NanamiNeural / 男性: ja-JP-KeitaNeural
  インドネシア語 女性: id-ID-GadisNeural / 男性: id-ID-ArdiNeural
"""

import argparse
import asyncio
import json
import os
import sys

# Windows の cp932 コンソールでも絵文字入りログが落ちないようにする
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import edge_tts
except ImportError:
    print("エラー: edge-tts が必要です。次を実行してください:\n"
          "    pip install edge-tts", file=sys.stderr)
    sys.exit(1)

JA_VOICE_DEFAULT = "ja-JP-NanamiNeural"
ID_VOICE_DEFAULT = "id-ID-GadisNeural"
RATE = "+0%"        # 話速は標準
MAX_RETRIES = 3

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "data.json")


async def synth(text, voice, out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            await edge_tts.Communicate(text, voice, rate=RATE).save(out_path)
            if os.path.getsize(out_path) > 0:
                return
            last_err = RuntimeError("0バイトのファイルが生成されました")
        except Exception as e:  # ネットワーク一時エラー等
            last_err = e
        await asyncio.sleep(attempt * 2)
    raise SystemExit(f"生成失敗 {out_path}: {last_err}")


async def main():
    parser = argparse.ArgumentParser(
        description="Shadowing N4 の音声を edge-tts で生成します。")
    parser.add_argument("--lang", choices=["ja", "id", "both"], default="ja",
                        help="生成する言語（既定: ja）")
    parser.add_argument("--voice", default=None,
                        help="日本語の声（既定: %s）" % JA_VOICE_DEFAULT)
    parser.add_argument("--id-voice", default=ID_VOICE_DEFAULT,
                        help="インドネシア語の声（既定: %s）" % ID_VOICE_DEFAULT)
    args = parser.parse_args()

    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    sentences = data.get("sentences", [])

    jobs = []  # (出力パス, 本文, 声, ラベル)
    for s in sentences:
        if args.lang in ("ja", "both") and s.get("audio") and s.get("jp"):
            jobs.append((os.path.join(HERE, s["audio"]), s["jp"].strip(),
                         args.voice or JA_VOICE_DEFAULT, f"{s['id']} 🇯🇵"))
        if args.lang in ("id", "both") and s.get("audio_id") and s.get("idn"):
            jobs.append((os.path.join(HERE, s["audio_id"]), s["idn"].strip(),
                         args.id_voice, f"{s['id']} 🇮🇩"))

    print(f"全 {len(sentences)} 文 / 生成対象 {len(jobs)} ファイル "
          f"(声: {args.voice or JA_VOICE_DEFAULT}"
          + (f", {args.id_voice}" if args.lang in ("id", "both") else "") + ")")

    for i, (path, text, voice, label) in enumerate(jobs, 1):
        print(f"  [{i}/{len(jobs)}] {label} -> {os.path.relpath(path, HERE)}",
              flush=True)
        await synth(text, voice, path)

    # 検証: 全ファイルが存在し 0 バイトでないこと
    bad = [p for p, *_ in jobs
           if not os.path.exists(p) or os.path.getsize(p) == 0]
    if bad:
        raise SystemExit(f"エラー: 不正なファイルがあります: {bad}")
    print(f"\n完了: {len(jobs)} 件の MP3 を生成しました（すべて 0 バイトでないことを確認済み）。")


if __name__ == "__main__":
    asyncio.run(main())
