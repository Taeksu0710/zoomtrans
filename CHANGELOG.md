# Changelog

## 0.1.0-preview.1

First public preview for Apple Silicon Macs running macOS 26 or newer.

- Local English recognition and Korean translation with a compact subtitle overlay.
- Sentence continuity, bounded dialogue context, ordered translation and complete Korean output.
- Optional session-only 12B model for long sentences and compound conditional clauses.
- Subtitle history, display controls, local transcripts and optional meeting summaries.
- Bundled Python runtime and precompiled audio helper; prerequisite checks and explicit Whisper download.

Preview limitations: no Developer ID signing or notarization; BlackHole 16ch,
Ollama and AI model weights are separate installations. Hardware, model size,
speech and queue backlog affect latency and accuracy. Intel, Windows and Linux
are not supported by this release.
