# Whisper Fast GUI

**Version 1.1.1** (15.07.2026)

A graphical interface for audio and video transcription based on Faster-Whisper (OpenAI Whisper).

Repository: https://github.com/magnusua/WhisperFastGUI

---

## Usage on different operating systems

The script supports Windows, Linux, and macOS.

### Windows 10/11
- **Launch:** double-click `main.py` or use a shortcut (`Whisper Fast GUI.lnk`).
- **Launch with automatic queue start:** from a console in the project directory: `python main.py --transcribe` — the program opens and, after half a second, automatically starts processing the entire current queue (if the queue is not empty).
- **Without a console:** `run_whisper.vbs`.
- **Installing dependencies:** `install.bat` (or the [Dependencies] button in the program).
- **Model:** the button showing the current model name (for example, "large-v3-turbo") — to the left of the "Tray" switch — opens the Whisper model selection dialog; the dialog includes **"Download model"** and **"Update model"** buttons (checks for a new revision on Hugging Face Hub).
- **Taskbar / Tray / Taskbar + Tray:** switch in the bottom panel. In **Tray** mode, closing the window (×) does not quit the program — it hides in the system tray and keeps running; exit via the tray icon menu. See the "Taskbar / Tray / Taskbar + Tray modes" section below for details.
- **Autostart:** the [Autostart] button in the program (next to the "Tray" switch) runs `autorun_delayed.bat` and adds the program to startup with a 25 s delay.
- **Clicking a link in the log:** opens the file; Shift+click — open the folder and select the file in Explorer.
- **Completion sound:** if no .mp3 file is present in the **program directory**, the Windows system sound is used.

### Linux
- **Launch:** from the project directory run: `python3 main.py` (or `python main.py`). For automatic queue processing start: `python3 main.py --transcribe`.
- **Drag & Drop and display:** requires a desktop environment with X11/Wayland support and tkinter.
- **Installation:** install Python 3, FFmpeg, and dependencies (via pip or the [Dependencies] button).
- **Opening files and folders from the log:** uses `xdg-open` (the `xdg-utils` package).
- **Completion sound:** if no .mp3 is present in the **program directory** — attempts to play a system sound (freedesktop); otherwise no sound is played.

### macOS
- **Launch:** `python3 main.py` from the project directory. For automatic queue processing start: `python3 main.py --transcribe`.
- **Opening files from the log:** `open` (normal click) and `open -R` (Shift+click — show in Finder).
- **Completion sound:** same as on Linux (system sounds when no .mp3 is present in the program directory).

On all operating systems you need: **Python 3.9–3.13** (in practice, **3.11 or 3.12** is most stable), **FFmpeg** in PATH, and dependencies (faster-whisper, torch, pydub, etc.). **Python 3.14+** is often unsuitable: there are no **ctranslate2** / **torch** wheels on PyPI yet — installation fails; use 3.12. An NVIDIA GPU with CUDA is recommended for acceleration on Windows and Linux.

---

## About hardware and the script

The script is based on OpenAI Whisper technology and the Faster-Whisper library. By default, the **large-v3-turbo** model is used (an optimal balance of quality and speed). The model can be changed: the button showing the current model name (to the left of the "Tray" switch) opens the selection dialog (tiny, base, small, medium, large-v1/v2/v3, large-v3-turbo, distil-large-v3). The dialog shows which models are already downloaded and their approximate size; the **"Download model"** button lets you load the selected model into memory immediately, without waiting for transcription to start; the **"Update model"** button downloads a new weight revision from Hugging Face Hub. The application version and date are shown at the top of the window (to the right of the buttons).

---

## Project structure

