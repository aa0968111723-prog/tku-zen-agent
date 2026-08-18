@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 淡江大學領袖禪學社 · AI 代理

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   第一次啟動，正在建立 Python 環境，大約需要 1-2 分鐘...
  echo.
  python -m venv .venv
  if errorlevel 1 (
    echo.
    echo   建立失敗。請確認電腦有安裝 Python 3.10 以上版本。
    echo   下載： https://www.python.org/downloads/
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo   套件安裝失敗，請把上面的錯誤訊息回報。
    pause
    exit /b 1
  )
  echo.
  echo   環境建立完成。
  echo.
)

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo.
  echo   ⚠  已經幫你建立 .env 設定檔。
  echo.
  echo   請在接下來打開的記事本裡，把 NVIDIA_API_KEY 那一行
  echo   換成你自己的金鑰（到 https://build.nvidia.com/settings/api-keys 免費申請）
  echo   存檔後關掉記事本，這個視窗會繼續。
  echo.
  notepad ".env"
)

".venv\Scripts\python.exe" -m app
echo.
echo   伺服器已停止。
pause
