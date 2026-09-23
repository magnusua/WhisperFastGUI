# Third-party notices — FTW

FTW is free software (MIT). It uses the following components. License texts
are not reproduced in full; follow the links for the complete terms.

FTW does **not** ship FFmpeg or Pandoc in this git repository. Those tools are
optional/runtime installs chosen by the user.

| Component | License | Used for | Link |
|---|---|---|---|
| **FTW** (this project; formerly Whisper Fast GUI) | MIT | Application source | [LICENSE](LICENSE) |
| Python | PSF | Runtime | https://docs.python.org/3/license.html |
| Tcl/Tk (Tkinter) | Tcl/Tk | Desktop GUI | https://www.tcl-lang.org/software/tcltk/license.html |
| faster-whisper | MIT | Local speech-to-text | https://github.com/SYSTRAN/faster-whisper |
| CTranslate2 | MIT | Whisper inference | https://github.com/OpenNMT/CTranslate2 |
| OpenAI Whisper (model / architecture) | MIT | ASR weights via Hugging Face | https://github.com/openai/whisper |
| PyTorch | BSD-3-Clause | Tensors / optional CUDA | https://github.com/pytorch/pytorch |
| NumPy | BSD | Capture mix, arrays | https://numpy.org/doc/stable/license.html |
| pydub | MIT | Audio cut, stereo speakers, MP3 | https://github.com/jiaaro/pydub |
| sounddevice | MIT | Microphone + WASAPI loopback | https://github.com/spatialaudio/python-sounddevice |
| PortAudio | MIT | Audio I/O backend for sounddevice | https://github.com/PortAudio/portaudio |
| pygame | LGPL | Queue-finished sound | https://www.pygame.org/wiki/about |
| Pillow | HPND-derived | Tray icon | https://github.com/python-pillow/Pillow/blob/main/LICENSE |
| Material Design Icons (toolbar PNGs in `resources/icons/`) | Apache-2.0 | Toolbar pictograms | https://github.com/google/material-design-icons |
| pystray | LGPL | System tray | https://github.com/moses-palmer/pystray |
| tkinterdnd2-universal | MIT | Drag-and-drop onto the queue | https://pypi.org/project/tkinterdnd2-universal/ |
| markitdown | MIT | PDF / Office → Markdown | https://github.com/microsoft/markitdown |
| packaging | Apache-2.0 / BSD | Version comparison | https://github.com/pypa/packaging |
| audioop-lts | PSF | `audioop` on Python 3.13+ (pydub) | https://pypi.org/project/audioop-lts/ |
| cursor-sdk | Cursor terms | Optional Cursor AI post-process | https://pypi.org/project/cursor-sdk/ |
| FFmpeg (user-installed) | LGPL 2.1 or GPL 2+ | Decode audio/video | https://ffmpeg.org/legal.html |
| Pandoc (optional, user-installed) | GPL-2.0+ | Markdown → Word | https://github.com/jgm/pandoc |
| pyannote.audio (optional, not in requirements) | MIT + model terms | Mono diarization | https://github.com/pyannote/pyannote-audio |

Cloud AI providers (Gemini, Claude, Copilot, Cursor, hosted OpenAI-compatible
APIs) are **optional**. FTW itself has no account and no paid features; those
services are billed by their vendors if you choose to use them.

Local transcription (faster-whisper) and local summaries (Ollama on localhost)
do not send audio or transcripts off the machine.
