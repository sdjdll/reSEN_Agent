@echo off

chcp 65001 >nul 2>&1

setlocal EnableExtensions



rem reSEN Agent 启动入口（Windows / 纯 cmd 实现，不依赖 PowerShell）

rem 用法：start.bat           启动 TUI

rem       start.bat --check   只校验运行环境、配置和依赖

rem       start.bat --help    查看帮助



rem ---------- 解析参数 ----------

set "CHECK="

if /i "%~1"=="--check" set "CHECK=1"

if /i "%~1"=="-check"  set "CHECK=1"

if /i "%~1"=="/check"  set "CHECK=1"

if /i "%~1"=="--help"  goto :usage

if /i "%~1"=="-help"   goto :usage

if /i "%~1"=="-h"      goto :usage

if /i "%~1"=="/?"      goto :usage

if not "%~1"=="" (

    echo 错误：未知参数：%~1（使用 --help 查看用法）

    exit /b 1

)

if not "%~2"=="" (

    echo 错误：参数过多：%~2（使用 --help 查看用法）

    exit /b 1

)



rem ---------- 路径与环境 ----------

set "ROOT=%~dp0"

set "VENV_DIR=%ROOT%.venv"

set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

set "REQ=%ROOT%requirement"

set "CONFIG=%ROOT%configure\config.json"



set "PYTHONUTF8=1"

set "PYTHONIOENCODING=utf-8"



pushd "%~dp0" >nul 2>&1

if errorlevel 1 (

    echo 错误：无法进入项目目录：%~dp0

    exit /b 1

)



echo [1/5] 检查运行环境

if not exist "%REQ%"    goto :err_no_req

if not exist "%CONFIG%" goto :err_no_config



rem 依次尝试：环境变量 PYTHON -> py -3 -> python -> python3（均要求 3.10+）

if defined PYTHON goto :py_from_env



py -3 -c "import sys;raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1

if errorlevel 1 goto :py_try_exe

set "BASE_PY=py"

set "BASE_ARGS=-3"

goto :py_ok



:py_try_exe

python -c "import sys;raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1

if errorlevel 1 goto :py_try_exe3

set "BASE_PY=python"

set "BASE_ARGS="

goto :py_ok



:py_try_exe3

python3 -c "import sys;raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1

if errorlevel 1 goto :err_no_python

set "BASE_PY=python3"

set "BASE_ARGS="

goto :py_ok



:py_from_env

"%PYTHON%" -c "import sys;raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1

if errorlevel 1 goto :err_no_python

set "BASE_PY=%PYTHON%"

set "BASE_ARGS="



:py_ok

<nul set /p "=  使用 Python："

"%BASE_PY%" %BASE_ARGS% --version



echo [2/5] 校验配置文件

"%BASE_PY%" %BASE_ARGS% -c "import json,sys;c=json.load(open(sys.argv[1],encoding='utf-8-sig'));ok=isinstance(c,dict) and all(isinstance(c.get(k),str) and c.get(k).strip() for k in ('apikey','baseurl','modelname'));sys.exit(0 if ok else 2)" "%CONFIG%"

if errorlevel 1 goto :err_config



echo [3/5] 创建并安装虚拟环境依赖

if exist "%VENV_PY%" goto :venv_ready

"%BASE_PY%" %BASE_ARGS% -m venv "%VENV_DIR%"

if errorlevel 1 goto :err_venv

:venv_ready

if not exist "%VENV_PY%" goto :err_venv

"%VENV_PY%" -m pip install -r "%REQ%"

if errorlevel 1 goto :err_pip



echo [4/5] 验证依赖

"%VENV_PY%" -m pip check

if errorlevel 1 goto :err_pipcheck

"%VENV_PY%" -c "import openai,textual" >nul 2>&1

if errorlevel 1 goto :err_import



if defined CHECK goto :checked



echo [5/5] 启动 reSEN Agent

"%VENV_PY%" "%ROOT%tui\main.py"

set "EXITCODE=%ERRORLEVEL%"

popd

exit /b %EXITCODE%



:checked

echo 校验通过

popd

exit /b 0



:usage

echo 用法：start.bat [--check] [--help]

echo   --check  只校验运行环境、配置和依赖，不启动 TUI

echo   --help   显示本帮助

exit /b 0



:err_no_req

echo 错误：依赖清单不存在：%REQ%

goto :fail



:err_no_config

echo 错误：配置文件不存在：%CONFIG%

goto :fail



:err_no_python

echo 错误：找不到 Python 3.10 或更高版本，请安装后重试（也可用环境变量 PYTHON 指定解释器）

goto :fail



:err_config

echo 错误：配置文件无效，需为 JSON 对象且包含非空的 apikey / baseurl / modelname

goto :fail



:err_venv

echo 错误：创建虚拟环境失败（若 .venv 是在 WSL/Linux 下创建的，请删除该目录后重试）

goto :fail



:err_pip

echo 错误：依赖安装失败，请检查网络连接或 requirement 文件

goto :fail



:err_pipcheck

echo 错误：依赖校验未通过（pip check）

goto :fail



:err_import

echo 错误：依赖导入失败，请检查 requirement 并重新安装

goto :fail



:fail

popd

echo.

echo 启动失败，退出码：1

pause

exit /b 1

