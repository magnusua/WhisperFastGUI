# FTW — Help

FTW converts speech in audio and video files into text and subtitles. You can also add text and office documents to the queue (convert to Markdown, then optionally AI post-processing and Word).

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
4. If **MD → Word** is on, Pandoc creates `.docx` after Markdown is ready (or after each AI result). If Pandoc is missing, the app can install it from the **Environment** menu, or from https://pandoc.org/installing.html

The log shows conversion steps, output paths, AI progress, and final results.

## Processing part of a file

Double-click a queue row to edit **Start**, intermediate segment boundaries, and **End** (audio/video). Only the selected time range is processed. Results for ranges receive time suffixes, so several parts of one file can be saved separately.

**Shift+click** a row to show the source file in the file manager.

## Recognition language, device, and model

- **Recognition language** is an icon showing the current choice: **AUTO**, **RU**, **UK**, or **EN**. Click it to open the choice window. **AUTO** detects the spoken language from the recording. Choose **EN**, **UK**, or **RU** when the language is known. This does not change the interface language.
- **Device: AUTO** uses an NVIDIA GPU when available and otherwise uses the CPU.
- **GPU** uses CUDA. If an NVIDIA GPU has powered down, FTW wakes it first; without a GPU the work runs on the CPU.
- **CPU** works without CUDA and is suitable for systems with AMD or integrated graphics.
- Click the chip icon to select, download, load, or update a Whisper model. The current name is in the tooltip. Smaller models are faster; larger models generally provide better recognition.

## Output options

- **Play sound** notifies you when the queue finishes (including after AI post-processing, if enabled).
- **Save MP3** — a note icon. Click chooses where to save the file. Shift+click or Space turns saving on or off. A green icon means saving is on.
- **MD → Word** — a document icon. Click shows the export note and Pandoc status. Shift+click turns export on or off. A green icon means export is on.
- **Save directory** — **Save** settings: next to the source, a selected folder, a named folder next to the video, or a selected folder plus a parameterized subfolder name (`{basename}`).
- Click a file link in the log to open it.
- **Shift+click** a log link to show the file in its folder.

## Directory watch

Turn watching on or off with Shift+click on the eye (open means on, closed means off). A normal click opens the folder list (saved in `settings.json`, comma-separated). If no folder is set, watching uses your Downloads folder. New supported files go to pending first: age ≥ 10 s, size stable ~15 s, and the file must be openable. Then they are queued and processed. App-created outputs are ignored. On decode errors — up to 2 retries from pending.

If another task is running, new files wait in the queue and start automatically afterward.

## AI post-processing (Cursor / Gemini / Claude / Copilot)

Enable **To AI** (Shift+click the prompts icon; green means it is on) to process generated `.txt` (after transcription) or `.md` (documents) using prompts from the `promts/` folder (one JSON file each).

- Click the icon to open the prompt list. A check mark on a row means the prompt runs by default; **Edit** opens that prompt file. Hover a name for a short description.
- **API keys** opens from the **Prompts** window. It is one dialog for Cursor, Gemini, Claude, Azure OpenAI (Copilot), Ollama, and OpenAI-compatible URLs. **Sign in to Gemini** opens the browser and copies the auth URL (same pattern as Cursor Chat / Google Calendar). **Test connection** pings the selected provider. Closing with X discards changes. On Windows, keys are stored encrypted (DPAPI). On macOS they go into the login Keychain. Environment variables take priority: `CURSOR_API_KEY`, `GEMINI_API_KEY` / `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY` / `CLAUDE_API_KEY`, `AZURE_OPENAI_*`, `OLLAMA_HOST`, `OPENAI_BASE_URL` / `OPENAI_API_KEY`.

After Whisper or document conversion, the log shows a clickable **Send to AI** link and the **Prompts** dialog opens (unless an auto-run rule matches):

- Choose integration: **Cursor** | **Gemini** | **Claude** | **Copilot**.
- Checkboxes / click a name to select prompts (pre-checked from the **To AI** overview).
- **Run** (or **Space**) — only checked prompts; **All** — select all and run.
- Closing the dialog skips AI; you can reopen the picker from the log link.

