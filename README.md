# 🌸 67Math — English Math Learning

A Gradio-based English Math Learning platform with:

- OXYZ 3D simulator
- Account / login / register
- Vocabulary decks + spaced repetition
- Random vocabulary tests
- Math exercise generator
- AI Math Tutor with image upload
- Light / dark mode
- English / Vietnamese UI
- Dashboard and learning progress

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Open `http://localhost:7860`.

## OpenAI API key

Do **not** put the API key directly into GitHub. Set it as an environment variable / secret:

```bash
OPENAI_API_KEY=your_key_here
```

The app works without an OpenAI key, but AI explanations and the AI Tutor will be disabled.

## GitHub

```bash
git init
git add .
git commit -m "Initial 67Math app"
git branch -M main
git remote add origin YOUR_GITHUB_REPO_URL
git push -u origin main
```

## Deploy

GitHub stores the source code; it does not itself run the Gradio server. Use a hosting service such as Hugging Face Spaces, Render, or another Python host connected to the repository.

Set these environment variables on the host:

- `OPENAI_API_KEY` — optional, required for AI features
- `OPENAI_MODEL` — optional, defaults to `gpt-6-luna`
- `DB_PATH` — optional; point this to persistent storage if you need accounts/progress to survive redeploys
- `PORT` — usually supplied by the host

### Important: SQLite persistence

The app stores accounts, decks, reviews and progress in SQLite. If the hosting platform uses an ephemeral filesystem, the database can disappear after a restart/redeploy. Use a persistent disk/volume or migrate the database to PostgreSQL for a multi-user production deployment.
