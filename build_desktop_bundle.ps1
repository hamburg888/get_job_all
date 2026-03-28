param(
    [string]$FrontendProjectPath = (Join-Path $PSScriptRoot '..\..\Front\job_help'),
    [string]$EmbeddedFrontendPath = (Join-Path $PSScriptRoot 'frontend_dist'),
    [string]$DesktopAssetsPath = (Join-Path $PSScriptRoot 'desktop_assets'),
    [string]$PythonExe = (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    [switch]$SkipPyInstaller
)

$ErrorActionPreference = 'Stop'

function Resolve-FullPath {
    param([string]$PathValue)
    return [System.IO.Path]::GetFullPath($PathValue)
}

$frontendRoot = Resolve-FullPath $FrontendProjectPath
$embeddedRoot = Resolve-FullPath $EmbeddedFrontendPath
$desktopAssetsRoot = Resolve-FullPath $DesktopAssetsPath
$pythonPath = Resolve-FullPath $PythonExe
$frontendDist = Join-Path $frontendRoot 'dist'
$desktopOutput = Join-Path $PSScriptRoot 'dist_desktop'
$buildOutput = Join-Path $PSScriptRoot 'build'
$specPath = Join-Path $PSScriptRoot 'JobHelpDesktop.spec'
$templateDbPath = Join-Path $desktopAssetsRoot 'job_help.template.db'
$appIconPath = Join-Path $desktopAssetsRoot 'app_icon.ico'

Write-Host "==> 前端项目: $frontendRoot"
Write-Host "==> 嵌入目录: $embeddedRoot"
Write-Host "==> 桌面资源: $desktopAssetsRoot"
Write-Host "==> Python: $pythonPath"
Write-Host "==> 应用图标: $appIconPath"

if (-not (Test-Path $frontendRoot)) {
    throw "未找到前端项目目录: $frontendRoot"
}

if (-not (Test-Path $pythonPath)) {
    throw "未找到 Python 可执行文件: $pythonPath"
}

if (-not (Test-Path $specPath)) {
    throw "未找到 PyInstaller 规格文件: $specPath"
}

Push-Location $frontendRoot
try {
    Write-Host "==> 构建前端 dist"
    npm run build
}
finally {
    Pop-Location
}

if (-not (Test-Path $frontendDist)) {
    throw "前端构建完成后未找到 dist 目录: $frontendDist"
}

if (Test-Path $embeddedRoot) {
    Remove-Item -Path $embeddedRoot -Recurse -Force
}

Write-Host "==> 复制前端 dist 到后端 frontend_dist"
Copy-Item -Path $frontendDist -Destination $embeddedRoot -Recurse

$embeddedIndex = Join-Path $embeddedRoot 'index.html'
if (-not (Test-Path $embeddedIndex)) {
    throw "嵌入后的前端产物缺少 index.html: $embeddedIndex"
}

if (-not (Test-Path $desktopAssetsRoot)) {
    New-Item -ItemType Directory -Path $desktopAssetsRoot | Out-Null
}

if (-not (Test-Path $appIconPath)) {
    throw "未找到桌面图标文件: $appIconPath"
}

Write-Host "==> 生成空数据库模板"
& $pythonPath "$PSScriptRoot\\build_empty_db_template.py" $templateDbPath | Out-Null

if (-not (Test-Path $templateDbPath)) {
    throw "空数据库模板生成失败: $templateDbPath"
}

if ($SkipPyInstaller) {
    Write-Host "==> 已跳过 PyInstaller。当前已完成前端嵌入，可直接运行:"
    Write-Host "   $pythonPath $PSScriptRoot\\run_desktop.py"
    exit 0
}

Write-Host "==> 检查 PyInstaller"
& $pythonPath -m PyInstaller --version | Out-Null

if (Test-Path $desktopOutput) {
    Remove-Item -Path $desktopOutput -Recurse -Force
}

if (Test-Path $buildOutput) {
    Remove-Item -Path $buildOutput -Recurse -Force
}

Write-Host "==> 开始打包桌面版"
& $pythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $desktopOutput `
    --workpath $buildOutput `
    $specPath

Write-Host "==> 打包完成: $(Join-Path $desktopOutput 'JobHelpDesktop')"