```
WhisperFastGUI/
├── main.py              — entry point: dependency check, taskbar icon, GUI launch
├── gui.py               — main window: queue, settings, transcription, log
├── model_manager.py     — Whisper model loading and unloading (Singleton)
├── input_files.py       — adding files/directories, validation, Drag & Drop
├── i18n.py              — single import point for translations (lang_manager or i18n_fallback)
├── lang_manager.py      — UI translations (EN/UK/RU), lang.json loading
├── i18n_fallback.py     — fallback t/set_language functions when lang_manager is unavailable
├── config.py            — constants (BASE_DIR, version, GitHub, formats, model, queue, intervals), README loading
├── utils.py             — time formatting (including for SRT and file names), sound, audio duration, make_queue_item, normalize_queue_path
├── installer.py         — dependency installation and updates (pip), system check
├── gpu_info.py          — NVIDIA GPU detection, GPU model saved in settings.json
├── model_updates.py     — Whisper weight checks and updates on Hugging Face Hub
├── app_updates.py       — application self-update checks and updates from GitHub
├── lang.json            — UI texts in three languages
├── settings.json        — saved settings (language, directories, device, GPU model, whisper_model, etc.)
├── request_queue.json   — saved file queue (path, start/end, processed flag)
├── README.md            — project readme (Ukrainian)
├── README_EN.md / README_UK.md / README_RU.md — Help texts by UI language
├── IMPROVEMENT_PLAN.md  — code improvement plan (DRY, bottlenecks, consistency) for developers
├── favicon.ico          — window and taskbar icon
├── install.bat          — initial dependency installation
├── run_whisper.vbs      — launch without a console window
├── start_delayed.vbs    — delayed launch (for autostart)
├── autorun_delayed.bat  — add the program to startup (creates a shortcut in the Startup folder)
└── __pycache__/         — Python cache (created automatically)
```

Optionally in the **program directory** (where main.py is located): an .mp3 file for the completion sound. All paths (settings, queue, README, autostart shortcut) are based on this directory.

---

## Delayed autostart (Windows)

To have the program start when you log in with a 20–30 second delay:

**Method 1 (recommended):** in the program, click the **[Autostart]** button (in the bottom panel after the "Tray" switch). A script window opens; after it runs, a shortcut appears in the Startup folder.

**Method 2:** run **`autorun_delayed.bat`** from the project folder (double-click or from CMD). The script creates a shortcut in the Startup folder; after the next Windows login, the program starts automatically after 25 seconds. The script is written as a single PowerShell invocation line (no line continuations) so it works correctly on different PCs and with different line endings in the file.

**Method 3 (manual):**
1. Open the **Startup** folder: press `Win+R`, enter `shell:startup`, Enter.
2. Create a **shortcut** to `start_delayed.vbs` from the project folder and move it to the Startup folder.
3. The default delay is **25 seconds**. To change it, edit `start_delayed.vbs`: change `delaySec = 25` to 20 or 30.

Administrator rights are not required: the shortcut is created in the current user's startup folder (`%APPDATA%\...\Startup`).

After logging in to Windows, the program starts automatically after the specified number of seconds (without a CMD window).

---

## "Taskbar" / "Tray" / "Taskbar + Tray" modes and system tray behavior

The bottom panel has a display mode switch: **Taskbar**, **Tray**, **Taskbar + Tray**. It controls where the program appears (taskbar, notification area near the clock, or both) and **what happens when you click the window close button (×)**.

The **pystray** and **Pillow** libraries are required ([Dependencies] or [Updates] buttons). If they are not installed, "Tray" and "Taskbar + Tray" modes are unavailable (a warning appears in the log).

### "Taskbar" mode
- Shown only on the **taskbar**; no system tray icon.
- **Closing the window (×):** a "Close the program?" dialog appears — confirming closes the application.

### "Tray" mode
- The program is shown only as an **icon in the system tray** (notification area near the clock). The window is hidden at launch; double-clicking the icon or choosing **"Show window"** from the menu opens the window.
- **Closing the window (×):** the window **does not close** — it hides to the tray; the program keeps running in the background (the queue and directory watch can continue).
- **Fully quitting the program:** in the tray icon context menu, choose **"Exit"** — the usual close confirmation dialog appears.

### "Taskbar + Tray" mode
- The program is on both the **taskbar** and in the **system tray**.
- **Closing the window (×):** same as "Taskbar" mode — a close dialog appears; confirming closes the application.
- To leave the program running in the background without quitting — minimize the window with the "−" button, do not close with ×.

### Summary
| Mode            | Does × close the window? | How to quit the program fully?        |
|-----------------|--------------------------|---------------------------------------|
| Taskbar         | Yes (via dialog)         | × button → "Yes" in the dialog        |
| Tray            | No (hides to tray)       | Tray icon menu → "Exit" → "Yes"       |
| Taskbar + Tray  | Yes (via dialog)         | × button → "Yes" in the dialog        |

---

## Technical requirements

