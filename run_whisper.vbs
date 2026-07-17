' Запуск Whisper Fast GUI без окна CMD (рабочая папка = папка скрипта)
' Якщо в settings.json є python_path — запускає саме його (pythonw), інакше pyw
Option Explicit
Dim fso, shell, scriptDir, settingsPath, pythonPath, launchCmd, ts, jsonText, re, m
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
settingsPath = scriptDir & "\settings.json"
pythonPath = ""

If fso.FileExists(settingsPath) Then
    Set ts = fso.OpenTextFile(settingsPath, 1, False, 0)
    jsonText = ts.ReadAll
    ts.Close
    Set re = New RegExp
    re.Pattern = """python_path""\s*:\s*""([^""]+)"""
    re.IgnoreCase = True
    Set m = re.Execute(jsonText)
    If m.Count > 0 Then
        pythonPath = Replace(m(0).SubMatches(0), "\\", "\")
    End If
End If

If Len(pythonPath) > 0 And fso.FileExists(pythonPath) Then
    ' python.exe → pythonw.exe у тому ж каталозі
    If LCase(Right(pythonPath, 10)) = "python.exe" Then
        If fso.FileExists(Left(pythonPath, Len(pythonPath) - 10) & "pythonw.exe") Then
            pythonPath = Left(pythonPath, Len(pythonPath) - 10) & "pythonw.exe"
        End If
    End If
    launchCmd = """" & pythonPath & """ """ & scriptDir & "\main.py"""
Else
    launchCmd = "pyw """ & scriptDir & "\main.py"""
End If

shell.Run launchCmd, 0, False