| Integration | With API key | Without key (fallback) |
|-------------|--------------|------------------------|
| **Cursor** | Cursor SDK, prompt chain | Cursor Chat + prompt on clipboard |
| **Gemini** | Google Generative Language API, or browser OAuth (Desktop client + PKCE) | Browser gemini.google.com + clipboard |
| **Claude** | Anthropic Messages API | Browser claude.ai + clipboard |
| **Copilot** | Azure OpenAI (endpoint + key + deployment) | Browser copilot.microsoft.com + clipboard |
| **Ollama** | Local daemon, no key | Needs Ollama running |
| **OpenAI-compatible** | `/v1/chat/completions` | Browser fallback |

## Archive, capture, extra exports

- **Archive** lists processed jobs (`library.sqlite`). Search by transcript text. The **Sent to** column lists everyone who already received the results. **Send to Telegram** sends the same files as the queue; Shift+click always asks for a recipient, even when a chat is already known. Double-click an SRT line to hear that moment (needs the source or a saved MP3). **Delete all files** removes the source and every derivative. **Ask** sends a question about that transcript to the current API provider. **Speakers** renames You/Them using the first and longest utterance.
- **Record** (also tray / **Ctrl+Shift+R**) captures microphone + system audio. PCM is written to disk as you go; **Stop** and **Clip** encode to Opus (default 24 kbit/s mono), AAC, MP3, or WAV and always enqueue. **Pause** / **Ctrl+Shift+P** skips audio until you resume. The gear opens **recording settings**: mix vs device, codec, auto-record (Zoom/Teams/Meet/Telegram/Viber/Phone Link/WhatsApp), Google Calendar / Outlook / ICS, filename tokens (`%W`, `%C`, …). **Sign in to Google Calendar** opens the browser (same pattern as Cursor Chat without an API key) and copies the auth URL; you still need your own Desktop OAuth client ID (Google does not allow shipping one). PKCE, secret optional. Manual Start/Stop outranks auto-record and the calendar. A 15 s silence auto-stop applies only to auto sessions. You are asked to tell the room the first time. An interrupted recording is repaired on the next launch. CLI: `python main.py sessions` | `process <folder>` | `record start|stop`.
- **Save settings** can also write **JSON** segments, **WebVTT**, word timestamps, and stereo speaker labels (You/Them). Session folders get `meta.json`, `speakers.json`, and `summary.md` next to the transcript.

Output file names come from the prompt title quotes (e.g. `*_TW_core.md` from `## Prompt #2 "TW_core"`). Empty sections are skipped.

## Telegram

The Telegram icon opens settings. Shift+click starts or stops the listener in this FTW window; a green icon means the listener is on. If it was on when FTW closed, the listener starts again with the window. If the account or bot is not filled in yet, an error asks you to open settings first. Keep FTW open while the listener runs. Hover a field for where the value comes from.

A YouTube, Instagram, or Facebook link is downloaded and sent back to the chat. It enters the Whisper queue only when “Put videos downloaded from social networks into the Whisper queue” is on. Telegram settings can take voice, video, and links, voice and video only, or links only. Learning mode does not process a new chat until you answer. The question only decides whether to take the file. Several files from one chat within a few seconds are asked about together. No stops processing that chat. Closing the window, or 10 seconds without an answer, skips only this file. The Process line in the log asks again.

- **Account** — private chats of this Telegram user. You need `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org) (API development tools, platform Desktop) and a phone number such as `+380501111111`. **Sign in** receives the code inside Telegram, not by SMS; a cloud password is asked for when the account has one. The listener can start only after you are signed in.
- **Bot** — a bot from @BotFather: send `/newbot`, the username must end with `bot`, and the token looks like `123456789:AAH…`. You also need `api_id`, `api_hash`, and a local `telegram-bot-api.exe` (files up to about 2 GB) from [tdlib/telegram-bot-api](https://github.com/tdlib/telegram-bot-api/releases). The default address is `http://127.0.0.1:8081`. Before the first local start, call `logOut` on the cloud Bot API for this token.

Audio and video go into the queue. As soon as a file is accepted, a line appears in the FTW log, before the download finishes. Results return to the same chat: the transcript (TXT), every AI file, an MP3 extracted from a video, and a later audio clip (caption “Clip of the audio”). An MP3 made from an audio file is not sent back. Stopping transcription in the middle of a file sends nothing for it; files that already finished, and their AI output, are sent on their own. The same Telegram file (a forward) is not downloaded again: if it is still in the queue, the new message receives the results when processing finishes; if the transcript or AI files already exist, those files are sent. After processing the originals stay on disk next to the transcript; the chat receives a copy.