- **Python:** 3.9 or newer (3.10+ recommended). Python 3.13+ is supported.
- **OS:** Windows 10/11, Linux (X11/Wayland), macOS.
- **GPU:** NVIDIA GPU with CUDA 12.1 support recommended. AMD Radeon and other non-CUDA GPUs work in CPU mode only.
- **FFmpeg:** Required in PATH for media file processing.
- **Python 3.13+:** pyaudioop is installed automatically (replacement for the removed audioop module).

---

## Supported formats

| Type  | Formats                         |
|-------|---------------------------------|
| Audio | .mp3, .wav, .m4a, .flac, .ogg  |
| Video | .mp4, .mkv, .avi, .mov         |

FFmpeg in PATH is required for media decoding.

---

## Queue (table)

The section label is **"Queue"** (or "Черга" / "Очередь" depending on language). Hovering over this label shows a tooltip explaining the table and time segments.

The queue is displayed as a **table** with columns:

| Column          | Description |
|-----------------|-------------|
| **#**           | Sequence number. |
| **File name**   | File name (plus "– processed" if already processed). |
| **Start**       | Processing start time (default 00:00:00,000). |
| **End seg. 1**  | End of the first segment (empty by default). |
| **End seg. 2**  | End of the second segment (empty by default). |
| **End**         | Processing end time (default — file duration). |

- When a file is added, **Start** = 00:00:00,000, **End** = file duration (from ffprobe/pydub).
- **Double-click** a row — time range edit dialog (Start, End seg. 1/2, End).
- **Shift + click** a row — open the source file location in the file manager (on Windows: the folder opens with the file selected).
- **Delete** key and right-click → **Delete** remove the selected file(s) from the queue; multi-select with **Ctrl** or **Shift**.
- The queue is **saved** in `request_queue.json` (add, clear, drag, edit).
- On **startup**, the queue is loaded from `request_queue.json` (files that no longer exist on disk are skipped).

Processing runs only in the **[Start — End]** range for each row. This lets you process one file in parts (for example, 0–20 min in English, 20–60 min in Russian).

---

## Functionality

Converts speech from audio and video to text. Output files are created in the save directory or next to the source file:

1. **.txt** — plain transcription text (each segment on a new line).
2. **.srt** — subtitles: segment numbering, timestamps, text.

Optionally (when "Save extracted audio (MP3)" is enabled):
3. **_audio.mp3** — extracted audio track (one file for the full file; a separate file with a time suffix for a segment).

**Range processing (by segments):** if Start and/or End in the table differ from 0 and the file duration, only that segment is processed. Results are saved to **separate files** with a time suffix, for example:
- `name_00-00-00_00-20-00.txt` / `.srt` — segment 0:00–0:20;
- `name_00-20-00_01-00-00.txt` / `.srt` — segment 0:20–1:00.
When "Save Mp3" is enabled, each segment gets its own `name_..._audio.mp3` for the same range.

---

## Processing modes

- **One file in the queue:** select the file and press [Start] — no dialog is shown.
- **Multiple files:** pressing [Start] shows a dialog: "Selected file only", "All files in queue", or "Cancel".
- **New only:** only files without the "– processed" mark are processed.
- **Entire queue:** all files processed in order.

---

## Device and model settings

- **AUTO** — automatic selection (NVIDIA GPU if available, otherwise CPU).
- **GPU** — force GPU (if CUDA is available). For older cards — safe int8 mode.
- **CPU** — force CPU, int8 (also suitable for AMD Radeon and other non-CUDA GPUs).

**Model selection:** the button showing the current model name (for example, "large-v3-turbo") opens a dialog where you can choose a model from the list (tiny, base, small, medium, large-v1/v2/v3, large-v3-turbo, distil-large-v3), see which are already downloaded and their size, press **"Download model"** to load the selected model into memory immediately (without waiting for transcription to start), or **"Update model"** to download a new weight revision from Hugging Face Hub (the list shows an "update available" mark when a newer version exists on the Hub). The model is loaded once and used for all files (Singleton). Downloaded models are stored in the Hugging Face Hub cache (path shown in the button tooltip and in the dialog).

**Additional options:**
- Play sound when the queue finishes.
- Save extracted audio (MP3) — one `<name>_audio.mp3` for a full file; a separate file with a time suffix for a segment (for example `<name>_00-20-00_01-00-00_audio.mp3`).

---

## Interface languages

