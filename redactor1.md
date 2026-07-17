# Redactor prompts for Whisper Fast GUI

Numbered prompts below are applied in order after transcription.
Prompt 1 writes `*_edited.md`; prompts 2+ write `*_edited_N.md`.

## Промпт №1

Clean up the transcript: fix obvious punctuation and capitalization,
remove filler words where safe, keep the original meaning and language.
Output Markdown only (no commentary outside the document).

## Промпт №2

Add a short title and a brief summary at the top, then keep the cleaned body.

