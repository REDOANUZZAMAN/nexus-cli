# NEXUS CLI installer for Windows (PowerShell 5.1+).
#   irm https://raw.githubusercontent.com/REDOANUZZAMAN/nexus-cli/main/install.ps1 | iex
# Installs into %USERPROFILE%\.nexus\cli (its own Python environment) and adds
# %USERPROFILE%\.nexus\bin to your PATH. Installs Python with winget if it's missing.
# Options (env vars): NEXUS_VERSION, NEXUS_WHEEL (local file or URL).

& {
    $ErrorActionPreference = 'Stop'
    $version = if ($env:NEXUS_VERSION) { $env:NEXUS_VERSION } else { '0.2.0' }
    $repo = 'REDOANUZZAMAN/nexus-cli'
    $wheel = if ($env:NEXUS_WHEEL) { $env:NEXUS_WHEEL } else {
        "https://github.com/$repo/releases/download/v$version/nexus_cli-$version-py3-none-any.whl" }
    $root = Join-Path $env:USERPROFILE '.nexus'
    $installDir = Join-Path $root 'cli'
    $binDir = Join-Path $root 'bin'

    Write-Host "* Installing NEXUS CLI v$version" -ForegroundColor Magenta

    function Test-Python($exe, $extra) {
        try {
            & $exe @extra -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>$null | Out-Null
            return ($LASTEXITCODE -eq 0)
        } catch { return $false }
    }

    function Find-Python {
        # `py` launcher first; skip the Microsoft Store "python" stub
        if ((Get-Command py -ErrorAction SilentlyContinue) -and (Test-Python 'py' @('-3'))) { return @('py', '-3') }
        foreach ($name in 'python', 'python3') {
            $cmd = Get-Command $name -ErrorAction SilentlyContinue
            if ($cmd -and $cmd.Source -notlike '*WindowsApps*' -and (Test-Python $cmd.Source @())) { return @($cmd.Source) }
        }
        $local = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
        if ($local -and (Test-Python $local.FullName @())) { return @($local.FullName) }
        return $null
    }

    $py = Find-Python
    if (-not $py) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Host '  Python not found, installing it with winget...' -ForegroundColor DarkGray
            winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
            $py = Find-Python
        }
        if (-not $py) {
            Write-Host 'NEXUS CLI needs Python 3.9 or newer.' -ForegroundColor Red
            Write-Host 'Install it from https://www.python.org/downloads/ (tick "Add Python to PATH"), then run this again.'
            return
        }
    }
    $pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
    Write-Host "  using $(& $pyExe @pyArgs --version 2>&1)" -ForegroundColor DarkGray

    # 1. A private Python environment
    if (Test-Path $installDir) { Remove-Item -Recurse -Force $installDir }
    New-Item -ItemType Directory -Force $root | Out-Null
    & $pyExe @pyArgs -m venv $installDir
    if ($LASTEXITCODE -ne 0) { Write-Host "Couldn't create a Python environment." -ForegroundColor Red; return }

    Write-Host '  downloading...' -ForegroundColor DarkGray
    & "$installDir\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check --upgrade $wheel
    if ($LASTEXITCODE -ne 0) { Write-Host 'Download/install failed. Check your internet connection and try again.' -ForegroundColor Red; return }

    # 2. A small `nexus` launcher in .nexus\bin, and that folder on the user PATH
    New-Item -ItemType Directory -Force $binDir | Out-Null
    Set-Content -Encoding ASCII (Join-Path $binDir 'nexus.cmd') "@`"$installDir\Scripts\nexus.exe`" %*"

    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not $userPath) { $userPath = '' }
    if (($userPath -split ';') -notcontains $binDir) {
        [Environment]::SetEnvironmentVariable('Path', (($userPath.TrimEnd(';') + ";$binDir").TrimStart(';')), 'User')
    }
    if (($env:Path -split ';') -notcontains $binDir) { $env:Path = "$binDir;$env:Path" }

    Write-Host "OK - installed $(& "$installDir\Scripts\nexus.exe" --version)" -ForegroundColor Green
    Write-Host ''
    Write-Host 'Next:'
    Write-Host '  nexus setup   ' -NoNewline; Write-Host 'connect to your NEXUS server (address + API key from the app)' -ForegroundColor DarkGray
    Write-Host '  nexus         ' -NoNewline; Write-Host 'start chatting' -ForegroundColor DarkGray
    Write-Host '  (other open terminals need restarting to find "nexus")' -ForegroundColor DarkGray
}
