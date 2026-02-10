#!/usr/bin/env python3
"""
Code Review Agent MVP
A minimal viable product for automated code review using Claude API.

Usage:
    python code_reviewer.py <file_or_directory> [--rules rules.yaml]
"""

import argparse
import os
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import json

# For MVP, we'll use a simple approach that can work without the API initially
# Once you have API access, uncomment the anthropic import

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


@dataclass
class ReviewFinding:
    """Represents a single code review finding."""
    file: str
    line: Optional[int]
    severity: str  # "info", "warning", "error"
    category: str  # "documentation", "style", "algorithm", "security", "maintainability"
    message: str
    suggestion: Optional[str] = None


@dataclass
class ReviewResult:
    """Contains all findings for a file."""
    file: str
    findings: list[ReviewFinding] = field(default_factory=list)
    summary: str = ""


def load_rules(rules_path: Optional[str] = None) -> dict:
    """Load review rules from YAML or use defaults."""
    default_rules = {
        "documentation": {
            "enabled": True,
            "require_docstrings": True,
            "require_type_hints": True,
        },
        "style": {
            "enabled": True,
            "max_line_length": 100,
            "max_function_length": 50,
            "max_complexity": 10,
        },
        "algorithms": {
            "enabled": True,
            "flag_nested_loops": True,
            "suggest_builtins": True,
        },
        "security": {
            "enabled": True,
            "check_hardcoded_secrets": True,
            "check_sql_injection": True,
        },
        "maintainability": {
            "enabled": True,
            "max_parameters": 5,
            "flag_global_state": True,
        }
    }
    
    if rules_path and os.path.exists(rules_path):
        try:
            import yaml
            with open(rules_path) as f:
                custom_rules = yaml.safe_load(f)
                # Merge custom rules with defaults
                for category, settings in custom_rules.items():
                    if category in default_rules:
                        default_rules[category].update(settings)
                    else:
                        default_rules[category] = settings
        except ImportError:
            print("Warning: PyYAML not installed. Using default rules.")
        except Exception as e:
            print(f"Warning: Could not load rules file: {e}. Using defaults.")
    
    return default_rules


def get_file_content(filepath: str) -> str:
    """Read file content with error handling."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        raise RuntimeError(f"Could not read file {filepath}: {e}")


def build_review_prompt(code: str, rules: dict, filename: str) -> str:
    """Construct the prompt for Claude."""
    
    rules_text = json.dumps(rules, indent=2)
    
    prompt = f"""You are an expert code reviewer. Analyze the following code and provide specific, actionable feedback.

## Review Rules
```json
{rules_text}
```

## Code to Review
Filename: {filename}

```python
{code}
```

## Instructions
1. Analyze the code against each enabled rule category
2. Identify specific issues with line numbers where possible
3. Provide concrete suggestions for improvement
4. Be constructive and prioritize the most important issues

## Output Format
Respond with a JSON object containing:
{{
    "summary": "Brief overall assessment (2-3 sentences)",
    "findings": [
        {{
            "line": <line_number or null>,
            "severity": "<info|warning|error>",
            "category": "<documentation|style|algorithm|security|maintainability>",
            "message": "Description of the issue",
            "suggestion": "How to fix it (optional)"
        }}
    ]
}}

Only output the JSON, no additional text."""

    return prompt


def extract_json(text: str) -> dict:
    """Extract JSON from Claude's response, handling markdown code blocks."""
    import re
    
    # Try to find JSON in code blocks first
    # Match ```json ... ``` or ``` ... ```
    code_block_pattern = r'```(?:json)?\s*([\s\S]*?)```'
    matches = re.findall(code_block_pattern, text)
    
    if matches:
        # Try each match until we find valid JSON
        for match in matches:
            try:
                return json.loads(match.strip())
            except json.JSONDecodeError:
                continue
    
    # Try parsing the raw text (maybe it's already clean JSON)
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    
    # Try to find JSON object pattern in the text
    json_pattern = r'\{[\s\S]*\}'
    json_match = re.search(json_pattern, text)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    
    # If all else fails, raise an error with helpful info
    raise ValueError(f"Could not parse JSON from response. First 500 chars: {text[:500]}")


