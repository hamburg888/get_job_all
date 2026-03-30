uvicorn app.main:app --reload

## Windows 桌面端打包

目录版桌面端与安装包统一通过下面的脚本构建：

```powershell
.\build_desktop_bundle.ps1
```

默认流程：

1. 构建前端 `dist`
2. 复制到后端 `frontend_dist`
3. 生成空数据库模板
4. 使用 PyInstaller 生成 `dist_desktop\JobHelpDesktop`
5. 如果系统已安装 Inno Setup 6，则继续生成安装包到 `dist_installer`

可选参数：

```powershell
.\build_desktop_bundle.ps1 -SkipInstaller
.\build_desktop_bundle.ps1 -SkipPyInstaller
.\build_desktop_bundle.ps1 -InnoSetupCompiler "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

