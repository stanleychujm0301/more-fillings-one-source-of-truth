@echo off
REM AHCC 后端一键启动器（双击或命令行运行）。
REM 先杀掉残留 uvicorn 进程，再只起一个实例，杜绝旧进程残留导致的「改了代码没生效」。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_server.ps1" %*