def review_with_claude(code: str, rules: dict, filename: str) -> ReviewResult:
    """Send code to Claude API for review."""
    
    if not HAS_ANTHROPIC:
        return review_mock(code, rules, filename)
    
    client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY env var
    
    prompt = build_review_prompt(code, rules, filename)
    
    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    
    result_text = response.content[0].text
    
    # Extract JSON from response (handles markdown code blocks)
    result_data = extract_json(result_text)
    
    findings = [
        ReviewFinding(
            file=filename,
            line=f.get("line"),
            severity=f.get("severity", "info"),
            category=f.get("category", "maintainability"),
            message=f.get("message", ""),
            suggestion=f.get("suggestion")
        )
        for f in result_data.get("findings", [])
    ]
    
    return ReviewResult(
        file=filename,
        findings=findings,
        summary=result_data.get("summary", "")
    )


def review_mock(code: str, rules: dict, filename: str) -> ReviewResult:
    """
    Mock review for testing without API access.
    Uses simple heuristics to demonstrate the structure.
    """
    findings = []
    lines = code.split('\n')
    
    # Simple heuristic checks (these would be replaced by Claude's analysis)
    
    # Check for missing docstrings
    if rules.get("documentation", {}).get("require_docstrings"):
        if 'def ' in code and '"""' not in code and "'''" not in code:
            findings.append(ReviewFinding(
                file=filename,
                line=None,
                severity="warning",
                category="documentation",
                message="Functions appear to be missing docstrings",
                suggestion="Add docstrings describing function purpose, parameters, and return values"
            ))
    
    # Check line length
    max_len = rules.get("style", {}).get("max_line_length", 100)
    for i, line in enumerate(lines, 1):
        if len(line) > max_len:
            findings.append(ReviewFinding(
                file=filename,
                line=i,
                severity="info",
                category="style",
                message=f"Line exceeds {max_len} characters ({len(line)} chars)",
                suggestion="Consider breaking this line for readability"
            ))
    
    # Check for nested loops (simple detection)
    if rules.get("algorithms", {}).get("flag_nested_loops"):
        indent_level = 0
        in_loop = False
        for i, line in enumerate(lines, 1):
            stripped = line.lstrip()
            if stripped.startswith(('for ', 'while ')):
                if in_loop:
                    findings.append(ReviewFinding(
                        file=filename,
                        line=i,
                        severity="warning",
                        category="algorithm",
                        message="Nested loop detected - potential O(n²) complexity",
                        suggestion="Consider if this can be optimized with a different data structure or algorithm"
                    ))
                in_loop = True
            elif stripped and not stripped.startswith('#'):
                # Reset on non-loop, non-comment lines at base indent
                if len(line) - len(stripped) == 0:
                    in_loop = False
    
    # Check for hardcoded secrets (very basic)
    if rules.get("security", {}).get("check_hardcoded_secrets"):
        secret_patterns = ['password', 'api_key', 'secret', 'token']
        for i, line in enumerate(lines, 1):
            lower_line = line.lower()
            if any(p in lower_line and '=' in line for p in secret_patterns):
                if '"' in line or "'" in line:
                    findings.append(ReviewFinding(
                        file=filename,
                        line=i,
                        severity="error",
                        category="security",
                        message="Possible hardcoded secret detected",
                        suggestion="Use environment variables or a secrets manager instead"
                    ))
    
    summary = f"Mock review of {filename}: Found {len(findings)} potential issues."
    if not findings:
        summary = f"Mock review of {filename}: No issues detected with basic heuristics."
    
    return ReviewResult(file=filename, findings=findings, summary=summary)


