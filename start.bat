@echo off
rem Double-click to open the UIT Confession viewer (Streamlit) in your browser.
rem Machine-specific paths (OLLAMA_EXE, OLLAMA_MODELS, CHROME_EXE, CHROME_PROFILE) go in local_env.bat, which git ignores.
cd /d "%~dp0"
if exist local_env.bat call local_env.bat
if not exist ".venv\Scripts\streamlit.exe" (
  echo The virtual environment is missing. Run once:  python -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.txt
  pause
  exit /b 1
)
rem open the browser a few seconds after the server starts (a second launch just reuses the running server)
start "" /min cmd /c "timeout /t 4 >nul & start http://localhost:8501"
".venv\Scripts\streamlit.exe" run app.py --server.headless true --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
