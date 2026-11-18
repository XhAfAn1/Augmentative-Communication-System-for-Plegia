# =============================================================================
#  One-time setup of the AAC system on a new PC (run via setup_new_pc.bat).
#    setup_new_pc.bat          check everything and install what is missing (asks before big installs)
#    setup_new_pc.bat check    only report what is missing / what would be done; changes NOTHING
#  Safe to run again: steps that are already done are skipped.
#  Log: logs\setup_<date>.log
# =============================================================================
param([string]$Mode = "install")
$ErrorActionPreference = "Continue"
$Check = ($Mode -eq "check")
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Tracker = Join-Path $Root "Binocular Eye Tracking Module"
$VenvPy = Join-Path $Backend "venv\Scripts\python.exe"
$LlamaVersion = "0.3.28"
$Problems = New-Object System.Collections.ArrayList

New-Item -ItemType Directory -Force (Join-Path $Root "logs") | Out-Null
try { Start-Transcript -Path (Join-Path $Root ("logs\setup_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")) | Out-Null } catch {}

function Step($t) { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  [OK]   $t" -ForegroundColor Green }
function Todo($t) { Write-Host "  [TODO] $t" -ForegroundColor Yellow }
function Bad($t)  { Write-Host "  [!]    $t" -ForegroundColor Red; [void]$Problems.Add($t) }
function Info($t) { Write-Host "         $t" }

function Ask($q) {
    if ($Check) { Info "(check only - would ask: $q)"; return $false }
    $a = Read-Host "  $q [Y/n]"
    return ($a -eq "" -or $a -match "^[yY]")
}

# runs a program; in check mode only prints it. Returns $true on exit code 0.
function Run($exe, [string[]]$argv) {
    if ($Check) { Info "(check only - would run: $exe $($argv -join ' '))"; return $true }
    Info "> $exe $($argv -join ' ')"
    & $exe @argv
    return ($LASTEXITCODE -eq 0)
}

# true if the program runs and exits with 0 (output hidden)
function Works($exe, [string[]]$argv) {
    try { & $exe @argv *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}

function Find-Python313 {
    $c = @()
    try { $p = & py -3.13 -c "import sys;print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0 -and $p) { $c += "$p".Trim() } } catch {}
    foreach ($x in @("$env:ProgramFiles\Python313\python.exe", "C:\Python313\python.exe",
                     "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe")) { if (Test-Path $x) { $c += $x } }
    try {
        $w = (Get-Command python -ErrorAction Stop).Source
        $v = & $w -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
        if ("$v".Trim() -eq "3.13") { $c += $w }
    } catch {}
    return ($c | Select-Object -First 1)
}

function Find-VS([string]$range) {
    # installation path of a Visual Studio with the C++ tools; $range e.g. "[17.0,18.0)" = VS 2022
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) { return $null }
    $a = @("-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath")
    if ($range) { $a += @("-version", $range) }
    $p = & $vswhere @a 2>$null | Select-Object -First 1
    if ($p) { return "$p".Trim() } else { return $null }
}

function Install-VSBuildTools {
    if (-not (Ask "Install Visual Studio 2022 Build Tools with C++ (free, several GB, 10-30 min)?")) { return $false }
    return (Run "winget" @("install", "--id", "Microsoft.VisualStudio.2022.BuildTools", "--accept-package-agreements",
                           "--accept-source-agreements", "--override",
                           "--passive --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"))
}

# builds llama-cpp-python from source inside the VS 2022 C++ environment (CPU or CUDA)
function Build-Llama([string]$vsPath, [bool]$cuda) {
    $bat = Join-Path $env:TEMP "aac_build_llama.bat"
    $lines = @("@echo off", "call `"$vsPath\VC\Auxiliary\Build\vcvars64.bat`" >nul", "set CMAKE_GENERATOR=Ninja", "set FORCE_CMAKE=1")
    if ($cuda) { $lines += "set `"CMAKE_ARGS=-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=native`"" }
    $lines += "`"$VenvPy`" -m pip install `"llama-cpp-python==$LlamaVersion`" --force-reinstall --no-deps --no-cache-dir --no-binary llama-cpp-python"
    $lines += "exit /b %ERRORLEVEL%"
    if ($Check) { Info "(check only - would build llama-cpp-python $LlamaVersion from source, CUDA=$cuda, with $vsPath)"; return $true }
    Set-Content -Path $bat -Value $lines -Encoding ASCII
    Info "Building llama-cpp-python (CUDA=$cuda). This takes a while (GPU build: 20-40 min)..."
    & cmd /c "`"$bat`""
    return ($LASTEXITCODE -eq 0)
}

Write-Host "AAC system - setup for this PC   (mode: $Mode)" -ForegroundColor Cyan
Write-Host "Folder: $Root"
if ($Check) { Write-Host "CHECK ONLY: nothing will be installed or changed." -ForegroundColor Yellow }

# -----------------------------------------------------------------------------
Step "1. Files that must be copied with the folder"
foreach ($f in @("backend\qwen2.5-0.5b-instruct-q8_0.gguf", "backend\Llama-3.2-3B-Instruct-Q4_K_M.gguf", "backend\vocab.json",
                 "backend\trigrams.json", "backend\main.py", "backend\requirements-lock.txt",
                 "Binocular Eye Tracking Module\tracker_service.py", "frontend\pubspec.yaml")) {
    $p = Join-Path $Root $f
    if ((Test-Path $p) -and ((Get-Item $p).Length -gt 0)) { Ok $f } else { Bad "missing: $f (copy the whole Combined System folder again)" }
}

# -----------------------------------------------------------------------------
Step "2. Python 3.13 (eye tracker + AI backend)"
$Py = Find-Python313
if ($Py) { Ok "Python 3.13: $Py" }
else {
    Todo "Python 3.13 not found"
    if (Ask "Install Python 3.13 with winget (for all users, added to PATH)?") {
        [void](Run "winget" @("install", "-e", "--id", "Python.Python.3.13", "--scope", "machine", "--accept-package-agreements",
                              "--accept-source-agreements", "--override", "/quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1"))
        $Py = Find-Python313
    }
    if ($Py) { Ok "Python 3.13: $Py" } elseif (-not $Check) { Bad "Python 3.13 is needed: https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this setup again" }
}

# -----------------------------------------------------------------------------
Step "3. Eye tracker packages (numpy, opencv-python, Pillow, matplotlib)"
if ($Py) {
    if (Works $Py @("-c", "import numpy, cv2, PIL, matplotlib")) { Ok "installed" }
    else {
        Todo "missing packages"
        if (-not (Run $Py @("-m", "pip", "install", "-r", (Join-Path $Tracker "requirements.txt")))) { Bad "installing the eye tracker packages failed (see above)" }
        elseif (-not $Check) { Ok "installed" }
    }
}

# -----------------------------------------------------------------------------
Step "4. AI backend environment (backend\venv)"
if ($Py) {
    $venvWorks = (Test-Path $VenvPy) -and (Works $VenvPy @("-c", "import sys"))
    if (-not $venvWorks) {
        if (Test-Path (Join-Path $Backend "venv")) {
            $old = "venv_from_other_pc_" + (Get-Date -Format "yyyyMMdd_HHmmss")
            Todo "backend\venv was copied from another PC and does not work here: it is renamed to backend\$old (you can delete it)"
            if (-not $Check) { Rename-Item (Join-Path $Backend "venv") $old }
        } else { Todo "backend\venv does not exist yet" }
        if (Run $Py @("-m", "venv", (Join-Path $Backend "venv"))) { if (-not $Check) { Ok "created" } }
        else { Bad "creating backend\venv failed" }
    } else { Ok "backend\venv works" }
    if ($Check -or (Test-Path $VenvPy)) {
        Info "Installing / checking the exact package versions (requirements-lock.txt; first time: several GB, e.g. PyTorch)..."
        $ok = Run $VenvPy @("-m", "pip", "install", "--upgrade", "pip")
        $ok = (Run $VenvPy @("-m", "pip", "install", "-r", (Join-Path $Backend "requirements-lock.txt"))) -and $ok
        if (-not $ok) { Bad "installing the backend packages failed (see above)" } elseif (-not $Check) { Ok "backend packages installed" }
    }
}

# -----------------------------------------------------------------------------
Step "5. Prediction engine (llama-cpp-python ${LlamaVersion}, GPU or CPU)"
if ($Py -and ((Test-Path $VenvPy) -or $Check)) {
    $have = $null
    if (Test-Path $VenvPy) {
        try { $have = & $VenvPy -c "import llama_cpp;print(llama_cpp.__version__, 'GPU' if llama_cpp.llama_supports_gpu_offload() else 'CPU')" 2>$null } catch {}
        if ($LASTEXITCODE -ne 0) { $have = $null }
    }
    $gpuName = $null
    try { $gpuName = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1) } catch {}
    $nvcc = $null
    if ($env:CUDA_PATH -and (Test-Path (Join-Path $env:CUDA_PATH "bin\nvcc.exe"))) { $nvcc = Join-Path $env:CUDA_PATH "bin\nvcc.exe" }
    $vs2022 = Find-VS "[17.0,18.0)"
    $vsAny = Find-VS $null
    Info ("NVIDIA GPU: " + $(if ($gpuName) { $gpuName } else { "none" }) + " | CUDA Toolkit: " + $(if ($nvcc) { $nvcc } else { "not installed" }) +
          " | VS 2022 C++: " + $(if ($vs2022) { "yes" } else { "no" }))
    if ($have) {
        Ok "installed and working: $have"
        if ("$have" -match "CPU" -and $gpuName) { Info "Running on the CPU. For the GPU version: install the CUDA Toolkit + VS 2022 Build Tools, delete nothing, run this setup again and answer Y to the GPU build (or ask Claude)." }
    } else {
        Todo "llama-cpp-python is not installed (or does not load on this PC)"
        $done = $false
        if ($gpuName -and $nvcc -and $vs2022) {
            if (Ask "Build the GPU version for '$gpuName' (much faster sentence suggestions, build takes 20-40 min)?") { $done = Build-Llama $vs2022 $true }
        } elseif ($gpuName) {
            Info "An NVIDIA GPU is present. For the GPU version this PC needs the CUDA Toolkit (https://developer.nvidia.com/cuda-downloads)"
            Info "and Visual Studio 2022 Build Tools (CUDA 13.0 does not accept VS 2026). Installing the CPU version now; it works everywhere."
        }
        if (-not $done) {
            Info "Trying a ready-made CPU version first..."
            $done = Run $VenvPy @("-m", "pip", "install", "llama-cpp-python==$LlamaVersion", "--only-binary=llama-cpp-python",
                                  "--extra-index-url", "https://abetlen.github.io/llama-cpp-python/whl/cpu")
            if (-not $done) {
                Info "No ready-made version for this Python: building the CPU version (needs Visual Studio C++ tools)."
                if (-not $vsAny) { if (Install-VSBuildTools) { $vsAny = Find-VS $null } }
                if ($vsAny) { $done = Build-Llama $vsAny $false }
            }
        }
        if ($Check) { }
        elseif ($done -and (Works $VenvPy @("-c", "import llama_cpp"))) { Ok ("installed: " + (& $VenvPy -c "import llama_cpp;print(llama_cpp.__version__, 'GPU' if llama_cpp.llama_supports_gpu_offload() else 'CPU')")) }
        else { Bad "llama-cpp-python could not be installed: word/sentence predictions will not work (speech still works)" }
    }
}

# -----------------------------------------------------------------------------
Step "6. system_config.json (which Python the app starts the eye tracker with)"
if ($Py) {
    $cfgPath = Join-Path $Root "system_config.json"
    $cfg = $null
    try { $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json } catch {}
    if (-not $cfg) { $cfg = New-Object PSObject }
    $cur = $cfg.tracker_python
    if ($cur -eq $Py) { Ok "tracker_python = $Py" }
    else {
        Todo "tracker_python: '$cur' -> '$Py'"
        if (-not $Check) {
            $cfg | Add-Member -NotePropertyName tracker_python -NotePropertyValue $Py -Force
            if (-not $cfg.backend_python) { $cfg | Add-Member -NotePropertyName backend_python -NotePropertyValue "backend/venv/Scripts/python.exe" -Force }
            ($cfg | ConvertTo-Json) | Set-Content -Path $cfgPath -Encoding UTF8
            Ok "saved"
        }
    }
}

# -----------------------------------------------------------------------------
Step "7. The AAC app (Flutter, Windows)"
$exe = Join-Path $Root "frontend\build\windows\x64\runner\Release\synaptalk.exe"
$front = Join-Path $Root "frontend"
$needBuild = $true
if (Test-Path $exe) {
    $t = (Get-Item $exe).LastWriteTime
    $n = (Get-ChildItem -LiteralPath (Join-Path $front "lib"), (Join-Path $front "pubspec.yaml") -Recurse -File |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
    $needBuild = ($n -gt $t)
}
$vcrt = @("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll") | Where-Object { -not (Test-Path (Join-Path $env:WINDIR "System32\$_")) }
if ($vcrt) {
    Todo "Microsoft Visual C++ runtime missing ($($vcrt -join ', ')) - the app needs it"
    if (Ask "Install the Microsoft Visual C++ runtime with winget?") {
        [void](Run "winget" @("install", "-e", "--id", "Microsoft.VCRedist.2015+.x64", "--accept-package-agreements", "--accept-source-agreements"))
    }
} else { Ok "Visual C++ runtime present" }
if (-not $needBuild) { Ok "app already built and up to date: start_aac.bat starts it without building" }
else {
    Todo "the app has to be built once (start_aac.bat does it automatically)"
    if (Get-Command flutter -ErrorAction SilentlyContinue) { Ok "Flutter found" }
    else { Bad "Flutter SDK not found: install it (https://docs.flutter.dev/get-started/install/windows/desktop), add flutter\bin to PATH, run this setup again" }
    if (-not (Find-VS $null)) {
        Todo "building the app needs Visual Studio C++ tools"
        [void](Install-VSBuildTools)
    } else { Ok "Visual Studio C++ tools found" }
}

# -----------------------------------------------------------------------------
Step "Summary"
if ($Check) { Write-Host "  Check only - nothing was changed. Run setup_new_pc.bat without 'check' to install." -ForegroundColor Yellow }
if ($Problems.Count -eq 0) { Write-Host "  Everything needed is in place." -ForegroundColor Green }
else { Write-Host "  Still to fix:" -ForegroundColor Red; foreach ($p in $Problems) { Write-Host "   - $p" -ForegroundColor Red } }
Write-Host ""
Write-Host "  On a new PC, after the first start:"
Write-Host "   1. Eye Tracking > Sensors: check which camera is the left / right eye (numbers can differ on a new PC)."
Write-Host "   2. Eye Tracking > Calibration: run a Full Calibration (new screen = new calibration)."
Write-Host "   3. Settings > Prediction Profile: choose Patient (or Test), and switch GPU Acceleration on/off as wanted."
try { Stop-Transcript | Out-Null } catch {}
if (-not $Check -and $Problems.Count -eq 0) {
    if (Ask "Start the AAC system now (start_aac.bat)?") { Start-Process -FilePath (Join-Path $Root "start_aac.bat") -WorkingDirectory $Root }
}