def format_findings(result: ReviewResult, output_format: str = "text") -> str:
    """Format review results for output."""
    
    if output_format == "json":
        return json.dumps({
            "file": result.file,
            "summary": result.summary,
            "findings": [
                {
                    "line": f.line,
                    "severity": f.severity,
                    "category": f.category,
                    "message": f.message,
                    "suggestion": f.suggestion
                }
                for f in result.findings
            ]
        }, indent=2)
    
    # Text format
    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"Review: {result.file}")
    lines.append(f"{'='*60}")
    lines.append(f"\n{result.summary}\n")
    
    if not result.findings:
        lines.append("✓ No issues found!")
    else:
        # Group by severity
        severity_order = {"error": 0, "warning": 1, "info": 2}
        sorted_findings = sorted(result.findings, key=lambda f: severity_order.get(f.severity, 3))
        
        severity_symbols = {"error": "✗", "warning": "⚠", "info": "ℹ"}
        
        for finding in sorted_findings:
            symbol = severity_symbols.get(finding.severity, "•")
            loc = f"Line {finding.line}: " if finding.line else ""
            lines.append(f"\n{symbol} [{finding.severity.upper()}] [{finding.category}]")
            lines.append(f"  {loc}{finding.message}")
            if finding.suggestion:
                lines.append(f"  → {finding.suggestion}")
    
    lines.append(f"\n{'='*60}\n")
    return '\n'.join(lines)


def find_python_files(path: str) -> list[str]:
    """Find all Python files in a path."""
    path_obj = Path(path)
    
    if path_obj.is_file():
        if path_obj.suffix == '.py':
            return [str(path_obj)]
        else:
            return []
    
    # Directory - find all .py files
    return [str(p) for p in path_obj.rglob('*.py') if not any(
        part.startswith('.') or part == '__pycache__' or part == 'venv'
        for part in p.parts
    )]


