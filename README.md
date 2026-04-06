English | [한국어](README.ko.md)

# git-study

![git-study demo](assets/demo_hq.gif)

> A terminal learning tool that turns Git commits into **code comprehension quizzes**.

Why was this changed? What are the tradeoffs? It asks the questions that are hard to answer from a diff alone.

---

## Features

- **Inline quizzes** — questions anchored to the exact code location
- **4 question types** — `intent` · `behavior` · `tradeoff` · `vulnerability`
- **All in one screen** — answer → grade → feedback
- **Works anywhere** — local repos + GitHub URLs
- **Two UIs** — Textual TUI (v2) + Streamlit web app

---

## Quick Start

**Requirements**: Python 3.13+, [uv](https://docs.astral.sh/uv/), OpenAI API key

**Step 1. Install uv (once)**

macOS / Linux

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows (PowerShell)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Step 2. Install git-study**

```bash
uv tool install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  git-study
```

**Step 3. Set your API key (once)**

```bash
cd /path/to/your/repo   # go to your project root (where .git lives)
git-study               # launch the app
/apikey set sk-...      # enter inside the app (saved permanently)
```

**Step 4. Start learning**

```
/commits      # select commit range
/quiz         # generate quiz
/grade        # grade your answers
```

Use `Shift+Tab` to switch between chat and code view.

> **Tip.** Run `/hook on` once to install a git post-commit hook.  
> After that, every time you commit, git-study will launch automatically and generate a quiz for that commit.

---

## Commands

| Command | Description |
|---------|-------------|
| `/commits` | Select commit range |
| `/quiz [range] [count] [--ai\|--others]` | Generate quiz |
| `/quiz list` | List quiz sessions |
| `/quiz clear` | Delete quiz for current range |
| `/quiz retry` | Reset answers and retake (keeps questions & grades) |
| `/grade` | Grade answers |
| `/answer` | Re-enter answer mode for last question |
| `/review [range]` | Explain commits |
| `/map [--full] [--refresh]` | Repository structure map |
| `/clear` | Reset conversation |
| `/resume` | Resume previous conversation |
| `/repo [path\|URL]` | Switch repository |
| `/apikey` | Manage API key |
| `/model <id>` | Change model |
| `/hook on\|off` | Install/uninstall git post-commit hook |
| `/exit` | Quit |
| `?` | Help |

---

## Data Storage

| Path | Contents |
|------|----------|
| `<repo>/.git-study/state.json` | Selected commit range |
| `<repo>/.git-study/sessions/` | Quiz sessions (questions · answers · grades) |
| `~/.git-study/settings.json` | Global settings |
| `~/.git-study/secrets.json` | API key |

When using a GitHub URL or running outside a repo, data is stored in `~/.git-study/`.
