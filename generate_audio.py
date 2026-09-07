#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_audio.py — Shadowing N4 の音声を edge-tts（無料）で生成するスクリプト

data.json の各文について MP3 を書き出します。
  日本語（jp / jp_tts）   → audio/     （--lang ja）
  インドネシア語（idn）    → audio_id/  （--lang id）
  ミャンマー語（mya）      → audio_my/  （--lang my）

使い方:
    pip install edge-tts

    python generate_audio.py --lang ja                     # 日本語のみ（既定の声）
    python generate_audio.py --lang all --missing-only     # 無いファイルだけ全言語生成
    python generate_audio.py --lang my                     # ミャンマー語のみ

主な声:
  日本語        女性: ja-JP-NanamiNeural / 男性: ja-JP-KeitaNeural
  インドネシア語 女性: id-ID-GadisNeural  / 男性: id-ID-ArdiNeural
  ミャンマー語   女性: my-MM-NilarNeural  / 男性: my-MM-ThihaNeural
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
MY_VOICE_DEFAULT = "my-MM-NilarNeural"
RATE = "+0%"        # 話速は標準
MAX_RETRIES = 4
CONCURRENCY = 4     # 同時生成数（上げすぎるとレート制限のおそれ）

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "data.json")


async def synth(sem, text, voice, out_path, label, progress):
    async with sem:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        last_err = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await edge_tts.Communicate(text, voice, rate=RATE).save(out_path)
                if os.path.getsize(out_path) > 0:
                    progress[0] += 1
                    if progress[0] % 25 == 0 or progress[0] == progress[1]:
                        print(f"  {progress[0]}/{progress[1]} 完了 (最新: {label})",
                              flush=True)
                    return
                last_err = RuntimeError("0バイトのファイルが生成されました")
            except Exception as e:  # ネットワーク一時エラー等
                last_err = e
            await asyncio.sleep(attempt * 3)
        raise SystemExit(f"生成失敗 {out_path}: {last_err}")


async def main():
    parser = argparse.ArgumentParser(
        description="Shadowing N4 の音声を edge-tts で生成します。")
    parser.add_argument("--lang", choices=["ja", "id", "my", "both", "all"],
                        default="ja",
                        help="生成する言語（both=ja+id / all=ja+id+my）")
    parser.add_argument("--voice", default=None,
                        help="日本語の声（既定: %s）" % JA_VOICE_DEFAULT)
    parser.add_argument("--id-voice", default=ID_VOICE_DEFAULT)
    parser.add_argument("--my-voice", default=MY_VOICE_DEFAULT)
    parser.add_argument("--missing-only", action="store_true",
                        help="既にあるファイルはスキップして無い分だけ生成")
    args = parser.parse_args()

    langs = {"ja": ["ja"], "id": ["id"], "my": ["my"],
             "both": ["ja", "id"], "all": ["ja", "id", "my"]}[args.lang]

    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    sentences = data.get("sentences", [])

    jobs = []  # (出力パス, 本文, 声, ラベル)
    for s in sentences:
        pairs = []
        if "ja" in langs and s.get("audio") and s.get("jp"):
            # jp_tts があればそちらを読み上げに使う（読み間違い対策。表示は jp のまま）
            pairs.append((s["audio"], (s.get("jp_tts") or s["jp"]),
                          args.voice or JA_VOICE_DEFAULT, "🇯🇵"))
        if "id" in langs and s.get("audio_id") and s.get("idn"):
            pairs.append((s["audio_id"], s["idn"], args.id_voice, "🇮🇩"))
        if "my" in langs and s.get("audio_my") and s.get("mya"):
            pairs.append((s["audio_my"], s["mya"], args.my_voice, "🇲🇲"))
        for rel, text, voice, flag in pairs:
            path = os.path.join(HERE, rel)
            if args.missing_only and os.path.exists(path) and os.path.getsize(path) > 0:
                continue
            jobs.append((path, text.strip(), voice, f"{s['id']} {flag}"))

    print(f"全 {len(sentences)} 文 / 生成対象 {len(jobs)} ファイル "
          f"(並列 {CONCURRENCY})")
    if not jobs:
        print("生成するファイルはありません。")
        return

    sem = asyncio.Semaphore(CONCURRENCY)
    progress = [0, len(jobs)]
    await asyncio.gather(*[synth(sem, t, v, p, lb, progress)
                           for p, t, v, lb in jobs])

    bad = [p for p, *_ in jobs if not os.path.exists(p) or os.path.getsize(p) == 0]
    if bad:
        raise SystemExit(f"エラー: 不正なファイルがあります: {bad[:10]}")
    print(f"\n完了: {len(jobs)} 件の MP3 を生成しました"
          "（すべて 0 バイトでないことを確認済み）。")


if __name__ == "__main__":
    asyncio.run(main())
