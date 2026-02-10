# Code Review Agent

An AI-powered code review tool that analyzes Python codebases for documentation gaps, style issues, algorithm improvements, security vulnerabilities, and maintainability concerns.

## Quick Start

```bash
# Review a single file
python code_reviewer.py my_script.py

# Review an entire directory
python code_reviewer.py src/

# Use custom rules
python code_reviewer.py . --rules rules.yaml

# Output as JSON (for CI integration)
python code_reviewer.py src/ --output json
```

## GitHub Integration

Post review comments directly to GitHub Pull Requests:

```bash
# Auto-fetch code from PR (no local files needed!)
python code_reviewer.py --github owner/repo --pr 123

# With local code (for testing before pushing)
python code_reviewer.py src/ --github myorg/myrepo --pr 45

# Summary only (no inline comments)
python code_reviewer.py --github owner/repo --pr 123 --no-inline
```

### Monitor for New PRs

Automatically watch repositories and review new PRs as they're opened:

```bash
# Start the monitor daemon
python pr_monitor.py --repo owner/repo

# Monitor multiple repos
python pr_monitor.py --repo owner/repo1 --repo owner/repo2

# Custom check interval (2 minutes)
python pr_monitor.py --repo owner/repo --interval 120

# Run once and exit (for cron jobs)
python pr_monitor.py --repo owner/repo --once

# Verbose output
python pr_monitor.py --repo owner/repo -v
```

The monitor:
- Checks for open PRs at regular intervals
- Tracks which PRs have been reviewed (stored in `~/.code_review_agent/reviewed_prs.json`)
- Re-reviews PRs when new commits are pushed
- Skips PRs with no Python files

### GitHub Setup

You have two options for authentication:

#### Option A: Personal Access Token (Quick Setup)
Comments will appear as **your username**.

