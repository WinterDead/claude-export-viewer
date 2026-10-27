# Claude Export Viewer launcher (Windows).
#
# Double-click run.bat to use this; the shim invokes this file with a
# per-invocation execution-policy bypass. This script never changes anything
# outside the project folder: no PATH edits, no policy changes, no installs
# beyond the local .venv.

$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

$MinMajor = 3
$MinMinor = 11
$Ports = @(5000, 5050, 5051, 5052)

function Get-PythonVersion {
    # Run a candidate interpreter and return "X.Y.Z", or $null if it did not
    # run or did not answer with a version (e.g. the Microsoft Store stub).
    param([string]$Exe, [string[]]$ExtraArgs)
    $allArgs = @($ExtraArgs) + @("-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])")
    try {
        $out = (& $Exe @allArgs 2>&1 | Out-String).Trim()
    } catch {
        return $null
    }
    if ($LASTEXITCODE -ne 0) { return $null }
    if ($out -match '^\d+\.\d+\.\d+$') { return $out }
    return $null
}

# --- 1. Find a suitable Python -------------------------------------------

$candidates = @(
    @{ Label = "py -3 (the Windows Python launcher)"; Exe = "py"; Args = @("-3") },
    @{ Label = "python"; Exe = "python"; Args = @() }
)

$python = $null
$pythonVersion = $null
$notes = @()

foreach ($c in $candidates) {
    $cmd = Get-Command $c.Exe -ErrorAction SilentlyContinue
    if (-not $cmd) {
        $notes += "$($c.Label): not found"
        continue
    }
    $version = Get-PythonVersion -Exe $c.Exe -ExtraArgs $c.Args
    if ($null -eq $version) {
        if ("$($cmd.Source)" -match 'WindowsApps') {
            $notes += "$($c.Label): only the Microsoft Store placeholder, not a real Python install"
        } else {
            $notes += "$($c.Label): found at $($cmd.Source) but it did not run"
        }
        continue
    }
    $parts = $version.Split(".")
    if ([int]$parts[0] -gt $MinMajor -or
        ([int]$parts[0] -eq $MinMajor -and [int]$parts[1] -ge $MinMinor)) {
        $python = $c
        $pythonVersion = $version
        break
    }
    $notes += "$($c.Label): Python $version - too old"
}

# The py launcher's default can be pinned to an older Python even when a newer
# one is installed (v1.3.1, D13): before declaring "too old", probe its full
# list and try each installed 3.x that meets the minimum by its versioned
# qualifier (py -3.12, ...), newest first.
if ($null -eq $python -and (Get-Command py -ErrorAction SilentlyContinue)) {
    $listed = @()
    try {
        $listed = (& py -0p 2>$null | Out-String) -split "`n"
    } catch {
        $listed = @()
    }
    $qualifiers = @()
    foreach ($line in $listed) {
        if ($line -match '-(?:V:)?(\d+)\.(\d+)') {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -gt $MinMajor -or ($major -eq $MinMajor -and $minor -ge $MinMinor)) {
                $qualifiers += [pscustomobject]@{ Major = $major; Minor = $minor }
            }
        }
    }
    $qualifiers = $qualifiers | Sort-Object Major, Minor -Descending -Unique
    foreach ($q in $qualifiers) {
        $qualifier = "-$($q.Major).$($q.Minor)"
        $version = Get-PythonVersion -Exe "py" -ExtraArgs @($qualifier)
        if ($null -ne $version) {
            $python = @{ Label = "py $qualifier (the Windows Python launcher)"; Exe = "py"; Args = @($qualifier) }
            $pythonVersion = $version
            break
        }
    }
}

if ($null -eq $python) {
    Write-Host ""
    Write-Host "Could not find Python $MinMajor.$MinMinor or newer on this machine."
    Write-Host ""
    Write-Host "What was checked:"
    foreach ($note in $notes) { Write-Host "  - $note" }
    Write-Host ""
    Write-Host "Next step: install Python 3.11 or newer from"
    Write-Host "  https://www.python.org/downloads/"
    Write-Host "During the install, tick 'Add Python to PATH', then run this script again."
    if ($notes -match 'too old') {
        Write-Host "If you've installed a newer Python and still see this, the py launcher may be pinned to an older version — run `"py -0p`" to list what's installed."
    }
    exit 1
}

Write-Host "Found Python $pythonVersion ($($python.Label))."

# --- 2. Create the virtual environment if absent or incomplete ------------

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

# Trust an existing .venv only if it actually has pip: a venv with a python
# but no pip is a half-created leftover, and the install step below would
# die with "No module named pip".
if (Test-Path $venvPython) {
    & $venvPython -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "The app's virtual environment is incomplete — rebuilding it..."
        Remove-Item -Recurse -Force ".venv"
    }
}

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating the project's virtual environment (.venv)..."
    $venvArgs = @($python.Args) + @("-m", "venv", ".venv")
    & $python.Exe @venvArgs
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        Write-Host "Creating .venv did not work."
        Write-Host "Next step: delete the .venv folder if one was half-created, then run this script again."
        exit 1
    }
}

# Install the app into the venv only when its dependencies are not importable
# yet. Cheap when already installed, and self-heals a half-finished first run.
& $venvPython -c "import flask, flask_wtf, markdown, bleach" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing the app and its dependencies (this can take a minute)..."
    & $venvPython -m pip install -e .
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing dependencies did not work."
        Write-Host "Next step: check your internet connection, then run this script again."
        exit 1
    }
}

# --- 3. Pick a port --------------------------------------------------------

function Test-PortFree([int]$Port) {
    try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        $listener.Stop()
        return $true
    } catch {
        return $false
    }
}

$port = $null
foreach ($candidate in $Ports) {
    if (Test-PortFree $candidate) {
        $port = $candidate
        break
    }
    Write-Host "Port $candidate is busy, trying the next one..."
}

if ($null -eq $port) {
    Write-Host "All of the usual ports ($($Ports -join ', ')) are busy."
    Write-Host "Next step: close whatever is using them, or start the app yourself on a port of your choice:"
    Write-Host "  .venv\Scripts\python.exe -m flask --app app run --host 127.0.0.1 --port 8080"
    exit 1
}

if ($port -ne $Ports[0]) {
    Write-Host "Using port $port instead of the default $($Ports[0])."
}

# --- 4. Launch, and open the browser once the server answers ---------------

$url = "http://127.0.0.1:$port/"

$browserJob = Start-Job -ArgumentList $port -ScriptBlock {
    param($Port)
    # Poll until the server accepts a connection, then open the default
    # browser. Gives up quietly after 30 seconds.
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        $client = New-Object System.Net.Sockets.TcpClient
        $connected = $false
        try {
            $client.Connect("127.0.0.1", $Port)
            $connected = $true
        } catch {
            $connected = $false
        }
        $client.Close()
        if ($connected) {
            Start-Process "http://127.0.0.1:$Port/"
            return
        }
        Start-Sleep -Milliseconds 250
    }
}

Write-Host ""
Write-Host "Starting Claude Export Viewer at $url"
Write-Host "Your browser should open by itself. Press Ctrl+C in this window to stop the app."
Write-Host ""

& $venvPython -m flask --app app run --host 127.0.0.1 --port $port

$flaskExit = $LASTEXITCODE
Stop-Job $browserJob -ErrorAction SilentlyContinue
Remove-Job $browserJob -Force -ErrorAction SilentlyContinue
exit $flaskExit
