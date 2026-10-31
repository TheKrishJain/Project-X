# Auto-Poster

An automated, typed Python 3.11 social publishing engine that generates and posts high-signal content to **X** (Twitter) and **Meta Threads** via **GitHub Actions**.

Built around a sharp founder voice with zero fluff, strict style constraints, and deterministic scheduling.

---

## 🎯 Voice & Philosophy

Inspired by a sharp young founder from Mumbai who reads extensively, notices underlying patterns, and talks like a smart peer rather than a guru or corporate brand.

- **Zero buzzwords:** No *"game-changer"*, *"delve"*, *"unlock"*, *"Let that sink in"*, *"Here's the thing"*.
- **No hashtags, no rhetorical openers:** Starts directly with the observation.
- **Specific over generic:** Features real companies, specific features, and concrete numbers.
- **Strict character budget:** Every post fits within 280 characters for seamless cross-platform delivery.

---

## 📅 Schedule & Content Matrix

The schedule is configured in [`config/schedule.yaml`](file:///c:/Users/msi/Desktop/ProjectX/config/schedule.yaml):

### Morning Slot (09:00 AM IST / 03:30 AM UTC)
| Day | Pillar | X Format | Threads Format |
| :--- | :--- | :--- | :--- |
| **Monday** | `tech_for_business` | Single Post | Single Post |
| **Tuesday** | `marketing_case_study` | Single Post | **Thread (4–5 posts)** |
| **Wednesday** | `ai_hack` | Single Post | Single Post |
| **Thursday** | `business_strategy` | **Thread (4–5 posts)** | **Thread (4–5 posts)** |
| **Friday** | `marketing_case_study` or `tech_for_business` (random) | Single Post | Single Post |
| **Saturday** | `ai_hack` | Single Post | **Thread (4–5 posts)** |
| **Sunday** | `business_strategy` | Single Post | Single Post |

### Night Slot (09:00 PM IST / 15:30 PM UTC)
- **Distribution:** `psychology_insight` (75%) vs. `motivation` (25%).
- **Rotating industries:** `finance`, `marketing`, `operations`, `startups`.
- **Format:** Single post on both platforms.

---

## 📁 Project Structure

```text
auto-poster/
├── .github/
│   └── workflows/
│       └── auto_post.yml      # GitHub Actions cron & dispatch workflow
├── config/
│   ├── schedule.yaml          # Slot timings, pillar rotation, thread rules
│   └── style.md               # Persona guidelines, banned words, sample posts
├── data/
│   ├── history.json           # Log of past published posts & platform IDs
│   └── x_usage.json           # Monthly X API quota tracker {"month", "count"}
├── src/
│   ├── __init__.py
│   ├── generate.py            # Pillar selection, Gemini prompt, style enforcement
│   ├── post_x.py              # X API v2 (OAuth 1.0a, thread reply chaining, quota check)
│   ├── post_threads.py        # Threads Graph API (container creation & publish)
│   └── main.py                # CLI runner & scheduler coordinator
├── .env.example               # Template for required environment secrets
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 🚀 Setup & Local Usage

### 1. Prerequisites
- **Python 3.11+**
- Virtual environment (recommended)

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Environment Variables
Copy `.env.example` to `.env` and configure your credentials:

```bash
cp .env.example .env
```

| Secret | Purpose |
| :--- | :--- |
| `GEMINI_API_KEY` | Google Gemini API key for content generation |
| `GEMINI_MODEL` | Optional model name (default: `gemini-2.5-flash`) |
| `X_API_KEY` | X Consumer API Key |
| `X_API_SECRET` | X Consumer API Secret |
| `X_ACCESS_TOKEN` | X User Access Token (with write permission) |
| `X_ACCESS_TOKEN_SECRET` | X User Access Token Secret |
| `THREADS_ACCESS_TOKEN` | Meta Threads long-lived User Access Token |
| `THREADS_USER_ID` | Numeric Meta Threads User ID |
| `DRY_RUN` | Set to `"true"` to simulate without posting |

### 3. Running Locally

**Preview generated post without publishing or writing data:**
```bash
python -m src.main --preview
```

**Dry-run scheduled post (simulates X and Threads API publishing):**
```bash
python -m src.main --dry-run
```

**Test a specific day or slot:**
```bash
# Test Thursday morning thread logic
python -m src.main --slot morning --day thursday --dry-run

# Test Saturday AI hack thread on Threads only
python -m src.main --slot morning --day saturday --dry-run

# Test Night slot with custom pillar
python -m src.main --slot night --force-pillar psychology_insight --dry-run
```

---

## ⚡ GitHub Actions Automation

The repository includes a ready-to-run GitHub Actions workflow at [`.github/workflows/auto_post.yml`](file:///.github/workflows/auto_post.yml).

### 1. Add Repository Secrets
Navigate to **Settings > Secrets and variables > Actions** in your GitHub repository and add:
- `GEMINI_API_KEY`
- `X_API_KEY`
- `X_API_SECRET`
- `X_ACCESS_TOKEN`
- `X_ACCESS_TOKEN_SECRET`
- `THREADS_ACCESS_TOKEN`
- `THREADS_USER_ID`

### 2. Grant Workflow Write Permissions
To allow the action to persist `data/history.json` and `data/x_usage.json` across scheduled runs:
1. Go to **Settings > Actions > General**.
2. Scroll to **Workflow permissions**.
3. Select **Read and write permissions**.
4. Check **Allow GitHub Actions to create and approve pull requests**.
5. Click **Save**.

### 3. Manual Triggers (Workflow Dispatch)
You can trigger test runs anytime directly from GitHub:
1. Go to the **Actions** tab.
2. Select **Auto-Post Scheduled Content**.
3. Click **Run workflow**, choose your slot (`morning`/`night`/`auto`), and toggle `dry_run` if testing.

---

## 🛡️ Quota & Safety Features

1. **Monthly X Quota Protection:**
   [`data/x_usage.json`](file:///c:/Users/msi/Desktop/ProjectX/data/x_usage.json) tracks `{ "month": "YYYY-MM", "count": N }`. If the monthly usage limit (default 500) is reached, X publishing is guarded to prevent unexpected billings or account suspensions. Resets automatically on the first day of each month.

2. **Deduplication:**
   [`src/generate.py`](file:///c:/Users/msi/Desktop/ProjectX/src/generate.py) inspects recent entries in [`data/history.json`](file:///c:/Users/msi/Desktop/ProjectX/data/history.json) and injects anti-repetition instructions into the prompt context to keep examples fresh.

3. **Validation & Sanitation:**
   Every generated post is automatically scrubbed of banned phrases, hashtags, rhetorical question openers, and em-dashes, and verified against the 280-character boundary.
