#!/usr/bin/env python3
"""
PR Monitor Daemon for Code Review Agent
Watches a GitHub repository for new PRs and automatically reviews them.

Usage:
    # Monitor with bot mode (fast)
    python pr_monitor.py --repo owner/repo
    
    # Monitor with agent mode (thorough)
    python pr_monitor.py --repo owner/repo --agent
    
    # Monitor multiple repos
    python pr_monitor.py --repo owner/repo1 --repo owner/repo2 --agent
    
    # Run once (for cron jobs)
    python pr_monitor.py --repo owner/repo --once

Environment Variables:
    ANTHROPIC_API_KEY - Required for Claude API
    GITHUB_TOKEN - Or GitHub App credentials (see github_integration.py)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Import from local modules
from github_integration import GitHubClient, GitHubConfig, get_github_config, parse_github_repo


# State file to track reviewed PRs
DEFAULT_STATE_FILE = Path.home() / ".code_review_agent" / "reviewed_prs.json"


class ReviewState:
    """Track which PRs have been reviewed to avoid duplicates."""
    
    def __init__(self, state_file: Path = DEFAULT_STATE_FILE):
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.reviewed = self._load()
    
    def _load(self) -> dict:
        """Load state from file."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}
    
    def _save(self):
        """Save state to file."""
        with open(self.state_file, "w") as f:
            json.dump(self.reviewed, f, indent=2)
    
    def get_key(self, repo: str, pr_number: int) -> str:
        """Generate a unique key for a PR."""
        return f"{repo}#{pr_number}"
    
    def was_reviewed(self, repo: str, pr_number: int, head_sha: str) -> bool:
        """
        Check if a PR was already reviewed at this commit.
        Returns False if it's a new PR or if the head commit changed.
        """
        key = self.get_key(repo, pr_number)
        if key not in self.reviewed:
            return False
        
        # Check if the head SHA matches (re-review if new commits pushed)
        return self.reviewed[key].get("head_sha") == head_sha
    
    def mark_reviewed(self, repo: str, pr_number: int, head_sha: str, 
                      success: bool = True, error: Optional[str] = None,
                      mode: str = "bot"):
        """Mark a PR as reviewed."""
        key = self.get_key(repo, pr_number)
        self.reviewed[key] = {
            "head_sha": head_sha,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "success": success,
            "error": error,
            "mode": mode
        }
        self._save()
    
    def clear(self, repo: Optional[str] = None):
        """Clear review state (all or for a specific repo)."""
        if repo:
            keys_to_remove = [k for k in self.reviewed if k.startswith(f"{repo}#")]
            for key in keys_to_remove:
                del self.reviewed[key]
        else:
            self.reviewed = {}
        self._save()


