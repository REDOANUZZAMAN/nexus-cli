# NEXUS CLI installer for Windows (PowerShell 5.1+).
#   irm https://raw.githubusercontent.com/REDOANUZZAMAN/nexus-cli/main/install.ps1 | iex
# One command does everything: finds Python (or downloads a private copy with uv if there is
# none / it is unusable), installs into %USERPROFILE%\.nexus\cli, adds %USERPROFILE%\.nexus\bin
# to your PATH, then runs `nexus setup` the first time.
# Options (env vars): NEXUS_VERSION, NEXUS_WHEEL (local file or URL), NEXUS_NO_SETUP=1.

& {
    $ErrorActionPreference = 'Stop'
    # Older Windows 10 PowerShell defaults to TLS 1.0, which GitHub refuses.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
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

    # 1. A private Python environment: the Python already on this PC, or one uv downloads.
    if (Test-Path $installDir) { Remove-Item -Recurse -Force $installDir }
    New-Item -ItemType Directory -Force $root | Out-Null
    $venvPy = "$installDir\Scripts\python.exe"
    $py = Find-Python
    if ($py) {
        $pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
        Write-Host "  using $(& $pyExe @pyArgs --version 2>&1)" -ForegroundColor DarkGray
        try { & $pyExe @pyArgs -m venv $installDir 2>&1 | Out-Null } catch { }
        if (-not (Test-Path $venvPy)) { if (Test-Path $installDir) { Remove-Item -Recurse -Force $installDir } }
    }
    if (-not (Test-Path $venvPy)) {
        Write-Host '  no ready-to-use Python here, downloading a private copy (one time, ~40 MB)...' -ForegroundColor DarkGray
        $uvDir = Join-Path $root 'uv'
        $uv = Join-Path $uvDir 'uv.exe'
        if (-not (Test-Path $uv)) {
            $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
            $zip = Join-Path $env:TEMP 'nexus-uv.zip'
            try {
                $ProgressPreference = 'SilentlyContinue'
                Invoke-WebRequest -UseBasicParsing "https://github.com/astral-sh/uv/releases/latest/download/uv-$arch-pc-windows-msvc.zip" -OutFile $zip
                Expand-Archive -Force $zip $uvDir
            } catch { }
            Remove-Item -Force $zip -ErrorAction SilentlyContinue
        }
        if (-not (Test-Path $uv)) {
            Write-Host "Couldn't download Python. Check your internet connection and try again." -ForegroundColor Red; return
        }
        try { & $uv venv --quiet --seed --python 3.12 $installDir 2>&1 | Out-Null } catch { }
        if (-not (Test-Path $venvPy)) {
            Write-Host "Couldn't create a Python environment. Check your internet connection and try again." -ForegroundColor Red; return
        }
    }

    Write-Host '  downloading...' -ForegroundColor DarkGray
    & $venvPy -m pip install --quiet --disable-pip-version-check --upgrade $wheel
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

    # 3. First run: connect to the server right away.
    if (-not $env:NEXUS_NO_SETUP -and -not (Test-Path (Join-Path $root 'config.json'))) {
        Write-Host ''
        & "$installDir\Scripts\nexus.exe" setup
    }
    Write-Host ''
    Write-Host 'Start chatting:  ' -NoNewline; Write-Host 'nexus' -ForegroundColor White
    Write-Host '  Change server later with: nexus setup   (other open windows need restarting to find "nexus")' -ForegroundColor DarkGray
}
