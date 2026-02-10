#!/usr/bin/env python3
"""
GitHub Integration for Code Review Agent
Posts review comments to GitHub Pull Requests.

Authentication Methods:
    1. Personal Access Token (comments appear as your username)
       export GITHUB_TOKEN="ghp_your_token"
       
    2. GitHub App (comments appear as "YourApp[bot]")
       export GITHUB_APP_ID="123456"
       export GITHUB_APP_PRIVATE_KEY_PATH="/path/to/private-key.pem"
       export GITHUB_APP_INSTALLATION_ID="12345678"
    
Usage:
    python code_reviewer.py file.py --github owner/repo --pr 123
"""

import os
import json
import re
import time
import base64
from dataclasses import dataclass
from typing import Optional
import urllib.request
import urllib.error


@dataclass
class GitHubConfig:
    """GitHub configuration."""
    token: str
    owner: str
    repo: str
    pr_number: int
    
    @property
    def api_base(self) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.repo}"


def create_jwt(app_id: str, private_key: str) -> str:
    """
    Create a JSON Web Token for GitHub App authentication.
    Uses a simple implementation without external dependencies.
    """
    import hmac
    import hashlib
    
    # Header
    header = {"alg": "RS256", "typ": "JWT"}
    
    # Payload
    now = int(time.time())
    payload = {
        "iat": now - 60,  # Issued 60 seconds ago (clock drift)
        "exp": now + (10 * 60),  # Expires in 10 minutes
        "iss": app_id
    }
    
    def b64url_encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')
    
    header_b64 = b64url_encode(json.dumps(header).encode())
    payload_b64 = b64url_encode(json.dumps(payload).encode())
    
    message = f"{header_b64}.{payload_b64}".encode()
    
    # Sign with RSA - requires the cryptography library or similar
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        
        private_key_obj = serialization.load_pem_private_key(
            private_key.encode(), password=None
        )
        signature = private_key_obj.sign(message, padding.PKCS1v15(), hashes.SHA256())
        signature_b64 = b64url_encode(signature)
        
        return f"{header_b64}.{payload_b64}.{signature_b64}"
    except ImportError:
        raise ImportError(
            "GitHub App authentication requires the 'cryptography' package.\n"
            "Install with: pip install cryptography"
        )


def get_installation_token(app_id: str, private_key: str, installation_id: str) -> str:
    """Get an installation access token for a GitHub App."""
    jwt = create_jwt(app_id, private_key)
    
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    
    request = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "CodeReviewAgent/1.0"
        }
    )
    
    try:
        with urllib.request.urlopen(request) as response:
            data = json.loads(response.read().decode())
            return data["token"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        raise RuntimeError(f"Failed to get installation token: {e.code} - {error_body}")


@dataclass
class GitHubConfig:
    """GitHub configuration."""
    token: str
    owner: str
    repo: str
    pr_number: int
    
    @property
    def api_base(self) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.repo}"


class GitHubClient:
    """Simple GitHub API client using urllib (no dependencies)."""
    
    def __init__(self, config: GitHubConfig):
        self.config = config
        self.headers = {
            "Authorization": f"Bearer {config.token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "CodeReviewAgent/1.0"
        }
    
    def _request(self, method: str, endpoint: str, data: Optional[dict] = None) -> dict:
        """Make an API request."""
        url = f"{self.config.api_base}{endpoint}"
        
        body = json.dumps(data).encode() if data else None
        
        request = urllib.request.Request(url, data=body, headers=self.headers, method=method)
        
        try:
            with urllib.request.urlopen(request) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            raise RuntimeError(f"GitHub API error {e.code}: {error_body}")
    
    def get_pr_info(self) -> dict:
        """Get pull request information."""
        return self._request("GET", f"/pulls/{self.config.pr_number}")
    
    def get_pr_files(self) -> list[dict]:
        """Get list of files changed in the PR."""
        return self._request("GET", f"/pulls/{self.config.pr_number}/files")
    
    def create_review(self, body: str, event: str = "COMMENT", 
                      comments: Optional[list[dict]] = None) -> dict:
        """
        Create a pull request review.
        
        Args:
            body: Overall review comment
            event: APPROVE, REQUEST_CHANGES, or COMMENT
            comments: List of inline comments with path, line, body
        """
        data = {
            "body": body,
            "event": event,
        }
        
        if comments:
            data["comments"] = comments
        
        return self._request("POST", f"/pulls/{self.config.pr_number}/reviews", data)
    
    def create_issue_comment(self, body: str) -> dict:
        """Create a general comment on the PR (not a review)."""
        return self._request("POST", f"/issues/{self.config.pr_number}/comments", {"body": body})
    
    def get_file_diff_positions(self) -> dict[str, dict[int, int]]:
        """
        Map file lines to diff positions for inline comments.
        Returns: {filename: {line_number: diff_position}}
        """
        files = self.get_pr_files()
        positions = {}
        
        for file_info in files:
            filename = file_info["filename"]
            patch = file_info.get("patch", "")
            
            if not patch:
                continue
            
            positions[filename] = {}
            diff_position = 0
            current_line = 0
            
            for line in patch.split("\n"):
                diff_position += 1
                
                # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
                hunk_match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if hunk_match:
                    current_line = int(hunk_match.group(1)) - 1
                    continue
                
                # Lines starting with - are deletions (old file only)
                if line.startswith("-"):
                    continue
                
                # Lines starting with + or space are in the new file
                current_line += 1
                positions[filename][current_line] = diff_position
        
        return positions


