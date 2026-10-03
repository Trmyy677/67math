# 🌸 67Math — English Math Learning

A standalone web app built with **FastAPI + HTML/CSS/JavaScript + SQLite + Three.js**. No Gradio.

## Run locally

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`.

## Environment

Set `OPENAI_API_KEY` as a deployment secret. Never commit the key to GitHub.

## Features

- Fixed navigation bar
- Separate Account page
- Login/Register modal with blurred background
- Light/Dark mode
- English/Vietnamese toggle
- OXYZ 3D simulator
- Vocabulary decks, flashcard review, spaced repetition, random test
- Exercise generator
- AI tutor with image upload
- SQLite account/progress tracking
