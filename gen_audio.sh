#!/bin/bash
# Generate MP3 audio for every sentence in data.json using Open JTalk (Mei voice)
set -e
cd "$(dirname "$0")"
DIC=/var/lib/mecab/dic/open-jtalk/naist-jdic
VOICE=/usr/local/lib/python3.11/dist-packages/pyopenjtalk/htsvoice/mei_normal.htsvoice
mkdir -p audio
python3 - <<'EOF'
import json, subprocess, os
with open("data.json", encoding="utf-8") as f:
    data = json.load(f)
DIC = "/var/lib/mecab/dic/open-jtalk/naist-jdic"
VOICE = "/usr/local/lib/python3.11/dist-packages/pyopenjtalk/htsvoice/mei_normal.htsvoice"
ok = 0
for s in data["sentences"]:
    out = s["audio"]
    wav = "/tmp/tts_tmp.wav"
    p = subprocess.run(["open_jtalk", "-x", DIC, "-m", VOICE, "-r", "0.92", "-ow", wav],
                       input=s["jp"].encode("utf-8"), capture_output=True)
    if p.returncode != 0:
        print("ERR", s["id"], p.stderr.decode()); continue
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", wav,
                    "-codec:a", "libmp3lame", "-b:a", "64k", "-ac", "1", out], check=True)
    ok += 1
print(f"generated {ok}/{len(data['sentences'])}")
EOF
