\
' WS Skim - Desktop shortcut entry (keeps custom .ico; no visible cmd window).
Option Explicit
Dim sh, fso, here, root, pyw, app, msg
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetParentFolderName(fso.GetParentFolderName(here))
pyw = root & "\.venv\Scripts\pythonw.exe"
app = root & "\map144.py"
If Not fso.FileExists(pyw) Then
  msg = "WS Skim is not installed yet." & vbCrLf & vbCrLf & _
        "Open the scripts\windows folder and double-click Install-WsSkim.cmd." & vbCrLf & _
        "Click Yes if Windows asks for permission. Then double-click WS Skim again."
  sh.Popup msg, 0, "WS Skim", 48
  WScript.Quit 1
End If
' 0 = hide any window; pythonw itself has no console.
sh.CurrentDirectory = root
sh.Run """" & pyw & """ """ & app & """", 0, False