def format_review_body(results: list, summary_only: bool = False) -> str:
    """Format review results as a GitHub markdown comment."""
    
    lines = ["## 🤖 Code Review Agent Report\n"]
    
    total_errors = 0
    total_warnings = 0
    total_info = 0
    
    for result in results:
        for finding in result.findings:
            if finding.severity == "error":
                total_errors += 1
            elif finding.severity == "warning":
                total_warnings += 1
            else:
                total_info += 1
    
    # Summary badges
    if total_errors > 0:
        lines.append(f"❌ **{total_errors} Error(s)** ")
    if total_warnings > 0:
        lines.append(f"⚠️ **{total_warnings} Warning(s)** ")
    if total_info > 0:
        lines.append(f"ℹ️ **{total_info} Info** ")
    
    if total_errors == 0 and total_warnings == 0 and total_info == 0:
        lines.append("✅ **No issues found!**")
    
    lines.append("\n")
    
    if summary_only:
        return "\n".join(lines)
    
    # Detailed findings by file
    for result in results:
        if not result.findings:
            continue
        
        lines.append(f"### 📄 `{result.file}`\n")
        
        if result.summary:
            lines.append(f"_{result.summary}_\n")
        
        # Group by severity
        severity_order = {"error": 0, "warning": 1, "info": 2}
        sorted_findings = sorted(result.findings, key=lambda f: severity_order.get(f.severity, 3))
        
        for finding in sorted_findings:
            icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(finding.severity, "•")
            loc = f"**Line {finding.line}:** " if finding.line else ""
            
            lines.append(f"- {icon} `{finding.category}` {loc}{finding.message}")
            if finding.suggestion:
                lines.append(f"  - 💡 _{finding.suggestion}_")
        
        lines.append("")
    
    lines.append("\n---\n_Generated by Code Review Agent_")
    
    return "\n".join(lines)


def build_inline_comments(results: list, diff_positions: dict[str, dict[int, int]], 
                          pr_files: list[str]) -> list[dict]:
    """
    Build inline comments for PR review.
    Only includes comments for lines that are part of the diff.
    """
    comments = []
    
    # Normalize PR file paths for matching
    pr_file_set = set(pr_files)
    
    for result in results:
        # Try to match the file path
        filename = result.file
        
        # Try different path formats
        possible_names = [
            filename,
            os.path.basename(filename),
            filename.lstrip("./"),
        ]
        
        matched_file = None
        for name in possible_names:
            if name in pr_file_set:
                matched_file = name
                break
            # Also check if any PR file ends with this name
            for pr_file in pr_file_set:
                if pr_file.endswith(name) or name.endswith(pr_file):
                    matched_file = pr_file
                    break
        
        if not matched_file or matched_file not in diff_positions:
            continue
        
        file_positions = diff_positions[matched_file]
        
        for finding in result.findings:
            if finding.line and finding.line in file_positions:
                icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(finding.severity, "•")
                
                body = f"{icon} **{finding.category.upper()}**: {finding.message}"
                if finding.suggestion:
                    body += f"\n\n💡 {finding.suggestion}"
                
                comments.append({
                    "path": matched_file,
                    "line": finding.line,  # Use 'line' for single-line comments
                    "body": body
                })
    
    return comments