1. Go to [github.com/settings/tokens](https://github.com/settings/tokens)
2. Click "Generate new token (classic)"
3. Select scope: `repo`
4. Set the token:
   ```bash
   export GITHUB_TOKEN="ghp_your_token_here"
   ```

#### Option B: GitHub App (Custom Bot Name)
Comments will appear as **CodeReviewAgent[bot]** (or whatever you name your app).

1. **Create the App** at [github.com/settings/apps/new](https://github.com/settings/apps/new):
   - Name: `CodeReviewAgent` (or your preferred name)
   - Homepage URL: any URL (can be your repo)
   - Uncheck "Webhook → Active"
   - Permissions:
     - Pull requests: **Read & Write**
     - Contents: **Read**
   - Click "Create GitHub App"

2. **Get your App ID** from the app's settings page (shown near the top)

3. **Generate a Private Key:**
   - Scroll to "Private keys" section
   - Click "Generate a private key"
   - Save the downloaded `.pem` file securely

4. **Install the App:**
   - Go to your app's settings → "Install App"
   - Install on your repository (or all repositories)
   - Note the Installation ID from the URL: `github.com/settings/installations/INSTALLATION_ID`

5. **Set environment variables:**
   ```bash
   export GITHUB_APP_ID="123456"
   export GITHUB_APP_PRIVATE_KEY_PATH="/path/to/your-app.private-key.pem"
   export GITHUB_APP_INSTALLATION_ID="12345678"
   ```

6. **Install the cryptography package:**
   ```bash
   pip install cryptography
   ```

7. **Run the review:**
   ```bash
   python code_reviewer.py src/ --github owner/repo --pr 123
   ```

### What Gets Posted

- **Summary comment** with counts of errors, warnings, and info messages
- **Inline comments** on specific lines that are part of the PR diff
- **Review status**: "Request Changes" if errors found, "Comment" otherwise

### Example Output on GitHub

The review will appear as:

> ## 🤖 Code Review Agent Report
> 
> ❌ **2 Error(s)** ⚠️ **3 Warning(s)** ℹ️ **1 Info**
> 
> ### 📄 `src/utils.py`
> 
> - ❌ `security` **Line 42:** Possible hardcoded secret detected
>   - 💡 _Use environment variables or a secrets manager instead_
> - ⚠️ `algorithm` **Line 15:** Nested loop detected - potential O(n²) complexity
>   - 💡 _Consider if this can be optimized with a different data structure_

## Current Features (MVP)

- Single file and directory scanning
- Configurable rules via YAML
- Mock review mode (works without API)
- Basic heuristic checks:
  - Missing docstrings
  - Line length violations
  - Nested loop detection
  - Hardcoded secrets detection
- Text and JSON output formats

## Setup

### Prerequisites

```bash
pip install pyyaml  # For YAML config support
```

### Enable Claude API (Optional)

1. Get an API key from [console.anthropic.com](https://console.anthropic.com)
2. Set the environment variable:
   ```bash
   export ANTHROPIC_API_KEY="your-key-here"
   ```
3. Install the SDK:
   ```bash
   pip install anthropic
   ```
4. In `code_reviewer.py`, set `HAS_ANTHROPIC = True` and uncomment the API code

---

## Expansion Roadmap

### Phase 1: Enhanced Local Analysis (No API Required)
**Goal:** Make the tool useful even without API access

- [ ] **AST-based analysis** - Use Python's `ast` module for accurate parsing
  - Proper function/class detection
  - Accurate line numbers
  - Import analysis
  - Complexity calculation (cyclomatic)
  
- [ ] **More heuristic checks:**
  - Unused imports
  - Unused variables
  - Duplicate code detection (simple)
  - TODO/FIXME tracking
  
- [ ] **Type hint validation** - Check for missing type annotations

**Files to create:**
```
analyzers/
    __init__.py
    ast_analyzer.py    # AST-based code parsing
    complexity.py      # Cyclomatic complexity
    security.py        # Security pattern matching
```

---

### Phase 2: Full Claude Integration
**Goal:** Leverage Claude for intelligent, context-aware review

- [ ] **API integration** - Uncomment and test Claude API calls
- [ ] **Chunking strategy** - Handle large files (split by function/class)
- [ ] **Context window management** - Stay within token limits
- [ ] **Caching** - Don't re-review unchanged files
- [ ] **Cost tracking** - Monitor API usage

**Key code changes:**
```python
# Add caching
import hashlib
import json
from pathlib import Path

CACHE_DIR = Path(".code_review_cache")

def get_cache_key(code: str, rules: dict) -> str:
    content = code + json.dumps(rules, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()

def check_cache(cache_key: str) -> Optional[ReviewResult]:
    cache_file = CACHE_DIR / f"{cache_key}.json"
    if cache_file.exists():
        # Return cached result
        pass
```

---

### Phase 3: Git Integration
**Goal:** Focus reviews on what changed

- [ ] **Diff-based review** - Only review changed lines
- [ ] **Pre-commit hook** - Review before commits
- [ ] **PR review mode** - Compare branches
- [ ] **Blame-aware context** - Show who wrote problematic code

**New CLI options:**
```bash
# Review only staged changes
python code_reviewer.py --staged

# Review changes since last commit
python code_reviewer.py --diff HEAD~1

# Compare branches
python code_reviewer.py --compare main feature-branch
```

**Files to create:**
```
git_integration/
    __init__.py
    diff_parser.py     # Parse git diffs
    hooks.py           # Pre-commit hook setup
```

---

### Phase 4: CI/CD Integration
**Goal:** Automate reviews in your pipeline

- [ ] **GitHub Action** - Run on PRs automatically
- [ ] **Exit codes** - Fail builds on errors
- [ ] **PR comments** - Post findings as review comments
- [ ] **Status checks** - Block merges on critical issues
- [ ] **Baseline mode** - Only flag new issues

**Create GitHub Action:**
```yaml
# .github/workflows/code-review.yml
name: Code Review
on: [pull_request]
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python code_reviewer.py src/ --output json > review.json
      - name: Post Review Comments
        run: python scripts/post_github_comments.py review.json
```

---

### Phase 5: Advanced Analysis
**Goal:** Deeper, more valuable insights

- [ ] **Cross-file analysis** - Track dependencies, find unused exports
- [ ] **Historical tracking** - Trend analysis over time
- [ ] **Learning from feedback** - Remember suppressed warnings
- [ ] **Custom rules engine** - Define patterns in YAML/regex
- [ ] **Multi-language support** - JavaScript, TypeScript, C++

**Example custom rule:**
```yaml
custom:
  enabled: true
  patterns:
    - name: deprecated_api
      pattern: "requests\\.get\\("
      message: "Use httpx instead of requests for async support"
      severity: info
      
    - name: company_standard
      pattern: "print\\("
      message: "Use logging module instead of print statements"
      severity: warning
```

---

### Phase 6: Team Features
**Goal:** Make it useful for teams

- [ ] **Shared rule configs** - Pull from central repo
- [ ] **Metrics dashboard** - Track code quality over time
- [ ] **Team baselines** - Different standards per project
- [ ] **Review assignments** - Route issues to owners
- [ ] **Suppression comments** - `# noqa: DOC001`

---

## Project Structure (Target)

```
code_review_agent/
├── code_reviewer.py       # Main CLI entry point
├── rules.yaml             # Default rules
├── requirements.txt
├── README.md
│
├── analyzers/             # Analysis modules
│   ├── __init__.py
│   ├── ast_analyzer.py
│   ├── complexity.py
│   ├── documentation.py
│   ├── security.py
│   └── style.py
│
├── integrations/          # External integrations
│   ├── __init__.py
│   ├── claude_client.py   # API wrapper with caching
│   ├── git_integration.py
│   └── github_action.py
│
├── output/                # Output formatters
│   ├── __init__.py
│   ├── text.py
│   ├── json_format.py
│   ├── markdown.py
│   └── github_comments.py
│
└── tests/                 # Test suite
    ├── test_analyzers.py
    ├── test_integration.py
    └── fixtures/
        └── sample_code.py
```

---

## Next Steps

1. **Run the MVP** on one of your existing Python projects
2. **Identify gaps** - What issues does it miss that you care about?
3. **Pick one Phase 1 item** - I recommend AST analysis first
4. **Iterate** - Add features based on what you actually need

---

## Example Output

```
============================================================
Review: example.py
============================================================

Found 3 potential issues.

✗ [ERROR] [security]
  Line 42: Possible hardcoded secret detected
  → Use environment variables or a secrets manager instead

⚠ [WARNING] [algorithm]
  Line 15: Nested loop detected - potential O(n²) complexity
  → Consider if this can be optimized with a different data structure

⚠ [WARNING] [documentation]
  Functions appear to be missing docstrings
  → Add docstrings describing function purpose, parameters, and return values

============================================================
```
