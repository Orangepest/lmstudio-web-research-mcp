param(
  [string]$InstallDir = "$env:USERPROFILE\mcp-servers\lmstudio-web-research-mcp",
  [string]$RepoUrl = "https://github.com/Orangepest/lmstudio-web-research-mcp.git",
  [string]$ZipUrl = "https://github.com/Orangepest/lmstudio-web-research-mcp/archive/refs/heads/main.zip",
  [string]$ConfigPath = "$env:USERPROFILE\.lmstudio\mcp.json",
  [switch]$SkipClone,
  [switch]$SkipPlaywright,
  [switch]$NoRestartReminder
)

$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Require-Command {
  param([string]$Name, [string]$InstallHint)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "$Name was not found. $InstallHint"
  }
}

function Invoke-NativeProbe {
  param(
    [string]$Command,
    [string[]]$Arguments
  )
  $oldPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $output = & $Command @Arguments 2>$null
    return [pscustomobject]@{
      ExitCode = $LASTEXITCODE
      Output = $output
    }
  } catch {
    return [pscustomobject]@{
      ExitCode = 1
      Output = ""
    }
  } finally {
    $ErrorActionPreference = $oldPreference
  }
}

Write-Step "Checking prerequisites"

if (Get-Command py -ErrorAction SilentlyContinue) {
  $probe = Invoke-NativeProbe "py" @("-3.12", "--version")
  if ($probe.ExitCode -eq 0) {
    $PythonCommand = "py"
    $PythonArgs = @("-3.12")
    $pythonVersion = $probe.Output
  }
}
if (-not $PythonCommand -and (Get-Command python -ErrorAction SilentlyContinue)) {
  $probe = Invoke-NativeProbe "python" @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
  if ($probe.ExitCode -eq 0) {
    $versionText = [string]$probe.Output
    $parts = $versionText.Trim().Split(".")
    if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 12)) {
      $PythonCommand = "python"
      $PythonArgs = @()
      $pythonVersion = (Invoke-NativeProbe "python" @("--version")).Output
    }
  }
}
if (-not $PythonCommand) {
  throw "Python 3.12+ was not found. Install it from https://www.python.org/downloads/windows/, then run this script again."
}
Write-Host "Using $pythonVersion"

if (-not $SkipClone) {
  $git = Get-Command git -ErrorAction SilentlyContinue
  if ($git -and (Test-Path (Join-Path $InstallDir ".git"))) {
    Write-Step "Updating existing checkout"
    git -C $InstallDir pull --ff-only
  } elseif ($git -and -not (Test-Path $InstallDir)) {
    Write-Step "Cloning research MCP"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $InstallDir) | Out-Null
    git clone $RepoUrl $InstallDir
  } elseif (-not (Test-Path $InstallDir)) {
    Write-Step "Downloading research MCP ZIP"
    $parent = Split-Path -Parent $InstallDir
    $tempZip = Join-Path $env:TEMP "lmstudio-web-research-mcp.zip"
    $tempExtract = Join-Path $env:TEMP "lmstudio-web-research-mcp-install"
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    if (Test-Path $tempZip) { Remove-Item -Force $tempZip }
    if (Test-Path $tempExtract) { Remove-Item -Recurse -Force $tempExtract }
    Invoke-WebRequest -Uri $ZipUrl -OutFile $tempZip
    Expand-Archive -Path $tempZip -DestinationPath $tempExtract -Force
    $expanded = Get-ChildItem $tempExtract -Directory | Select-Object -First 1
    if (-not $expanded) {
      throw "Could not unpack $ZipUrl"
    }
    Move-Item -Path $expanded.FullName -Destination $InstallDir
  } else {
    Write-Host "Using existing install directory: $InstallDir"
  }
}

Write-Step "Creating Python environment"
Push-Location $InstallDir
try {
  if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $PythonCommand @PythonArgs -m venv .venv
  }
  .\.venv\Scripts\python.exe -m pip install --upgrade pip
  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  if (-not $SkipPlaywright) {
    .\.venv\Scripts\python.exe -m playwright install chromium
  }

  Write-Step "Installing LM Studio MCP config"
  .\.venv\Scripts\python.exe scripts\merge_lmstudio_mcp.py $ConfigPath --research-dir $InstallDir --platform windows --apply | Out-Null
  .\.venv\Scripts\python.exe scripts\validate_lmstudio_mcp.py $ConfigPath --research-dir $InstallDir --platform windows --check-paths | Out-Null

  Write-Step "Smoke-testing MCP server"
  .\.venv\Scripts\python.exe scripts\probe_mcp_server.py --command ".\.venv\Scripts\python.exe" --cwd $InstallDir | Out-Null

  Write-Host ""
  Write-Host "Installed LM Studio Web Research MCP." -ForegroundColor Green
  Write-Host "Config: $ConfigPath"
  Write-Host "Install dir: $InstallDir"
  if (-not $NoRestartReminder) {
    Write-Host ""
    Write-Host "Restart LM Studio now, then open Settings -> Integrations and confirm web-research is listed."
  }
} finally {
  Pop-Location
}
