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

    # --- terminal animation (plain ASCII so every Windows console font can draw it) ---
    $fancy = -not [Console]::IsOutputRedirected
    function Show-Line($text) { Write-Host -NoNewline ("`r" + $text.PadRight([Math]::Max(0, [Console]::BufferWidth - 1))) }
    function Get-Bounce($i) {  # [   ===      ] moving back and forth
        $w = 14; $span = $w - 3; $pos = $i % (2 * $span); if ($pos -gt $span) { $pos = 2 * $span - $pos }
        '[' + (' ' * $pos) + '===' + (' ' * ($span - $pos)) + ']'
    }
    function Get-Bar($frac) { $w = 24; $n = [int]([Math]::Floor($frac * $w)); '[' + ('#' * $n) + ('-' * ($w - $n)) + ']' }
    function Show-Done($label) { if ($fancy) { Show-Line "  [OK] $label"; Write-Host '' } else { Write-Host "  [OK] $label" } }

    # Runs a program quietly with an animated line; returns its exit code.
    function Invoke-Quiet($label, $exe, [string[]]$argv) {
        $q = $argv | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }
        $log = Join-Path $env:TEMP 'nexus-install'
        try {
            $p = Start-Process -FilePath $exe -ArgumentList ($q -join ' ') -NoNewWindow -PassThru `
                -RedirectStandardOutput "$log.out.txt" -RedirectStandardError "$log.err.txt"
        } catch { return 1 }
        $null = $p.Handle  # PowerShell 5.1 loses ExitCode unless the handle is opened early
        $sw = [Diagnostics.Stopwatch]::StartNew(); $i = 0
        if (-not $fancy) { Write-Host "  $label..." }
        while (-not $p.HasExited) {
            if ($fancy) { Show-Line ("  {0} {1}  {2}s" -f (Get-Bounce $i), $label, [int]$sw.Elapsed.TotalSeconds) }
            $i++; Start-Sleep -Milliseconds 100
        }
        $p.WaitForExit()
        if ($p.ExitCode -eq 0) { Show-Done $label } elseif ($fancy) { Show-Line ''; Write-Host -NoNewline "`r" }
        return $p.ExitCode
    }

    # Downloads a file with a real progress bar: [#####-----]  45%  9.1 / 20.3 MB  3.2 MB/s
    function Get-File($label, $url, $dest) {
        $resp = [Net.WebRequest]::Create($url).GetResponse()
        try {
            $total = $resp.ContentLength; $in = $resp.GetResponseStream(); $out = [IO.File]::Create($dest)
            try {
                $buf = New-Object byte[] 65536; $done = 0; $i = 0; $last = -1000
                $sw = [Diagnostics.Stopwatch]::StartNew()
                if (-not $fancy) { Write-Host "  $label..." }
                while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
                    $out.Write($buf, 0, $n); $done += $n
                    if ($fancy -and $sw.ElapsedMilliseconds - $last -ge 100) {
                        $last = $sw.ElapsedMilliseconds; $i++
                        $mb = $done / 1MB; $speed = $mb / [Math]::Max(0.1, $sw.Elapsed.TotalSeconds)
                        if ($total -gt 0) {
                            Show-Line ("  {0} {1,3}%  {2}  {3:N1} / {4:N1} MB  {5:N1} MB/s" -f (Get-Bar ($done / $total)), [int](100 * $done / $total), $label, $mb, ($total / 1MB), $speed)
                        } else {
                            Show-Line ("  {0} {1}  {2:N1} MB  {3:N1} MB/s" -f (Get-Bounce $i), $label, $mb, $speed)
                        }
                    }
                }
            } finally { $out.Close(); $in.Close() }
        } finally { $resp.Close() }
        Show-Done $label
    }

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
        return
    }

    # 1. A private Python environment: the Python already on this PC, or one uv downloads.
    if (Test-Path $installDir) { Remove-Item -Recurse -Force $installDir }
    New-Item -ItemType Directory -Force $root | Out-Null
    $venvPy = "$installDir\Scripts\python.exe"
    $py = @(Find-Python)  # @() keeps a one-item result an array (PowerShell unrolls it otherwise)
    if ($py.Count -gt 0) {
        $pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
        Write-Host "  using $(& $pyExe @pyArgs --version 2>&1)" -ForegroundColor DarkGray
        try { & $pyExe @pyArgs -m venv $installDir 2>&1 | Out-Null } catch { }
        if (-not (Test-Path $venvPy)) { if (Test-Path $installDir) { Remove-Item -Recurse -Force $installDir } }
    }
    if (-not (Test-Path $venvPy)) {
        Write-Host '  no ready-to-use Python here, downloading a private copy (one time, ~40 MB)' -ForegroundColor DarkGray
        $uvDir = Join-Path $root 'uv'
        $uv = Join-Path $uvDir 'uv.exe'
        if (-not (Test-Path $uv)) {
            $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
            $zip = Join-Path $env:TEMP 'nexus-uv.zip'
            try {
                $ProgressPreference = 'SilentlyContinue'
                Get-File 'downloading Python installer' "https://github.com/astral-sh/uv/releases/latest/download/uv-$arch-pc-windows-msvc.zip" $zip
                Expand-Archive -Force $zip $uvDir
            } catch { if ($fancy) { Write-Host '' } }
            Remove-Item -Force $zip -ErrorAction SilentlyContinue
        }
        if (-not (Test-Path $uv)) {
            Write-Host "Couldn't download Python. Check your internet connection and try again." -ForegroundColor Red; return
        }
        $null = Invoke-Quiet 'downloading Python 3.12' $uv @('venv', '--quiet', '--seed', '--python', '3.12', $installDir)
        if (-not (Test-Path $venvPy)) {
            Write-Host "Couldn't create a Python environment. Check your internet connection and try again." -ForegroundColor Red; return
        }
    }

    $rc = Invoke-Quiet 'downloading NEXUS CLI' $venvPy @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '--upgrade', $wheel)
    if ($rc -ne 0) { Write-Host 'Download/install failed. Check your internet connection and try again.' -ForegroundColor Red; return }

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
