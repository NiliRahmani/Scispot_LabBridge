# LabBridge demo video recorder

Records a narrated walkthrough of the LabBridge Streamlit app — a bottom
subtitle bar plus a synced amber highlight box on the element each caption
describes — and saves it as video. No app code is touched; the recorder only
drives the running app from the outside.

## What it produces

A ~2-minute 1280×720 screen recording of the 5-step wizard (upload → mapping →
QC → clean preview → export), with burned-in captions and highlights.

## Requirements

- Node (uses Playwright + the cached Chromium already on this machine)
- A full `ffmpeg` to turn Playwright's `.webm` into a shareable `.mp4`
  (the project's venv has one via `imageio-ffmpeg`)

## Run

```bash
# 1. start the app on a fixed port
labbridge/.venv/Scripts/python.exe -m streamlit run labbridge/app.py \
    --server.headless true --server.port 8531

# 2. in a scratch folder with playwright installed (npm i playwright@1.61.0):
URL=http://localhost:8531 OUTDIR=./video node record.js   # -> ./video/<hash>.webm

# 3. transcode to mp4 (ffmpeg path shown for the venv's bundled binary)
ffmpeg -y -i ./video/*.webm \
    -vf "scale=1280:720:flags=lanczos,fps=25" \
    -c:v libx264 -profile:v high -pix_fmt yuv420p -crf 20 -movflags +faststart \
    LabBridge_demo.mp4
```

## Editing the narration

Captions and timings live inline in `record.js` as `beat(text, target, ms)`
calls. Each `beat`:

- sets the subtitle text (HTML entities like `&mdash;` are fine),
- draws the highlight around `target` (a Playwright locator, or `null` for none),
- holds for `ms` milliseconds.

To retime, change the `ms` values; to re-point a highlight, change the locator.
Total video length ≈ the sum of the `ms` values plus the click/rerun waits.