- **EN** — English (default), **UK** — Ukrainian, **RU** — Russian.
- Switch (🌐 EN UK RU) at the top of the window. Language is saved in `settings.json`.
- The **[Help]** button opens the help text in the same language as the interface (`README_EN.md` / `README_UK.md` / `README_RU.md`).
- Interface language does not affect speech recognition. Recognition language is set by the "Recognition language" switch (AUTO, RU, UK, EN).

---

## Button reference

| Button              | Action |
|---------------------|--------|
| [Start]             | Start processing (with mode selection) |
| [Cancel]            | Stop the current task |
| [Add files]         | Select one or more files |
| [Add directory]     | Add all supported files from a directory recursively |
| [Clear queue]       | Remove all files from the list |
| [System]            | Check GPU (NVIDIA), PyTorch/CUDA, and FFmpeg; GPU model saved in `settings.json` |
| [Dependencies]      | Install or reinstall libraries (including pystray, Pillow for tray), Force Reinstall option; pip errors go to the log |
| [Updates]           | Check for updates: pip packages (for NVIDIA — torch from CUDA 12.1 / cu121 index), downloaded Whisper models on Hugging Face Hub, the application on GitHub; installs selected items after confirmation |
| Model button        | Whisper model selection dialog: list (downloaded / not downloaded), "Download model", "Update model", "OK", "Cancel" |
| [Clear log]         | Clear the log window |
| [Autostart]         | Runs `autorun_delayed.bat`: adds the program to Windows startup with a 25 s delay (Windows only) |
| [Save directory]    | Output directory (empty field — save next to the source file) |
| [Help]              | This help text |

**Checkboxes:** "Play sound when queue finishes", "Save extracted audio (MP3)".

**Directory watch:** when enabled, the program watches the specified directory. Each **new** supported file:
1. **Is added to the queue** (table) with range 00:00:00 — file duration.
2. **Processing starts automatically** for that file. If another job is already running, the file **is queued** and will be processed **automatically after** the current task finishes (log: "in queue — will process after current task"). Only **one** transcription task runs at a time; the completion sound plays only when the entire watch queue is processed.

Files the program creates in that directory (`.txt`, `.srt`, `*_audio.mp3` export, etc.) are **not added** to the queue again — to avoid a loop when saving MP3 next to the source file is treated as a "new file".

---

## Keyboard shortcuts

- **Enter** — if the queue is empty: opens the "Add files" dialog; if files are present: starts transcription (same as [Start]).
- **Space** — toggles the "Save Mp3" checkbox (in input fields it acts as a normal space).
- **Ctrl+V** — paste a path from the clipboard into the "Directory watch" field.
- **Delete** — remove selected file(s) from the queue (when the queue table has focus).

---

## Useful features

