# Auto-Poster

An automated, typed Python 3.11 social publishing engine that generates and posts high-signal content to **X** (Twitter) and **Meta Threads** via **GitHub Actions**.

Built around a sharp founder voice with zero fluff, strict style constraints, and deterministic scheduling.

---

## 🎯 Voice & Philosophy

Inspired by a sharp young founder from Mumbai who reads extensively, notices underlying patterns, and talks like a smart peer rather than a guru or corporate brand.

- **Zero buzzwords:** No *"game-changer"*, *"delve"*, *"unlock"*, *"Let that sink in"*, *"Here's the thing"*.
- **No hashtags, no rhetorical openers:** Starts directly with the observation.
- **Specific over generic:** Features real companies, specific features, and concrete numbers.
- **Strict character budget:**
  - **X posts:** Each post strictly $\le 270$ characters. No URLs.
  - **Threads posts:** Each post $\le 480$ characters (can be richer, more detailed).

---

## 📅 Schedule & Content Matrix

The schedule is configured in [`config/schedule.yaml`](file:///c:/Users/msi/Desktop/ProjectX/config/schedule.yaml):

### Morning Slot (Cron: `45 3 * * *` ~ 09:15 AM IST / 03:45 AM UTC)
| Day | Pillar | X Format | Threads Format |
| :--- | :--- | :--- | :--- |
| **Monday** | `tech_for_business` | Single Post | Single Post |
| **Tuesday** | `marketing_case_study` | Single Post | **Thread (4–5 posts)** |
| **Wednesday** | `ai_hack` | Single Post | Single Post |
| **Thursday** | `business_strategy` | **Thread (4–5 posts)** | **Thread (4–5 posts)** |
| **Friday** | `marketing_case_study` or `tech_for_business` (random) | Single Post | Single Post |
| **Saturday** | `ai_hack` | Single Post | **Thread (4–5 posts)** |
| **Sunday** | `business_strategy` | Single Post | Single Post |

### Night Slot (Cron: `15 15 * * *` ~ 08:45 PM IST / 03:15 PM UTC)
- **Distribution:** `psychology_insight` (75%) vs. `motivation` (25%).
- **Rotating industries:** `finance`, `marketing`, `operations`, `startups`.
- **Format:** Single post on both platforms.

---

## 📁 Project Structure

```text
auto-poster/
├── .github/
│   └── workflows/
│       ├── post.yml                   # Morning & night scheduled post workflow
│       └── refresh-threads-token.yml  # Bi-monthly Threads token refresher
├── config/
│   ├── schedule.yaml                  # Slot timings, pillar rotation, thread rules
│   └── style.md                       # Persona guidelines, banned words, sample posts
├── data/
│   ├── history.json                   # Log of published posts, topics & platform IDs
│   └── x_usage.json                   # Monthly X API quota tracker {"month", "count"}
├── src/
│   ├── __init__.py
│   ├── generate.py                    # google-genai, search grounding, self-critique
│   ├── post_x.py                      # tweepy.Client, OAuth 1.0a, budget guard
│   ├── post_threads.py                # Graph API v1.0, 5s delay, token refresh
│   └── main.py                        # CLI runner, jitter sleep, independent dispatch
├── .env.example                       # Template for local environment secrets
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 🔑 Required Secrets

Navigate to **Settings > Secrets and variables > Actions** in your GitHub repository and configure the following secrets:

| Secret | Purpose | Source / Notes |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Google Gemini API key | [Google AI Studio](https://aistudio.google.com/) |
| `THREADS_USER_ID` | Numeric Meta Threads user ID | Meta Graph API user lookup (`/me`) |
| `THREADS_ACCESS_TOKEN` | Long-lived Meta Threads user token | Meta Developers Portal (60-day token) |
| `X_API_KEY` | X Developer App Consumer API Key | [X Developer Portal](https://developer.x.com/) |
| `X_API_SECRET` | X Developer App Consumer API Secret | X Developer Portal |
| `X_ACCESS_TOKEN` | X User Access Token (Read & Write) | X Developer Portal |
| `X_ACCESS_SECRET` | X User Access Token Secret | X Developer Portal |
| `GH_PAT` | GitHub Personal Access Token | Classic PAT with `repo` scope (or fine-grained with Actions Secrets write permissions); required by `refresh-threads-token.yml` to automatically update `THREADS_ACCESS_TOKEN`. |

---

## 🧪 Steps for First Dry Run

You can verify the entire pipeline end-to-end without spending X quota or posting to live feeds.

### Option A: Local Dry Run

1. **Clone and install dependencies:**
   ```bash
   git clone https://github.com/TheKrishJain/Project-X.git
   cd Project-X
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Configure local environment (optional):**
   ```bash
   cp .env.example .env
   # Add your GEMINI_API_KEY to test live LLM generation, or leave blank to test mock fallback
   ```

3. **Execute dry run:**
   ```bash
   # Morning slot (skips jitter sleep):
   python -m src.main --slot morning --dry-run

   # Night slot:
   python -m src.main --slot night --dry-run

   # Test Thursday morning thread logic specifically:
   python -m src.main --slot morning --day thursday --dry-run
   ```

---

### Option B: GitHub Actions Dry Run

1. Go to your repository on GitHub and open the **Actions** tab.
2. Under **Workflows** on the left, click **Auto-Poster**.
3. Click the **Run workflow** dropdown on the right.
4. Select the slot: `morning` or `night`.
5. Check the **Simulate without publishing (Dry Run)** checkbox.
6. Click **Run workflow**.
7. Click the running workflow to view the live execution logs:
   - Topic selection & Gemini generation.
   - Self-critique score & reasoning (target $\ge 7/10$).
   - Regex & character limit validation.
   - Mock posting simulation to Threads and X.
   - History and usage recording.

---

## 🔄 Automated Workflows

### 1. Daily Auto-Poster ([`.github/workflows/post.yml`](file:///.github/workflows/post.yml))
- **Triggers:**
  - `45 3 * * *`: Morning slot (~09:15 AM IST)
  - `15 15 * * *`: Night slot (~08:45 PM IST)
  - `workflow_dispatch`: Manual execution with slot (`morning`/`night`) and `dry_run` inputs.
- **Concurrency Guard:** Uses concurrency group `poster` (`cancel-in-progress: false`) to ensure runs never overlap.
- **Independent Dispatch:** Posting to Threads and X happens independently; a failure on one platform does not block the other.
- **Alerting:** Exits non-zero **only if both platforms fail**, triggering GitHub's automated failure notification email.

### 2. Threads Token Refresh ([`.github/workflows/refresh-threads-token.yml`](file:///.github/workflows/refresh-threads-token.yml))
- **Triggers:** Automatically on the **1st and 15th** of every month (`cron: '0 0 1,15 * *'`).
- Calls Meta Graph API `GET /refresh_access_token?grant_type=th_refresh_token`.
- Automatically updates the `THREADS_ACCESS_TOKEN` secret in the repository using `gh secret set` and `GH_PAT`.

---

## 🛡️ Repository Activity & Workflow Keep-Alive

> [!NOTE]
> GitHub automatically disables scheduled cron workflows in public repositories that experience **60 consecutive days of inactivity** (no git commits).
> 
> In **Auto-Poster**, after every successful morning and night run, [`post.yml`](file:///.github/workflows/post.yml) automatically commits updated [`data/history.json`](file:///c:/Users/msi/Desktop/ProjectX/data/history.json) and [`data/x_usage.json`](file:///c:/Users/msi/Desktop/ProjectX/data/x_usage.json) back to the repository using `[skip ci]`.
> 
> Because new commits are pushed twice every day, your repository is **kept continuously active**, permanently preventing GitHub from pausing or disabling the cron schedules.

---

## 📊 X Budget Guard & Anti-Spam Safeguards

1. **Monthly Hard Cap:**
   [`data/x_usage.json`](file:///c:/Users/msi/Desktop/ProjectX/data/x_usage.json) tracks `{ "month": "YYYY-MM", "count": N }` and resets `count` on calendar month rollover.
   - Enforces `X_MONTHLY_CAP` (default: **85**).
   - If `count >= cap`: skips X posting entirely.
   - If `count + len(x_posts) > cap`: posts only the lead post `x_posts[0]`.

2. **Zero Retries:**
   [`src/post_x.py`](file:///c:/Users/msi/Desktop/ProjectX/src/post_x.py) never retries a `create_tweet` call that fails or times out, preventing accidental duplicate posts on your timeline.

3. **Natural Jitter:**
   Live runs introduce a random sleep between **0 and 35 minutes** (`random.randint(0, 35 * 60)`) to ensure post timestamps vary organically like a human creator.
