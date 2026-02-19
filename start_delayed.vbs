' Запуск Whisper Fast GUI з затримкою (25 сек) — для автозавантаження
' Рекомендовано: створіть ярлик на цей файл і покладіть його в папку "Автозавантаження"

Option Explicit
Dim fso, shell, scriptDir, delaySec
delaySec = 25  ' Затримка в секундах (можна змінити на 20–30)

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

WScript.Sleep delaySec * 1000
' Запуск без вікна CMD (як run_whisper.vbs)
shell.Run "cmd /c cd /d """ & scriptDir & """ && pyw main.py", 0, False
