# Whisper Fast GUI — Help

Whisper Fast GUI converts speech in audio and video files into text and subtitles. You can also add text and office documents to the queue (convert to Markdown, then optionally AI post-processing and Word).

## Quick start

1. Add media or documents with **Add files**, **Add directory**, or drag and drop into the window.
2. For audio/video: choose the recognition language (**AUTO**, **EN**, **UK**, or **RU**) and device (**AUTO**, **GPU**, or **CPU**).
3. If needed, choose a Whisper model by clicking its name.
4. Choose where to save results (empty = next to the source file).
5. Select a queue item and press **Start**.

For audio/video the program creates:
- `.txt` — plain transcription;
- `.srt` — subtitles with timestamps;
- `_audio.mp3` — extracted audio when **Save MP3** is enabled.

For documents it creates Markdown (`.md`), and optionally AI outputs and Word (`.docx`).

## Adding and managing files

- **Add files** selects one or more supported files.
- **Add directory** adds supported files from a directory and its subdirectories.
- Drag files into the program window to add them.
- Drag queue rows to change the processing order.
- Use **Delete** or right-click → **Delete** to remove selected rows.
- Use **Clear queue** to remove all rows.
- The queue is saved automatically and restored at the next launch.

Supported audio: `.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg`.

Supported video: `.mp4`, `.mkv`, `.avi`, `.mov`, `.wmv`, `.webm`.

Supported text: `.md`, `.markdown`, `.txt`, `.text`, `.rst`, `.csv`, `.html`, `.htm`.

Supported documents: `.pdf`, `.doc`, `.docx` (converted to Markdown; Whisper is not used).

## Processing the queue

- With one item, **Start** processes it immediately.
- With several items, choose the selected item, new items only, or the whole queue.
- **Cancel** stops the current task.
- Processed items are marked in the queue.
- Only one queue task runs at a time (transcription or document processing).

## Documents in the queue

1. PDF/DOC/DOCX are converted to `.md` (package `markitdown`).
2. Other text formats are prepared as Markdown in the save folder.
3. If **To AI** is on, a prompt dialog opens (Cursor / Gemini / Claude / Copilot); **Markdown** is sent to AI. The log notes that the original PDF/DOC/DOCX was **not** passed to AI.
4. If **MD → Word** is on, Pandoc creates `.docx` after Markdown is ready (or after each AI result). If Pandoc is missing, the app can install it via **Dependencies** / **Updates**, or from https://pandoc.org/installing.html

The log shows conversion steps, output paths, AI progress, and final results.

## Processing part of a file

Double-click a queue row to edit **Start**, intermediate segment boundaries, and **End** (audio/video). Only the selected time range is processed. Results for ranges receive time suffixes, so several parts of one file can be saved separately.

**Shift+click** a row to show the source file in the file manager.

## Recognition language, device, and model

- **Recognition language: AUTO** detects the spoken language automatically.
- Choose **EN**, **UK**, or **RU** when the language is known.
- **Device: AUTO** uses an NVIDIA GPU when available and otherwise uses the CPU.
- **GPU** forces CUDA processing.
- **CPU** works without CUDA and is suitable for systems with AMD or integrated graphics.
- Click the model name to select, download, load, or update a Whisper model. Smaller models are faster; larger models generally provide better recognition.

## Output options

- **Play sound** notifies you when the queue finishes (including after AI post-processing, if enabled).
- **Save MP3** extracts the processed audio to a separate file.
- **MD → Word** exports Markdown to `.docx` via Pandoc.
- **Save directory** selects where results are written. An empty field means “next to the source file.”
- Click a file link in the log to open it.
- **Shift+click** a log link to show the file in its folder.

## Directory watch

Enable **Watch** and use **Folder** to set one or more directories (saved in `settings.json`, comma-separated). New supported files go to pending first: age ≥ 10 s, size stable ~15 s, and the file must be openable. Then they are queued and processed. App-created outputs are ignored. On decode errors — up to 2 retries from pending.

If another task is running, new files wait in the queue and start automatically afterward.

