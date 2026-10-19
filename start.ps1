#Requires -Version 5.1

param(
    [switch]$Check,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$ProjectDir = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}
Set-Location -LiteralPath $ProjectDir

$VenvDir          = Join-Path $ProjectDir '.venv'
$VenvPython       = Join-Path $VenvDir 'Scripts\python.exe'
$RequirementsFile = Join-Path $ProjectDir 'requirement'

$script:PythonExe    = $null
$script:PythonPrefix = @()

function Fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    $Host.UI.WriteErrorLine("错误：$Message")
    exit 1
}

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host $Message
}

function Find-Python {
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($env:PYTHON)) {
        $candidates += , @($env:PYTHON)
    }
    $candidates += , @('py', '-3')
    $candidates += , @('python')
    $candidates += , @('python3')

    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        $prefix = @()
        if ($candidate.Count -gt 1) {
            $prefix = $candidate[1..($candidate.Count - 1)]
        }

        $command = Get-Command -Name $exe -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $command) { continue }

        & $command.Source @prefix -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) {
            $script:PythonExe = $command.Source
            $script:PythonPrefix = $prefix
            return $true
        }
    }
    return $false
}

if ($Help) {
    Write-Host '用法：start.ps1 [-Check] [-Help]'
    Write-Host '  -Check  只校验运行环境和依赖，不启动 TUI'
    Write-Host '  -Help   显示本帮助'
    exit 0
}

Write-Step '[1/4] 检查运行环境'
if (-not (Find-Python)) {
    Fail '找不到 Python 3.10 或更高版本，请安装后重试（也可用环境变量 PYTHON 指定解释器）'
}
if (-not (Test-Path -LiteralPath $RequirementsFile -PathType Leaf)) {
    Fail "依赖清单不存在：$RequirementsFile"
}

$BasePython = $script:PythonExe
$BasePythonArgs = @($script:PythonPrefix)
$pythonVersion = (& $BasePython @BasePythonArgs --version 2>&1 | Out-String).Trim()
Write-Host "  使用 Python：$pythonVersion"

Write-Step '[2/4] 创建并安装虚拟环境依赖'
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    & $BasePython @BasePythonArgs -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Fail '创建虚拟环境失败（若 .venv 是在 WSL/Linux 下创建的，请删除该目录后重试）'
    }
}
& $VenvPython -m pip install -r $RequirementsFile
if ($LASTEXITCODE -ne 0) { Fail '依赖安装失败，请检查网络连接或 requirement 文件' }

Write-Step '[3/4] 验证依赖'
& $VenvPython -m pip check
if ($LASTEXITCODE -ne 0) { Fail '依赖校验未通过（pip check）' }

& $VenvPython -c 'import openai, textual'
if ($LASTEXITCODE -ne 0) { Fail '依赖导入失败，请检查 requirement 并重新安装' }

if ($Check) {
    Write-Host '校验通过'
    exit 0
}

Write-Step '[4/4] 启动 reSEN Agent'
& $VenvPython (Join-Path $ProjectDir 'tui\main.py')
exit $LASTEXITCODE
