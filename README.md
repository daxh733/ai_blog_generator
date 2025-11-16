# AI Blog Generator

This repository contains a small Django app that generates blog posts from YouTube videos by downloading audio, transcribing via AssemblyAI, and summarizing via LLM or transformers.

Important: Do NOT store API keys in source code. Use environment variables and GitHub Secrets instead.

Quick local setup

1. Create and activate a virtual environment (PowerShell):

```powershell
python -m venv venv
& .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Create a `.env` file in the project root (this file must NOT be committed):

```
ASSEMBLY_KEY="your_assemblyai_api_key"
HUGGINGFACEHUB_API_TOKEN="your_huggingface_token"
DJANGO_SECRET_KEY="replace-with-a-secret"
```

3. Ensure `ffmpeg` is installed and available on PATH (or set `FFMPEG_PATH` env var pointing to the folder containing `ffmpeg.exe`).

4. Run migrations and start the dev server:

```powershell
python manage.py migrate
python manage.py runserver
```

Preparing to push to GitHub

- Add `.env` to `.gitignore` (already included).
- Create a GitHub repository and push your code.
- In the repository Settings → Secrets, add the following Secrets:
  - `ASSEMBLY_KEY`
  - `HUGGINGFACEHUB_API_TOKEN`
  - `DJANGO_SECRET_KEY`

GitHub Actions (CI) template

A sample workflow is provided in `.github/workflows/ci.yml`. It installs dependencies and runs `python manage.py check`. You must add the required secrets to the repository for workflows that need them.

Security checklist before public repo

- Remove any hard-coded keys from your code. This repo loads `ASSEMBLY_KEY` from environment.
- Rotate keys that were previously committed to any remote (GitHub) — treat commits containing secrets as compromised.
- Add `.env` to `.gitignore` and never commit it.

If you want, I can:
- Add a script to rotate or scrub keys from git history (BFG or git filter-repo) and show exact commands.
- Patch `ai_blog_generator/settings.py` to read `SECRET_KEY` and `DEBUG` from env for safer deployment.
- Create a production-ready `gunicorn`/`Dockerfile` and a GitHub Actions deployment workflow.

Tell me which of the above you'd like next (scrub git history, patch settings, add Dockerfile, or add CI workflow).