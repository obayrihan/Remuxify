param([string]$PythonExecutable = 'python.exe')
$ErrorActionPreference = 'Stop'
$forgePython = (Get-Command -Name $PythonExecutable -ErrorAction Stop).Source
New-Item -ItemType Directory -Path "$PSScriptRoot\data" -Force | Out-Null
Start-Process -FilePath $forgePython -ArgumentList "`"$PSScriptRoot\run.py`"" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput "$PSScriptRoot\data\stdout.log" -RedirectStandardError "$PSScriptRoot\data\stderr.log"