def post_review_to_github(results: list, config: GitHubConfig, 
                          inline_comments: bool = True) -> dict:
    """
    Post code review results to a GitHub PR.
    
    Args:
        results: List of ReviewResult objects
        config: GitHub configuration
        inline_comments: If True, post inline comments on specific lines
        
    Returns:
        API response from GitHub
    """
    client = GitHubClient(config)
    
    # Get PR info and changed files
    pr_files = client.get_pr_files()
    pr_file_names = [f["filename"] for f in pr_files]
    
    # Determine review event based on findings
    has_errors = any(f.severity == "error" for r in results for f in r.findings)
    event = "REQUEST_CHANGES" if has_errors else "COMMENT"
    
    # Build the review body
    body = format_review_body(results)
    
    # Build inline comments if requested
    comments = []
    if inline_comments:
        diff_positions = client.get_file_diff_positions()
        comments = build_inline_comments(results, diff_positions, pr_file_names)
    
    # Post the review
    try:
        response = client.create_review(body, event, comments if comments else None)
        return {"success": True, "response": response, "inline_comments": len(comments)}
    except Exception as e:
        # Fall back to a simple issue comment if review fails
        print(f"Warning: Could not create PR review ({e}), posting as comment instead")
        response = client.create_issue_comment(body)
        return {"success": True, "response": response, "inline_comments": 0, "fallback": True}


def parse_github_repo(repo_string: str) -> tuple[str, str]:
    """Parse 'owner/repo' string into (owner, repo) tuple."""
    if "/" not in repo_string:
        raise ValueError(f"Invalid repo format: '{repo_string}'. Expected 'owner/repo'")
    
    parts = repo_string.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid repo format: '{repo_string}'. Expected 'owner/repo'")
    
    return parts[0], parts[1]


def get_github_config(repo: str, pr_number: int, token: Optional[str] = None) -> GitHubConfig:
    """Create GitHubConfig from arguments and environment."""
    
    # First, try Personal Access Token
    token = token or os.environ.get("GITHUB_TOKEN")
    
    # If no PAT, try GitHub App authentication
    if not token:
        app_id = os.environ.get("GITHUB_APP_ID")
        private_key_path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")
        private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY")  # Can also pass key directly
        installation_id = os.environ.get("GITHUB_APP_INSTALLATION_ID")
        
        if app_id and installation_id and (private_key_path or private_key):
            # Load private key from file if path provided
            if private_key_path and not private_key:
                with open(private_key_path, 'r') as f:
                    private_key = f.read()
            
            print("Authenticating as GitHub App...")
            token = get_installation_token(app_id, private_key, installation_id)
    
    if not token:
        raise ValueError(
            "GitHub authentication not found. Use one of:\n\n"
            "Option 1 - Personal Access Token:\n"
            "  export GITHUB_TOKEN='ghp_your_token'\n\n"
            "Option 2 - GitHub App (for custom bot name):\n"
            "  export GITHUB_APP_ID='123456'\n"
            "  export GITHUB_APP_PRIVATE_KEY_PATH='/path/to/key.pem'\n"
            "  export GITHUB_APP_INSTALLATION_ID='12345678'\n\n"
            "Create a token at: https://github.com/settings/tokens\n"
            "Create a GitHub App at: https://github.com/settings/apps/new"
        )
    
    owner, repo_name = parse_github_repo(repo)
    
    return GitHubConfig(
        token=token,
        owner=owner,
        repo=repo_name,
        pr_number=pr_number
    )


# CLI for standalone testing
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test GitHub integration")
    parser.add_argument("--repo", required=True, help="Repository (owner/repo)")
    parser.add_argument("--pr", type=int, required=True, help="PR number")
    parser.add_argument("--test", action="store_true", help="Test connection only")
    
    args = parser.parse_args()
    
    config = get_github_config(args.repo, args.pr)
    client = GitHubClient(config)
    
    if args.test:
        pr_info = client.get_pr_info()
        print(f"✓ Connected to: {pr_info['title']}")
        print(f"  State: {pr_info['state']}")
        print(f"  Author: {pr_info['user']['login']}")
        
        files = client.get_pr_files()
        print(f"  Changed files: {len(files)}")
        for f in files[:5]:
            print(f"    - {f['filename']}")
        if len(files) > 5:
            print(f"    ... and {len(files) - 5} more")