- **Queue table:** "Queue" label; hovering shows a tooltip explaining columns and time segments. Six columns (#, name, start, end seg. 1/2, end); double-click — edit time.
- **Drag & Drop:** drag files into the window; dragging rows in the table changes queue order.
- **Queue persistence:** `request_queue.json` — the queue is saved on changes and loaded on startup.
- **Smart queue:** processed files are marked "– processed".
- **Log links:** click — open file; Shift+click — open folder with file selected in Explorer.
- **Log context menu:** right-click — copy text.
- **Log size:** automatically limited (old lines removed from the top) to avoid excessive memory use during long sessions.
- **Debugging:** if you set the environment variable `DEBUG=1` before launch, the log shows the full traceback on transcription errors.
- **Progress bar** and segment timestamps during processing.
- **VAD filter:** Voice Activity Detection enabled by default.
- **Icon:** window and taskbar use `favicon.ico` from the project folder; for the tray icon, a fallback gray icon is used if the file is missing.
- **Dialogs:** all popup windows (model selection, row time edit, "selected only / all files" choice, help) are centered on the main program window (or screen).
- **Application updates:** the [Updates] button compares the local version from `config.py` with GitHub (branch `main`); if git is available — `git pull`, otherwise ZIP download and restart (Windows).
- **torch updates on NVIDIA:** when an NVIDIA GPU is detected, torch uses the **CUDA 12.1 (cu121)** index, not regular PyPI (so a CPU build is not offered).

---

## What's new in 1.1.1

- **Queue removal:** selected files can be removed with the Delete key or via the context menu (right-click → "Delete"); multi-select with Ctrl/Shift is supported.
- **Help in interface language:** the [Help] button opens `README_EN.md` / `README_UK.md` / `README_RU.md` according to the selected UI language (EN / UK / RU).

---

## What's new in 1.1.0

- **Application self-update** from GitHub ([magnusua/WhisperFastGUI](https://github.com/magnusua/WhisperFastGUI)) via the [Updates] button.
- **Whisper weight updates** (Hugging Face Hub) — in the general update process and via the separate "Update model" button in the model selection dialog.
- **Correct PyTorch updates** on NVIDIA systems: version checks use only the **cu121** index; GPU model saved in `settings.json`.
- **Directory watch queue:** if new files appear during processing, they are processed automatically after the current task finishes.

---

## Installation and setup

### Initial installation

1. **Install Python 3.9+** (3.10+ recommended). During installation, check "Add Python to PATH".  
   https://www.python.org/downloads/

2. **Install FFmpeg** and add it to PATH.  
   https://ffmpeg.org/download.html

3. **Run `install.bat`.** The script:
   - checks Python and already installed packages;
   - updates pip, setuptools, wheel;
   - installs PyTorch (CUDA 12.1), faster-whisper, ctranslate2;
   - skips nvidia-cublas-cu12 and nvidia-cudnn-cu12 on first install (they can be installed from the GUI: [Updates] or [Dependencies]);
   - installs pygame, pydub, tkinterdnd2-universal, pystray, Pillow;
   - checks packages, FFmpeg, and CUDA.

4. **Launch:**
   - `run_whisper.vbs` — double-click (no console window).
   - You can create a shortcut manually: target — `run_whisper.vbs` or `main.py`.  
   For debugging from CMD: `py main.py` (from the project folder).

### Automatic installation

On first launch, the script checks and installs missing dependencies (without NVIDIA components). NVIDIA libraries are installed only via [Updates] or [Dependencies] in the GUI.

### Manual installation

In a command prompt in the project directory, run: `python installer.py`.

---

## Dependencies (table)

All packages required for the program and what each one does:

| Package | Purpose |
|---------|---------|
| **pip** | Install and update Python packages. |
| **setuptools** | Build and install packages. |
| **wheel** | Binary package format for fast installation. |
| **torch** | PyTorch — foundation for running the Whisper model on CPU or GPU. |
| **torchvision** | Additional PyTorch components (required with torch). |
| **torchaudio** | Audio handling in PyTorch. |
| **faster-whisper** | Speech recognition model (Whisper wrapper). |
| **ctranslate2** | Fast inference engine for translation models (used by faster-whisper). |
| **nvidia-cublas-cu12** | NVIDIA CUDA 12 libraries for GPU acceleration (installed from GUI). |
| **nvidia-cudnn-cu12** | cuDNN libraries for NVIDIA GPU (installed from GUI). |
| **pydub** | Audio processing: segment extraction, MP3 export, duration lookup. |
| **pygame** | Completion sound playback (audio notification). |
| **tkinterdnd2-universal** | File drag-and-drop (Drag & Drop) into the window and within the queue table. |
| **pystray** | Program icon in the system tray (notification area). |
| **Pillow** | Load and prepare the tray icon image (favicon.ico). |
| **pyaudioop** | Replacement for the removed `audioop` module in Python 3.13+ (required for pydub). |
| **ffmpeg** | System program (not pip): audio/video decoding, duration checks. Must be installed separately and added to PATH. |

**Notes:**
- **nvidia-cublas-cu12** and **nvidia-cudnn-cu12** are not installed on first run (install.bat or auto-install); install them with [Updates] or [Dependencies] in the program.
- **pyaudioop** is only needed for Python 3.13+; installed automatically when required.
- **tkinter** — part of the standard Python distribution; used for the graphical interface.

---

## Safety and stability

- **Safe shutdown:** on application close, the model is unloaded from memory and the CUDA cache is cleared.
- **Operation cancellation:** instant stop with proper completion of the current segment.
- **Error handling:** logging and proper response to failures. If a "list index out of range" error occurs during processing, the program shows a hint: the file may have **no audio track** or no sound — check the file (for example, open it in a media player).
- **One task at a time:** only one transcription runs at any moment; pressing [Start] again or a new file from directory watch during execution does not start a second task in parallel.
