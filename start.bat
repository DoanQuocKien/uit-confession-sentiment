@echo off
rem Double-click to open the UIT Confession viewer in your browser.
rem First run installs what the viewer needs (streamlit + pandas) into .venv; later runs start at once.
rem Machine-specific paths (OLLAMA_EXE, OLLAMA_MODELS, CHROME_EXE, CHROME_PROFILE) go in local_env.bat, which git ignores.
cd /d "%~dp0"
if exist local_env.bat call local_env.bat
if not exist ".venv\Scripts\streamlit.exe" (
  echo First run: setting up the viewer ^(about a minute^)...
  where python >nul 2>nul || ( echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1 )
  python -m venv .venv || ( echo Could not create .venv & pause & exit /b 1 )
  ".venv\Scripts\python" -m pip install --quiet streamlit pandas || ( echo Install failed, check your internet connection & pause & exit /b 1 )
)
echo Starting the viewer at http://localhost:8501  ^(close this window to stop it^)
rem open the browser a few seconds after the server starts (a second launch just reuses the running server)
start "" /min cmd /c "timeout /t 4 >nul & start http://localhost:8501"
".venv\Scripts\streamlit.exe" run app.py --server.headless true --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
pause