def main():
    parser = argparse.ArgumentParser(
        description="Code Review Agent - Automated code review using AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Local review
    python code_reviewer.py my_script.py
    python code_reviewer.py src/ --rules my_rules.yaml
    python code_reviewer.py . --output json
    
    # GitHub PR review (auto-fetch code from PR)
    python code_reviewer.py --github owner/repo --pr 123
    
    # GitHub PR review with local code
    python code_reviewer.py src/ --github owner/repo --pr 123
    
    # Monitor for new PRs (see pr_monitor.py)
    python pr_monitor.py --repo owner/repo
        """
    )
    
    parser.add_argument("path", nargs="?", default=None,
                        help="File or directory to review (optional if using --github --pr)")
    parser.add_argument("--rules", "-r", help="Path to rules YAML file")
    parser.add_argument("--output", "-o", choices=["text", "json"], default="text",
                        help="Output format (default: text)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show verbose output")
    
    # GitHub options
    github_group = parser.add_argument_group("GitHub Integration")
    github_group.add_argument("--github", metavar="OWNER/REPO",
                              help="GitHub repository (e.g., 'octocat/hello-world')")
    github_group.add_argument("--pr", type=int, metavar="NUMBER",
                              help="Pull request number to review")
    github_group.add_argument("--github-token", 
                              help="GitHub token (or set GITHUB_TOKEN env var)")
    github_group.add_argument("--no-inline", action="store_true",
                              help="Don't post inline comments, only summary")
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.github and not args.pr:
        parser.error("--github requires --pr")
    if args.pr and not args.github:
        parser.error("--pr requires --github")
    
    # Determine if we're fetching from GitHub or using local files
    fetch_from_github = args.github and args.pr and not args.path
    
    if not args.path and not fetch_from_github:
        parser.error("Either provide a path or use --github and --pr to fetch from GitHub")
    
    # Validate local path if provided
    if args.path and not os.path.exists(args.path):
        print(f"Error: Path '{args.path}' does not exist")
        sys.exit(1)
    
    # Load rules
    rules = load_rules(args.rules)
    if args.verbose:
        print(f"Loaded rules: {list(rules.keys())}")
    
    # Collect files to review
    all_results = []
    
    if fetch_from_github:
        # Fetch files directly from the PR
        try:
            from github_integration import get_github_config, GitHubClient, post_review_to_github
            
            print(f"Fetching PR #{args.pr} from {args.github}...")
            
            config = get_github_config(args.github, args.pr, args.github_token)
            client = GitHubClient(config)
            
            # Get PR info
            pr_info = client.get_pr_info()
            print(f"PR: {pr_info['title']}")
            print(f"Author: {pr_info['user']['login']}")
            print(f"Branch: {pr_info['head']['ref']} -> {pr_info['base']['ref']}")
            
            # Fetch file contents
            files = client.get_pr_file_contents(python_only=True)
            
            if not files:
                print("No Python files changed in this PR")
                sys.exit(0)
            
            print(f"Found {len(files)} Python file(s) to review\n")
            
            # Review each file
            for file_info in files:
                filename = file_info["filename"]
                if args.verbose:
                    print(f"Reviewing: {filename}")
                
                try:
                    result = review_with_claude(file_info["content"], rules, filename)
                    all_results.append(result)
                    
                    if args.verbose:
                        print(format_findings(result, "text"))
                        
                except Exception as e:
                    print(f"Error reviewing {filename}: {e}")
            
        except ImportError:
            print("Error: github_integration.py not found")
            sys.exit(1)
        except Exception as e:
            print(f"Error fetching from GitHub: {e}")
            sys.exit(1)
    
    else:
        # Use local files
        files = find_python_files(args.path)
        if not files:
            print(f"No Python files found in '{args.path}'")
            sys.exit(0)
        
        if args.verbose:
            print(f"Found {len(files)} file(s) to review")
        
        # Review each file
        for filepath in files:
            if args.verbose:
                print(f"Reviewing: {filepath}")
            
            try:
                code = get_file_content(filepath)
                result = review_with_claude(code, rules, filepath)
                all_results.append(result)
                
                # Output results (unless we're posting to GitHub, then be quieter)
                if not args.github:
                    print(format_findings(result, args.output))
                elif args.verbose:
                    print(format_findings(result, "text"))
                
            except Exception as e:
                print(f"Error reviewing {filepath}: {e}")
    
    # Summary
    total_findings = sum(len(r.findings) for r in all_results)
    errors = sum(1 for r in all_results for f in r.findings if f.severity == "error")
    warnings = sum(1 for r in all_results for f in r.findings if f.severity == "warning")
    
    if len(all_results) > 1 or args.verbose:
        print(f"\n{'='*60}")
        print(f"TOTAL: {len(all_results)} files, {total_findings} findings")
        print(f"       {errors} errors, {warnings} warnings")
        print(f"{'='*60}")
    
    # Post to GitHub if requested
    if args.github and args.pr:
        try:
            from github_integration import get_github_config, post_review_to_github
            
            print(f"\nPosting review to GitHub PR #{args.pr}...")
            
            config = get_github_config(args.github, args.pr, args.github_token)
            result = post_review_to_github(
                all_results, 
                config, 
                inline_comments=not args.no_inline
            )
            
            if result["success"]:
                print(f"✓ Review posted successfully!")
                if result.get("inline_comments", 0) > 0:
                    print(f"  Posted {result['inline_comments']} inline comment(s)")
                if result.get("fallback"):
                    print("  (Posted as issue comment due to API limitations)")
            else:
                print(f"✗ Failed to post review")
                sys.exit(1)
                
        except ImportError:
            print("Error: github_integration.py not found")
            sys.exit(1)
        except Exception as e:
            print(f"Error posting to GitHub: {e}")
            sys.exit(1)
    
    # Exit with error code if there are errors
    if errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
