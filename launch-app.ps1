$ErrorActionPreference = 'Stop'

$AppRoot = $PSScriptRoot
$AppUrl = 'http://127.0.0.1:8765'
$HealthUrl = "$AppUrl/api/status"
$PythonPath = Join-Path $AppRoot '.venv\Scripts\python.exe'
$DataPath = Join-Path $AppRoot 'data'
$StdoutPath = Join-Path $DataPath 'server.stdout.log'
$StderrPath = Join-Path $DataPath 'server.stderr.log'

$ProgramFiles = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)
$ProgramFilesX86 = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFilesX86)
$LocalAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$BrowserPaths = @(
    (Join-Path $ProgramFilesX86 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $LocalAppData 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $LocalAppData 'Google\Chrome\Application\chrome.exe')
)
foreach ($CommandName in @('msedge.exe', 'chrome.exe')) {
    $Command = Get-Command $CommandName -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Command -and $Command.Source) {
        $BrowserPaths += $Command.Source
    }
}
$BrowserPath = $BrowserPaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if (-not $BrowserPath) {
    throw 'Install Microsoft Edge or Google Chrome to open the app in its own window.'
}
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw 'Run .\setup.ps1 before starting the app.'
}

New-Item -ItemType Directory -Path $DataPath -Force | Out-Null

function Test-AppReady {
    try {
        $Status = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
        return ($null -ne $Status.counts -and $null -ne $Status.inventory)
    }
    catch {
        return $false
    }
}

if (-not (Test-AppReady)) {
    $ServerProcess = Start-Process `
        -FilePath $PythonPath `
        -ArgumentList @('-m', 'uvicorn', 'website_hunter.api:app', '--app-dir', 'src', '--host', '127.0.0.1', '--port', '8765', '--log-level', 'warning') `
        -WorkingDirectory $AppRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath `
        -PassThru

    $Ready = $false
    for ($Attempt = 0; $Attempt -lt 45; $Attempt++) {
        if (Test-AppReady) {
            $Ready = $true
            break
        }
        $ServerProcess.Refresh()
        if ($ServerProcess.HasExited) {
            break
        }
        Start-Sleep -Milliseconds 700
    }

    if (-not $Ready) {
        $Details = if (Test-Path -LiteralPath $StderrPath) {
            (Get-Content -LiteralPath $StderrPath -Tail 20) -join [Environment]::NewLine
        }
        else {
            'No server output was recorded.'
        }
        throw "The local server did not become ready. Check $StderrPath. $Details"
    }
}

Start-Process -FilePath $BrowserPath -ArgumentList "--app=$AppUrl"
