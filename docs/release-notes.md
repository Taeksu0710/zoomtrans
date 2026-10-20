First public Preview of ZoomTrans, a local English-to-Korean meeting subtitle app.

**Requires Apple Silicon and macOS 26 or later. Intel is not supported.**

Download the ZIP, extract `zoomtrans.app`, and move it to Applications. Python
and Xcode are not required. Install BlackHole **16ch**, run Ollama, and download
`gemma4:e4b` separately. The app can download the selected Whisper model when
you explicitly choose to do so. Optional 12B translation starts disabled.

- [Installation and first launch](https://github.com/Taeksu0710/zoomtrans/blob/main/docs/installation.md)
- [Features and usage](https://github.com/Taeksu0710/zoomtrans#readme)
- [Licenses and third-party components](https://github.com/Taeksu0710/zoomtrans/blob/main/THIRD_PARTY_NOTICES.md)

This Preview has ad-hoc signing, **no Developer ID signing or Apple notarization**.
macOS may block the first launch; use Apple's per-app Open Anyway procedure
after verifying the source. Do not disable system-wide security.

Model weights, Ollama and BlackHole are not included. First-time setup requires
internet access and sufficient disk space/memory. Recognition and translation
run locally afterward. Translation can be delayed or inaccurate; check important
details against the original. Transcripts and optional summaries are saved locally.

Verify the download with the attached `SHA256SUMS.txt`.
