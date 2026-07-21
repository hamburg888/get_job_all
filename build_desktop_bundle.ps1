param(
    # 前端工程已迁入本仓库 fronted 目录（拼写同实际目录名），保持可被参数覆盖
    [string]$FrontendProjectPath = (Join-Path $PSScriptRoot 'fronted'),
    [string]$EmbeddedFrontendPath = (Join-Path $PSScriptRoot 'frontend_dist'),
    [string]$DesktopAssetsPath = (Join-Path $PSScriptRoot 'desktop_assets'),
    [string]$PythonExe = (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    [switch]$SkipPyInstaller,
    [switch]$SkipInstaller,
    [string]$InnoSetupCompiler = '',
    [string]$AppVersion = '0.1.0'
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
$installerOutput = Join-Path $PSScriptRoot 'dist_installer'
$buildOutput = Join-Path $PSScriptRoot 'build'
$specPath = Join-Path $PSScriptRoot 'JobHelpDesktop.spec'
$installerSpecPath = Join-Path $PSScriptRoot 'JobHelpDesktop.iss'
$templateDbPath = Join-Path $desktopAssetsRoot 'job_help.template.db'
$appIconPath = Join-Path $desktopAssetsRoot 'app_icon.ico'

if (-not $InnoSetupCompiler) {
    $compilerCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
        'C:\Program Files\Inno Setup 6\ISCC.exe'
    )
    $InnoSetupCompiler = $compilerCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

Write-Host "==> 前端项目: $frontendRoot"
Write-Host "==> 嵌入目录: $embeddedRoot"
Write-Host "==> 桌面资源: $desktopAssetsRoot"
Write-Host "==> Python: $pythonPath"
Write-Host "==> 应用图标: $appIconPath"
if ($InnoSetupCompiler) {
    Write-Host "==> Inno Setup: $InnoSetupCompiler"
}

if (-not (Test-Path $frontendRoot)) {
    throw "未找到前端项目目录: $frontendRoot"
}

if (-not (Test-Path $pythonPath)) {
    throw "未找到 Python 可执行文件: $pythonPath"
}

if (-not (Test-Path $specPath)) {
    throw "未找到 PyInstaller 规格文件: $specPath"
}

if (-not (Test-Path $installerSpecPath)) {
    throw "未找到安装包脚本: $installerSpecPath"
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
    try {
        Remove-Item -Path $desktopOutput -Recurse -Force
    }
    catch {
        throw "无法清理旧的桌面版输出目录，请先关闭正在运行的 JobHelpDesktop 程序或释放占用文件后再重试。目录: $desktopOutput"
    }
}

if (Test-Path $buildOutput) {
    try {
        Remove-Item -Path $buildOutput -Recurse -Force
    }
    catch {
        throw "无法清理旧的构建缓存目录，请关闭占用文件的进程后重试。目录: $buildOutput"
    }
}

Write-Host "==> 开始打包桌面版"
& $pythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $desktopOutput `
    --workpath $buildOutput `
    $specPath

Write-Host "==> 打包完成: $(Join-Path $desktopOutput 'JobHelpDesktop')"

if ($SkipInstaller) {
    Write-Host "==> 已跳过安装包生成"
    exit 0
}

if (-not $InnoSetupCompiler) {
    Write-Host "==> 未检测到 Inno Setup，已完成目录版打包。"
    Write-Host "   安装 Inno Setup 6 后，重新执行本脚本即可生成安装包。"
    exit 0
}

if (Test-Path $installerOutput) {
    try {
        Remove-Item -Path $installerOutput -Recurse -Force
    }
    catch {
        throw "无法清理旧的安装包输出目录，请关闭占用文件的进程后重试。目录: $installerOutput"
    }
}

$appVersion = if ($AppVersion) { $AppVersion.TrimStart('v', 'V') } else { '0.1.0' }

Write-Host "==> 应用版本: $appVersion"
Write-Host "==> 开始生成安装包"
& $InnoSetupCompiler `
    "/DMyAppVersion=$appVersion" `
    $installerSpecPath

Write-Host "==> 安装包生成完成: $installerOutput"
