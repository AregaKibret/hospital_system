# Resume Claude Session

Use this workflow to continue work after a session limit:

1. Run the PowerShell wrapper from the workspace root.
2. The wrapper will restart Claude automatically using the continue mode.
3. Keep the terminal open so it can resume work.

Example:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\claude_auto_resume.ps1
```