## AI post-processing (Cursor / Gemini / Claude / Copilot)

Enable **To AI** to process generated `.txt` (after transcription) or `.md` (documents) using prompts from `redactor1.md`.

- The **To AI** label opens the prompt file.
- **API keys** opens one dialog for Cursor, Gemini, Claude, and Azure OpenAI (Copilot). Closing with X discards changes. Environment variables take priority: `CURSOR_API_KEY`, `GEMINI_API_KEY` / `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY` / `CLAUDE_API_KEY`, `AZURE_OPENAI_*`.

After Whisper or document conversion, the log shows a clickable **Send to AI** link and the **Prompts** dialog opens:

- Choose integration: **Cursor** | **Gemini** | **Claude** | **Copilot**.
- Checkboxes / click a name to select prompts (the first is checked by default).
- **Run** (or **Space**) — only checked prompts; **All** — select all and run.
- Closing the dialog skips AI; you can reopen the picker from the log link.

| Integration | With API key | Without key (fallback) |
|-------------|--------------|------------------------|
| **Cursor** | Cursor SDK, prompt chain | Cursor Chat + prompt on clipboard |
| **Gemini** | Google Generative Language API | Browser gemini.google.com + clipboard |
| **Claude** | Anthropic Messages API | Browser claude.ai + clipboard |
| **Copilot** | Azure OpenAI (endpoint + key + deployment) | Browser copilot.microsoft.com + clipboard |

Output file names come from the prompt title quotes (e.g. `*_TW_core.md` from `## Prompt #2 "TW_core"`). Empty sections are skipped.

## Log

- The log is stored in `app_log.json` next to the program (batched writes).
- Entries are grouped by day; past days load when expanded; **today** stays expanded.
- One input file → one log block (segments, TXT/SRT/AI paths, etc.).
- **Clear log** clears the window and `app_log.json`.

## Buttons

- **Start** — start processing the queue.
- **Cancel** — stop the current task.
- **Add files / Add directory** — add media or documents.
- **Clear queue** — remove all queue items.
- **System** — check Python, FFmpeg, Pandoc, GPU, CUDA, and installed components.
- **Dependencies** — install or reinstall pip packages (including `markitdown`) and system tools (FFmpeg, Pandoc).
- **Updates** — check app, pip, Whisper model, and FFmpeg/Pandoc updates, then install the selected items.
- **Model name** — select and manage the Whisper model.
- **Clear log** — clear the log window and `app_log.json`.
- **Autostart** — add delayed startup on Windows.
- **To AI** — enable AI post-processing; the label opens `redactor1.md`.
- **API keys** — Cursor / Gemini / Claude / Azure OpenAI keys.
- **Help** — open this file in the interface language. Use the document list at the top to also read architecture, setup, and other docs from `docs/`.

## Display modes

- **Taskbar** — the program appears on the taskbar; closing the window exits after confirmation.
- **Tray** — the program runs in the system tray; closing the window hides it. Use the tray menu → **Exit** to quit.
- **Taskbar + Tray** — the program appears in both places; closing exits after confirmation.

## Keyboard and mouse

- **Enter** — add files when the queue is empty, otherwise start processing.
- **Space** — toggle **Save MP3** when focus is not in a text field; in the **Prompts** dialog — same as **Run**.
- **Delete** — remove selected queue items.
- **Ctrl+V** — paste a directory path into the save-folder field (or a row in the watch-folders dialog).
- **Double-click a queue row** — edit its time range.
- **Shift+click a queue row** — show the source file.
- **Click a log link** — open the file; **Shift+click** — show it in the folder.

## Interface language

Use **EN / UK / RU** at the top of the window. The interface language does not change the speech recognition language.

## If processing does not start

1. Open **System** and check Python, FFmpeg, CUDA, Pandoc (for Word export), and dependencies.
2. Use **Dependencies** to install missing pip packages and system tools (FFmpeg, Pandoc).
3. Try **AUTO** or **CPU** if GPU processing fails.
4. Check that the source media file contains an audio track.
5. Read the log for the exact error.
