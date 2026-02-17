' Запуск Whisper Fast GUI без окна CMD (рабочая папка = папка скрипта)
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("Wscript.Shell").Run "cmd /c cd /d """ & scriptDir & """ && pyw main.py", 0, False
