# Whisper Fast GUI — Help

Whisper Fast GUI converts speech in audio and video files into text and subtitles.

## Quick start

1. Add media with **Add files**, **Add directory**, or drag and drop files into the window.
2. Choose the recognition language: **AUTO**, **EN**, **UK**, or **RU**.
3. Choose the device: **AUTO**, **GPU**, or **CPU**.
4. If needed, choose a Whisper model by clicking its name.
5. Choose a save directory. Leave the field empty to save results next to the source file.
6. Select a queue item and press **Start**.

The program creates:
- `.txt` — plain transcription;
- `.srt` — subtitles with timestamps;
- `_audio.mp3` — extracted audio when **Save MP3** is enabled.

## Adding and managing files

- **Add files** selects one or more supported files.
- **Add directory** adds supported files from a directory and its subdirectories.
- Drag files into the program window to add them.
- Drag queue rows to change the processing order.
- Use **Delete** or right-click → **Delete** to remove selected rows.
- Use **Clear queue** to remove all rows.
- The queue is saved automatically and restored at the next launch.

Supported audio: `.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg`.

Supported video: `.mp4`, `.mkv`, `.avi`, `.mov`.

## Processing the queue

- With one item, **Start** processes it immediately.
- With several items, choose the selected item, new items only, or the whole queue.
- **Cancel** stops the current task.
- Processed items are marked in the queue.
- Only one transcription task runs at a time.

## Processing part of a file

Double-click a queue row to edit **Start**, intermediate segment boundaries, and **End**. Only the selected time range is processed. Results for ranges receive time suffixes, so several parts of one file can be saved separately.

**Shift+click** a row to show the source file in the file manager.

## Recognition language, device, and model

- **Recognition language: AUTO** detects the spoken language automatically.
- Choose **EN**, **UK**, or **RU** when the language is known.
- **Device: AUTO** uses an NVIDIA GPU when available and otherwise uses the CPU.
- **GPU** forces CUDA processing.
- **CPU** works without CUDA and is suitable for systems with AMD or integrated graphics.
- Click the model name to select, download, load, or update a Whisper model. Smaller models are faster; larger models generally provide better recognition.

## Output options

- **Play sound** notifies you when the queue finishes.
- **Save MP3** extracts the processed audio to a separate file.
- **Save directory** selects where results are written. An empty field means “next to the source file.”
- Click a file link in the log to open it.
- **Shift+click** a log link to show the file in its folder.

## Directory watch

Enable directory watch and select a directory. New supported files are automatically added and processed. Files created by Whisper Fast GUI are ignored to prevent processing loops.

If another task is running, new files wait in the queue and start automatically afterward.

## Cursor post-processing

Enable **To Cursor** to process generated `.txt` files using prompts from `redactor1.md`.

- **Change prompt** opens the prompt file.
- **api_key** saves a Cursor API key for automatic processing.
- With a working API key and Cursor SDK, prompts run in sequence and create `_edited.md` files.
- Without an API key, Cursor Chat opens and the first prompt is copied for manual confirmation.

## Buttons

- **Start** — start transcription.
- **Cancel** — stop the current task.
- **Add files / Add directory** — add media.
- **Clear queue** — remove all queue items.
- **System** — check Python, FFmpeg, GPU, CUDA, and installed components.
- **Dependencies** — install or reinstall required packages.
- **Updates** — check program, package, and model updates.
- **Model name** — select and manage the Whisper model.
- **Clear log** — clear messages.
- **Autostart** — add delayed startup on Windows.
- **Help** — open this file in the interface language.

## Display modes

- **Taskbar** — the program appears on the taskbar; closing the window exits after confirmation.
- **Tray** — the program runs in the system tray; closing the window hides it. Use the tray menu → **Exit** to quit.
- **Taskbar + Tray** — the program appears in both places; closing exits after confirmation.

## Keyboard and mouse

- **Enter** — add files when the queue is empty, otherwise start processing.
- **Space** — toggle **Save MP3** when focus is not in a text field.
- **Delete** — remove selected queue items.
- **Ctrl+V** — paste a directory path into the watch field.
- **Double-click a queue row** — edit its time range.
- **Shift+click a queue row** — show the source file.

## Interface language

Use **EN / UK / RU** at the top of the window. The interface language does not change the speech recognition language.

## If processing does not start

1. Open **System** and check Python, FFmpeg, CUDA, and dependencies.
2. Use **Dependencies** to install missing packages.
3. Try **AUTO** or **CPU** if GPU processing fails.
4. Check that the source file contains an audio track.
5. Read the log for the exact error.
