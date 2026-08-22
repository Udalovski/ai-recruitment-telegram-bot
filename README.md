# 🤖 AI-Powered Recruitment & HR Telegram Bot

> 🔒 **Confidentiality & Privacy Notice**: All UI texts, button labels, notifications, prompts, and application templates in this repository have been generalized and translated to English. Specific company data, proprietary workflows, internal links, and credentials have been replaced with generic open-source templates for privacy and compliance.

An asynchronous, production-ready Telegram Bot built with **Python**, **aiogram 3**, and **Anthropic Claude API** for automated candidate screening, qualification interviews, shift scheduling, and real-time synchronization with **PostgreSQL** and **Google Sheets**.

---

## 🌟 Key Features

- **🧠 Intelligent AI Screening**: Real-time evaluation of candidate responses and resume screening using Anthropic's Claude LLM.
- **🔄 Finite State Machine (FSM)**: Multi-step recruitment workflows, dynamic branching, and custom vacancy creation.
- **📊 Real-time Data Sync**: Automated test scoring and candidate data export to **Google Sheets API** (`gspread`).
- **🗄️ Asynchronous Persistence**: High-performance state, schedule, and metadata management via **PostgreSQL** (`asyncpg`).
- **👥 Role-Based Access Control**: Separate capabilities for Admins, HR managers, and Candidates (shift management, broadcast alerts, live interview handoff).
- **🐳 Containerized Deployment**: Ready for one-command production deployment with **Docker & Docker Compose**.

---

## 🛠️ Tech Stack

- **Framework**: `aiogram 3.20+` (async Python Telegram Bot framework)
- **AI / LLM**: `anthropic` SDK (Claude Sonnet 4.6)
- **Database**: PostgreSQL 15+ (`asyncpg`), Raw SQL & JSONB state storage
- **Integrations**: Google Sheets API (`gspread`, `google-auth`)
- **DevOps**: Docker, Docker Compose, `python-dotenv`

---

## 🚀 Quick Start

### 1. Clone the repository
```bash
git clone https://github.com/Udalovski/ai-recruitment-telegram-bot.git
cd ai-recruitment-telegram-bot
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```

If using Google Sheets integration, place your service account key at `credentials.json` (see `credentials.example.json`).

### 3. Run with Docker Compose
```bash
docker-compose up --build -d
```

### 4. Local Development (without Docker)
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

---

## 📝 License
MIT License. Free for open-source and commercial use.
