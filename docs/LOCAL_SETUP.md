# Local Development Setup

Guide for running Healio locally on Windows, macOS, or Linux.

## Prerequisites

- **Python 3.12** ([download](https://www.python.org/downloads/))
- **Git** ([download](https://git-scm.com/))
- **uv** package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))
- **OpenAI API key** ([get one](https://platform.openai.com/api-keys))
- **LangSmith credentials** ([sign up](https://smith.langchain.com/)) — optional but recommended
- **Telegram Bot token** ([create via @BotFather](https://core.telegram.org/bots/tutorial))

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/ramkiGitHub/Healio.git
cd Healio
```

### 2. Install Dependencies

```bash
# Install all dependencies (including dev tools)
uv sync --extra dev

# Activate the virtual environment
# On Windows:
.\.venv\Scripts\Activate.ps1

# On macOS/Linux:
source .venv/bin/activate
```

### 3. Configure Environment

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your keys:
# - OPENAI_API_KEY: Your OpenAI secret key
# - LANGCHAIN_API_KEY: LangSmith API key (optional, but recommended for tracing)
# - TELEGRAM_BOT_TOKEN: Your Telegram bot token
# - DOCTOR_CHAT_ID: Your Telegram chat ID for alerts
```

**To find your Telegram chat ID:**
1. Open Telegram and search for `@userinfobot`
2. Send `/start` — it will reply with your chat ID

### 4. Run the Server

```bash
# Start the local development server with auto-reload
uvicorn app.main:app --reload --port 8000
```

Server will be available at: **http://localhost:8000**

#### Verify Server is Running

```bash
# Open a new terminal
curl http://localhost:8000/health
# Should return: {"status": "ok", "version": "0.1.0", "env": "development"}
```

### 5. Run Tests

```bash
# Run full test suite (131 tests)
pytest tests/ -v

# Run tests with coverage report
pytest tests/ --cov=app --cov-report=html
# Open htmlcov/index.html to view coverage
```

### 6. Run the Demo Script

```bash
# Test the complete pipeline locally (requires valid OPENAI_API_KEY)
python scripts/demo.py
```

This will run 3 example flows:
- Emergency triage (chest pain → doctor alert)
- Multi-turn appointment booking
- General Q&A with allergy conflict detection

## Project Structure

```
Healio/
├── app/
│   ├── main.py              # FastAPI application entry point
│   ├── config.py            # Environment configuration (pydantic-settings)
│   ├── constants.py         # Enums and constants
│   ├── exceptions.py        # Custom exception classes
│   ├── logging_config.py    # Structured logging setup (structlog)
│   ├── channels/            # Message channel handlers
│   │   ├── telegram.py      # Telegram webhook & polling
│   │   ├── whatsapp.py      # WhatsApp (Twilio + Meta Cloud API)
│   │   └── normalizer.py    # Payload normalization layer
│   ├── graph/               # LangGraph pipeline
│   │   ├── graph.py         # Graph assembly and run_graph() entry point
│   │   ├── nodes.py         # Graph nodes (router, emergency, etc.)
│   │   ├── edges.py         # Conditional routing logic
│   │   └── state.py         # State schema
│   ├── nlp/                 # NLP pipelines
│   │   ├── biobert.py       # BioBERT medical NER
│   │   └── severity.py      # Emergency severity scoring
│   └── tools/               # LangGraph tools (integrations)
│       ├── alerts.py        # HITL doctor alerts
│       ├── calendar.py      # Appointment scheduling
│       └── ehr.py           # EHR patient lookup
├── tests/                   # 131 unit + integration tests
│   ├── conftest.py
│   ├── test_channels.py
│   ├── test_graph.py
│   └── ...
├── scripts/
│   └── demo.py              # End-to-end demo script (3 flows)
├── docs/                    # Documentation
├── data/
│   ├── db/                  # SQLite database (created on startup)
│   └── mock_patients.json   # Mock patient data for testing
├── pyproject.toml           # Project config + dependencies
├── Dockerfile               # Docker image definition (prod-ready)
├── docker-compose.yml       # Local Docker orchestration
├── .env.example             # Environment variables template
├── .env                     # Your local environment (DO NOT COMMIT)
└── README.md
```

## Development Workflow

### Making Changes

1. **Edit code** in `app/` folder
2. **Tests auto-run** on save (uvicorn --reload watches files)
3. **Check logs** in the terminal running uvicorn
4. **Run unit tests** regularly: `pytest tests/ -q`

### Common Tasks

#### Test a new node
```bash
# Create tests/test_my_feature.py
pytest tests/test_my_feature.py -v
```

#### Add a new dependency
```bash
uv add package_name     # Add to project
uv sync                 # Update venv
```

#### Check code quality
```bash
# Lint with ruff
ruff check app/

# Type checking with mypy
mypy app/
```

#### View database
```bash
# SQLite CLI
sqlite3 ./data/db/healio.db

# Common queries:
# .tables                          # List tables
# SELECT * FROM langgraph_checkpoint;  # View conversation state
# .quit                           # Exit
```

## Debugging

### View Logs

Logs are printed to console in structured JSON format (powered by structlog):

```
2026-04-10T11:15:30.136168Z [info] healio_starting app_env=development openai_model=gpt-4o-mini
```

### Enable Debug Logging

In `.env`, set:
```
LOG_LEVEL=DEBUG
```

This shows detailed node execution, LLM calls, and tool invocations.

### Inspect LangGraph Traces

If `LANGCHAIN_TRACING_V2=true` and `LANGCHAIN_API_KEY` is set, visit:
**https://smith.langchain.com/** → Your project → View traces

Each trace shows:
- All graph node executions
- LLM token counts
- Tool calls and results
- Execution time

### Test Telegram Locally

You can test Telegram webhook locally using ngrok (for webhook mode):

```bash
# Install ngrok: https://ngrok.com/download
# Expose local port
ngrok http 8000

# Copy the generated HTTPS URL, e.g., https://abc123.ngrok.io
# Set in .env:
TELEGRAM_WEBHOOK_URL=https://abc123.ngrok.io

# Register with Telegram (run once):
curl -X POST https://api.telegram.org/bot{YOUR_TOKEN}/setWebhook \
  -d url=https://abc123.ngrok.io/webhook/telegram

# Send test message to your bot on Telegram
```

## Troubleshooting

### "ModuleNotFoundError: No module named 'app'"

**Solution:** Make sure you're running from the repo root:
```bash
cd Healio
uvicorn app.main:app --reload --port 8000
```

### "ValidationError: openai_api_key - Field required"

**Solution:** Your `.env` file is missing or `OPENAI_API_KEY` is blank.
```bash
cp .env.example .env
# Edit .env and add your OpenAI API key
```

### "LangSmith API key is invalid"

**Solution:** Optional feature — either:
1. Set a valid `LANGCHAIN_API_KEY` from https://smith.langchain.com
2. Or disable tracing in `.env`: `LANGCHAIN_TRACING_V2=false`

### Tests fail with "DISABLE_BIOBERT not found"

**Solution:** This is expected — tests mock BioBERT using `conftest.py`. Just run:
```bash
pytest tests/ -v
```

### Database locked error

**Solution:** Multiple processes are accessing SQLite simultaneously.
```bash
# Delete the old database
rm ./data/db/healio.db

# Restart the server
uvicorn app.main:app --reload --port 8000
```

## Next Steps

- Read [DEPLOYMENT.md](./DEPLOYMENT.md) for Docker and production deployment
- Check [CLINIC_SETUP.md](./CLINIC_SETUP.md) for clinic environment configuration
- Review [requirements.md](./requirements.md) for full feature specifications