def review_pr_bot(repo: str, pr_number: int, verbose: bool = False) -> dict:
    """
    Review a PR using bot mode (fast, linear).
    """
    from code_reviewer import review_with_claude, load_rules
    from github_integration import post_review_to_github
    
    try:
        config = get_github_config(repo, pr_number)
        client = GitHubClient(config)
        
        pr_info = client.get_pr_info()
        if verbose:
            print(f"  PR #{pr_number}: {pr_info['title']}")
        
        files = client.get_pr_file_contents(python_only=True)
        
        if not files:
            if verbose:
                print("  No Python files changed, skipping.")
            return {"success": True, "findings_count": 0, "skipped": True}
        
        rules = load_rules()
        all_results = []
        
        for file_info in files:
            if verbose:
                print(f"    Reviewing: {file_info['filename']}")
            result = review_with_claude(file_info["content"], rules, file_info["filename"])
            all_results.append(result)
        
        post_result = post_review_to_github(all_results, config, inline_comments=True)
        total_findings = sum(len(r.findings) for r in all_results)
        
        return {
            "success": True,
            "findings_count": total_findings,
            "inline_comments": post_result.get("inline_comments", 0),
            "mode": "bot"
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def review_pr_agent(repo: str, pr_number: int, verbose: bool = False) -> dict:
    """
    Review a PR using agent mode (thorough, reasoning).
    """
    try:
        from agent_reviewer import run_agent
        from github_integration import GitHubClient, get_github_config
        
        config = get_github_config(repo, pr_number)
        client = GitHubClient(config)
        
        pr_info = client.get_pr_info()
        if verbose:
            print(f"  PR #{pr_number}: {pr_info['title']}")
            print(f"  🤖 Running agent mode...")
        
        # Note: force=False since we already checked has_existing_review in check_repo_for_prs
        state = run_agent(client, verbose=verbose, force=False)
        
        return {
            "success": state.review_posted,
            "findings_count": len(state.findings),
            "iterations": state.iteration,
            "mode": "agent"
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def review_pr(repo: str, pr_number: int, use_agent: bool = False, verbose: bool = False) -> dict:
    """
    Review a single PR.
    """
    if use_agent:
        return review_pr_agent(repo, pr_number, verbose)
    else:
        return review_pr_bot(repo, pr_number, verbose)


def check_repo_for_prs(repo: str, state: ReviewState, use_agent: bool = False, 
                       verbose: bool = False) -> int:
    """
    Check a repository for open PRs and review any new ones.
    
    Returns:
        Number of PRs reviewed
    """
    owner, repo_name = parse_github_repo(repo)
    
    try:
        config = get_github_config(repo, 1)
        client = GitHubClient(config)
        
        prs = client.get_open_prs()
        
        mode_str = "🤖 agent" if use_agent else "⚡ bot"
        if verbose:
            print(f"\n[{repo}] Found {len(prs)} open PR(s) - using {mode_str} mode")
        
        reviewed_count = 0
        
        for pr in prs:
            pr_number = pr["number"]
            head_sha = pr["head"]["sha"]
            title = pr["title"]
            
            # Check 1: Local state file
            if state.was_reviewed(repo, pr_number, head_sha):
                if verbose:
                    print(f"  PR #{pr_number}: Already reviewed (local state), skipping")
                continue
            
            # Check 2: Verify with GitHub API that we haven't already commented
            pr_config = get_github_config(repo, pr_number)
            pr_client = GitHubClient(pr_config)
            
            existing_review = pr_client.has_existing_review()
            if existing_review.get("has_review"):
                if verbose:
                    print(f"  PR #{pr_number}: Already reviewed on GitHub at {head_sha[:8]}, skipping")
                # Update local state to match
                state.mark_reviewed(
                    repo, pr_number, head_sha,
                    success=True,
                    mode="agent" if use_agent else "bot"
                )
                continue
            
            print(f"\n[{repo}] Reviewing PR #{pr_number}: {title}")
            
            result = review_pr(repo, pr_number, use_agent=use_agent, verbose=verbose)
            
            state.mark_reviewed(
                repo, pr_number, head_sha,
                success=result.get("success", False),
                error=result.get("error"),
                mode="agent" if use_agent else "bot"
            )
            
            if result.get("success") and not result.get("skipped"):
                reviewed_count += 1
                print(f"  ✓ Review posted ({result.get('findings_count', 0)} findings)")
            elif result.get("error"):
                print(f"  ✗ Error: {result.get('error')}")
        
        return reviewed_count
        
    except Exception as e:
        print(f"[{repo}] Error checking for PRs: {e}")
        return 0


def run_monitor(repos: list[str], interval: int = 300, once: bool = False, 
                use_agent: bool = False, verbose: bool = False):
    """
    Main monitoring loop.
    """
    state = ReviewState()
    mode_str = "🤖 Agent" if use_agent else "⚡ Bot"
    
    print("=" * 60)
    print(f"Code Review Agent - PR Monitor ({mode_str} Mode)")
    print("=" * 60)
    print(f"Monitoring {len(repos)} repo(s): {', '.join(repos)}")
    print(f"Check interval: {interval} seconds")
    print(f"State file: {state.state_file}")
    print("=" * 60)
    
    if once:
        total_reviewed = 0
        for repo in repos:
            total_reviewed += check_repo_for_prs(repo, state, use_agent=use_agent, verbose=verbose)
        
        print(f"\n{'=' * 60}")
        print(f"Reviewed {total_reviewed} PR(s)")
        return
    
    print("\nStarting monitor... (Press Ctrl+C to stop)\n")
    
    try:
        while True:
            check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{check_time}] Checking for new PRs...")
            
            total_reviewed = 0
            for repo in repos:
                total_reviewed += check_repo_for_prs(repo, state, use_agent=use_agent, verbose=verbose)
            
            if total_reviewed > 0:
                print(f"\nReviewed {total_reviewed} PR(s) this cycle")
            
            print(f"\nNext check in {interval} seconds...")
            time.sleep(interval)
            
    except KeyboardInterrupt:
        print("\n\nMonitor stopped.")


def main():
    parser = argparse.ArgumentParser(
        description="Monitor GitHub repos for PRs and auto-review them",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Monitor with fast bot mode
    python pr_monitor.py --repo owner/repo
    
    # Monitor with thorough agent mode
    python pr_monitor.py --repo owner/repo --agent
    
    # Monitor multiple repos with agent
    python pr_monitor.py --repo owner/repo1 --repo owner/repo2 --agent -v
    
    # Check once and exit (for cron)
    python pr_monitor.py --repo owner/repo --agent --once
    
    # Custom interval (2 minutes)
    python pr_monitor.py --repo owner/repo --interval 120
    
    # Clear history and re-review all
    python pr_monitor.py --repo owner/repo --clear-state
        """
    )
    
    parser.add_argument("--repo", "-r", action="append", required=True,
                        metavar="OWNER/REPO",
                        help="Repository to monitor (can specify multiple)")
    parser.add_argument("--interval", "-i", type=int, default=300,
                        help="Seconds between PR checks (default: 300)")
    parser.add_argument("--once", action="store_true",
                        help="Run once and exit (for cron jobs)")
    parser.add_argument("--agent", "-a", action="store_true",
                        help="Use agent mode for thorough, reasoning-based reviews")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show detailed output")
    parser.add_argument("--clear-state", action="store_true",
                        help="Clear review history and re-review all PRs")
    
    args = parser.parse_args()
    
    # Validate repos
    for repo in args.repo:
        try:
            parse_github_repo(repo)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)
    
    # Clear state if requested
    if args.clear_state:
        state = ReviewState()
        for repo in args.repo:
            state.clear(repo)
            print(f"Cleared review history for {repo}")
        if not args.once:
            print()
    
    # Check for required environment variables
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY environment variable not set")
        sys.exit(1)
    
    if not (os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_APP_ID")):
        print("Error: GitHub authentication not configured")
        print("Set GITHUB_TOKEN or GitHub App credentials")
        sys.exit(1)
    
    # Run the monitor
    run_monitor(
        repos=args.repo,
        interval=args.interval,
        once=args.once,
        use_agent=args.agent,
        verbose=args.verbose
    )


if __name__ == "__main__":
    main()