In account mode, messages you send yourself are skipped, except **Saved Messages** and the chats named in “Also process your own audio and video in these chats” (first name, full name, or `@username`, ignoring case). A group with that title sends every audio and video. When the listener starts, the same notice is sent to Saved Messages. An empty chat-id list means every private chat. In bot mode, while the chat-id list is empty, the bot ignores media and answers `/start` with that chat’s id.

In the queue, the trash column removes the row (the archive entry stays). The Telegram column sends the same result set as an automatic reply. If the file is not tied to a chat, a window asks for a group or contact name and lists the last 10 recipients. Shift+click on that column always asks who should receive the files, even when a chat is already known. Status shows an hourglass while waiting, a check mark when the transcript is done, `AI` / `TG` / `AI + TG` for the later steps, or the error text.

## Log

- The log is stored in `app_log.json` next to the program (batched writes).
- Entries are grouped by day; past days load when expanded; **today** stays expanded.
- One input file → one log block (segments, TXT/SRT/AI paths, etc.).
- **Clear log** clears the window and `app_log.json`. The word Log and the button sit on the left of the same row as Start.
- The progress bar appears above the log only while transcription is running.

## Buttons

- **Start** — a play icon next to the recognition-language icon. Starts the queue.
- **Cancel** — stop the current task.
- **Add files / Add directory** — add media or documents.
- **Archive** — search past transcripts; click a line to hear it. **Sent to** column and **Send to Telegram**.
- **Record** — capture microphone + system audio into the queue (Ctrl+Shift+R). Pause with Ctrl+Shift+P. **Clip** saves the last N seconds; the gear opens recording settings.
- **Clear queue** — remove all queue items.
- **Environment** — one puzzle icon. The menu is **Check system** (Python, FFmpeg, Pandoc, GPU, CUDA), **Check updates** (the app, pip, Whisper models, FFmpeg/Pandoc; a package is offered only when the version is newer and matches this Python), and **Install or reinstall** pip packages (including `markitdown`) and system tools.
- **Model** — a chip icon. The current model name is in the tooltip. The device icon to its left opens AUTO, GPU, or CPU. In GPU mode the video card stays awake while FTW is open.
- **Clear log** — clear the log window and `app_log.json`.
- **Autostart** — the icon turns delayed Windows startup on or off.
- **To AI** — click opens the Prompts window, Shift+click turns AI on or off. A green icon means AI is on. API keys are in the Prompts window.
- **Telegram** — click opens account or bot settings. Shift+click starts or stops the listener. A green icon means the listener is on.
- **Help** — open this file in the interface language. Use the document list at the top to also read architecture, setup, and other docs from `docs/`.

## Display modes

The taskbar icon opens the choice. The current mode is marked in the menu.

- **Taskbar** — the program appears on the taskbar; closing the window exits after confirmation.
- **Tray** — the program runs in the system tray; closing the window hides it. Use the tray menu → **Exit** to quit.
- **Taskbar + Tray** — the program appears in both places; closing exits after confirmation.

## Keyboard and mouse

- **Enter** — add files when the queue is empty, otherwise start processing.
- **Space** — toggle **Save MP3** when focus is not in a text field; in the **Prompts** dialog — same as **Run**.
- **Delete** — remove selected queue items.
- **Ctrl+V** — paste a directory path into the save-folder field (or a row in the watch-folders dialog).
- **Double-click a queue row** — edit its time range.
- **Shift+click a queue row** — show the source file. Shift+click the Telegram column asks who should receive the results.
- **Click a log link** — open the file; **Shift+click** — show it in the folder.

## Interface language

Use the **EN / UK / RU** dropdown at the top of the window. The interface language does not change the speech recognition language.

## If processing does not start

1. Open **Environment → Check system** and check Python, FFmpeg, CUDA, Pandoc (for Word export), and dependencies.
2. Use **Environment → Install or reinstall** to install missing pip packages and system tools (FFmpeg, Pandoc).
3. Try **AUTO** or **CPU** if GPU processing fails.
4. Check that the source media file contains an audio track.
5. Read the log for the exact error.
