#!/usr/bin/env python3
"""
Agentic Code Review Agent

Unlike the basic bot that follows a fixed script, this agent:
1. Observes - Analyzes the PR context and code changes
2. Reasons - Decides what type of review to perform and what additional context is needed
3. Acts - Gathers context, performs review, self-critiques
4. Iterates - Refines findings until satisfied

The agent uses Claude to make decisions at each step, creating a genuine
reasoning loop rather than a predefined workflow.
"""

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

from github_integration import (
    GitHubClient, GitHubConfig, get_github_config, 
    post_review_to_github, format_review_body
)
from code_reviewer import ReviewResult, ReviewFinding, load_rules


# =============================================================================
# Agent Tools - Actions the agent can take
# =============================================================================

AGENT_TOOLS = [
    {
        "name": "analyze_pr_context",
        "description": "Analyze the PR metadata to understand what kind of changes are being made. Returns PR title, description, labels, file types changed, and size metrics. Use this first to plan your review strategy.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "fetch_changed_files",
        "description": "Fetch the content of files changed in the PR. Can filter by file type.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File extensions to fetch (e.g., ['.py', '.js']). Empty means all files."
                }
            },
            "required": []
        }
    },
    {
        "name": "fetch_related_files",
        "description": "Fetch files that are related to the changed files but not part of the PR. Useful for understanding context like base classes, imports, or test files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Paths to fetch from the repository"
                },
                "reason": {
                    "type": "string",
                    "description": "Why these files are needed for context"
                }
            },
            "required": ["file_paths", "reason"]
        }
    },
    {
        "name": "review_code",
        "description": "Perform a code review on specific files with a given focus area. Returns detailed findings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filenames to review"
                },
                "focus_areas": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["security", "performance", "correctness", "maintainability", "documentation", "testing", "error_handling"]
                    },
                    "description": "What aspects to focus on in this review"
                },
                "context": {
                    "type": "string",
                    "description": "Additional context about what this code does and why"
                }
            },
            "required": ["files", "focus_areas"]
        }
    },
    {
        "name": "self_critique",
        "description": "Review your own findings and filter out noise. Remove obvious suggestions, duplicates, and low-value feedback. Prioritize actionable, specific findings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "The findings to critique"
                },
                "criteria": {
                    "type": "string",
                    "description": "What makes a good finding for this PR"
                }
            },
            "required": ["findings", "criteria"]
        }
    },
    {
        "name": "post_review",
        "description": "Post the final review to GitHub. Only call this when you're satisfied with the findings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Overall summary of the review"
                },
                "findings": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Final list of findings to post"
                },
                "recommendation": {
                    "type": "string",
                    "enum": ["approve", "request_changes", "comment"],
                    "description": "Overall recommendation"
                }
            },
            "required": ["summary", "findings", "recommendation"]
        }
    },
    {
        "name": "finish",
        "description": "End the review process. Call this when the review has been posted or if there's nothing to review.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why the review is complete"
                }
            },
            "required": ["reason"]
        }
    }
]


# =============================================================================
# Agent State
# =============================================================================

@dataclass
class AgentState:
    """Tracks the agent's progress and gathered information."""
    pr_context: Optional[dict] = None
    changed_files: dict = field(default_factory=dict)  # filename -> content
    related_files: dict = field(default_factory=dict)  # filename -> content
    findings: list = field(default_factory=list)
    review_posted: bool = False
    iteration: int = 0
    max_iterations: int = 10
    reasoning_trace: list = field(default_factory=list)
    
    def add_reasoning(self, thought: str):
        """Log a reasoning step."""
        self.reasoning_trace.append({
            "iteration": self.iteration,
            "thought": thought
        })
        print(f"  💭 {thought}")


# =============================================================================
# Tool Implementations
# =============================================================================

class AgentToolExecutor:
    """Executes tools on behalf of the agent."""
    
    def __init__(self, github_client: GitHubClient, state: AgentState, verbose: bool = False):
        self.client = github_client
        self.state = state
        self.verbose = verbose
        self.anthropic_client = anthropic.Anthropic() if HAS_ANTHROPIC else None
    
    def execute(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool and return the result as a string."""
        
        if self.verbose:
            print(f"\n  🔧 Executing: {tool_name}")
            if tool_input:
                print(f"     Input: {json.dumps(tool_input, indent=2)[:200]}...")
        
        method = getattr(self, f"tool_{tool_name}", None)
        if not method:
            return f"Error: Unknown tool '{tool_name}'"
        
        try:
            result = method(tool_input)
            if self.verbose and len(str(result)) < 500:
                print(f"     Result: {result}")
            return result
        except Exception as e:
            return f"Error executing {tool_name}: {str(e)}"
    
    def tool_analyze_pr_context(self, input: dict) -> str:
        """Analyze PR metadata."""
        pr_info = self.client.get_pr_info()
        files = self.client.get_pr_files()
        
        # Categorize files by type
        file_types = {}
        for f in files:
            ext = os.path.splitext(f["filename"])[1] or "no_extension"
            file_types[ext] = file_types.get(ext, 0) + 1
        
        # Calculate metrics
        total_additions = sum(f.get("additions", 0) for f in files)
        total_deletions = sum(f.get("deletions", 0) for f in files)
        
        context = {
            "title": pr_info["title"],
            "description": pr_info.get("body") or "(no description)",
            "author": pr_info["user"]["login"],
            "labels": [l["name"] for l in pr_info.get("labels", [])],
            "base_branch": pr_info["base"]["ref"],
            "head_branch": pr_info["head"]["ref"],
            "file_count": len(files),
            "file_types": file_types,
            "total_additions": total_additions,
            "total_deletions": total_deletions,
            "files_changed": [f["filename"] for f in files],
            "is_draft": pr_info.get("draft", False),
        }
        
        self.state.pr_context = context
        return json.dumps(context, indent=2)
    
    def tool_fetch_changed_files(self, input: dict) -> str:
        """Fetch content of changed files."""
        file_types = input.get("file_types", [".py"])
        
        files = self.client.get_pr_file_contents(python_only=False)
        
        fetched = []
        for f in files:
            filename = f["filename"]
            ext = os.path.splitext(filename)[1]
            
            if file_types and ext not in file_types:
                continue
            
            self.state.changed_files[filename] = f["content"]
            fetched.append({
                "filename": filename,
                "lines": len(f["content"].split("\n")),
                "status": f["status"]
            })
        
        return json.dumps({
            "fetched_count": len(fetched),
            "files": fetched
        }, indent=2)
    
    def tool_fetch_related_files(self, input: dict) -> str:
        """Fetch related files for context."""
        file_paths = input.get("file_paths", [])
        reason = input.get("reason", "")
        
        self.state.add_reasoning(f"Fetching related files: {reason}")
        
        pr_info = self.client.get_pr_info()
        base_ref = pr_info["base"]["sha"]
        
        fetched = []
        for path in file_paths:
            try:
                content = self.client.get_file_content(path, base_ref)
                self.state.related_files[path] = content
                fetched.append({"path": path, "lines": len(content.split("\n"))})
            except Exception as e:
                fetched.append({"path": path, "error": str(e)})
        
        return json.dumps({"fetched": fetched}, indent=2)
    
    def tool_review_code(self, input: dict) -> str:
        """Perform code review with specific focus areas."""
        files_to_review = input.get("files", list(self.state.changed_files.keys()))
        focus_areas = input.get("focus_areas", ["correctness", "maintainability"])
        context = input.get("context", "")
        
        all_findings = []
        
        for filename in files_to_review:
            if filename not in self.state.changed_files:
                continue
            
            code = self.state.changed_files[filename]
            
            # Build context from related files
            related_context = ""
            for rel_file, rel_content in self.state.related_files.items():
                related_context += f"\n\n### Related file: {rel_file}\n```\n{rel_content[:2000]}\n```"
            
            # Build focused review prompt
            prompt = f"""You are an expert code reviewer. Review this code with a focus on: {', '.join(focus_areas)}.

## Context
{context}

## PR Information
{json.dumps(self.state.pr_context, indent=2) if self.state.pr_context else 'No PR context available'}

## Code to Review: {filename}
```python
{code}
```
{related_context}

## Instructions
1. Focus specifically on: {', '.join(focus_areas)}
2. Only report issues that are genuinely important
3. Be specific with line numbers
4. Provide actionable suggestions

## Output Format
Return a JSON array of findings:
[
    {{
        "line": <line_number or null>,
        "severity": "<error|warning|info>",
        "category": "<{focus_areas[0] if focus_areas else 'general'}>",
        "message": "Clear description of the issue",
        "suggestion": "How to fix it"
    }}
]

Only output the JSON array, nothing else."""

            response = self.anthropic_client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}]
            )
            
            try:
                result_text = response.content[0].text
                # Extract JSON from response
                import re
                json_match = re.search(r'\[[\s\S]*\]', result_text)
                if json_match:
                    findings = json.loads(json_match.group())
                    for f in findings:
                        f["file"] = filename
                        all_findings.append(f)
            except Exception as e:
                self.state.add_reasoning(f"Error parsing review for {filename}: {e}")
        
        self.state.findings.extend(all_findings)
        
        return json.dumps({
            "findings_count": len(all_findings),
            "findings": all_findings
        }, indent=2)
    
    def tool_self_critique(self, input: dict) -> str:
        """Self-critique and filter findings."""
        findings = input.get("findings", self.state.findings)
        criteria = input.get("criteria", "actionable, specific, and important")
        
        if not findings:
            return json.dumps({"filtered_findings": [], "removed_count": 0})
        
        prompt = f"""You are reviewing code review feedback before it's posted. 
Filter out low-quality findings and keep only the valuable ones.

## Criteria for good findings:
{criteria}

## Findings to evaluate:
{json.dumps(findings, indent=2)}

## Instructions:
1. Remove obvious/trivial suggestions (like "add a docstring" for simple functions)
2. Remove duplicates or overlapping findings
3. Remove findings that are stylistic preferences rather than real issues
4. Keep findings that identify real bugs, security issues, or significant improvements
5. Prioritize actionable, specific feedback

## Output Format:
Return a JSON object:
{{
    "filtered_findings": [...],  // The findings to keep
    "removed": [...],  // Brief reasons for removed findings
    "quality_assessment": "Overall assessment of the review quality"
}}"""

        response = self.anthropic_client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )
        
        try:
            result_text = response.content[0].text
            import re
            json_match = re.search(r'\{[\s\S]*\}', result_text)
            if json_match:
                result = json.loads(json_match.group())
                self.state.findings = result.get("filtered_findings", findings)
                return json.dumps(result, indent=2)
        except Exception as e:
            self.state.add_reasoning(f"Error in self-critique: {e}")
        
        return json.dumps({"filtered_findings": findings, "error": "Could not parse critique"})
    
    def tool_post_review(self, input: dict) -> str:
        """Post the review to GitHub."""
        summary = input.get("summary", "Code review completed.")
        findings = input.get("findings", self.state.findings)
        recommendation = input.get("recommendation", "comment")
        
        # Convert findings to ReviewResult format
        review_results = []
        files_findings = {}
        
        for f in findings:
            filename = f.get("file", "unknown")
            if filename not in files_findings:
                files_findings[filename] = []
            files_findings[filename].append(ReviewFinding(
                file=filename,
                line=f.get("line"),
                severity=f.get("severity", "info"),
                category=f.get("category", "general"),
                message=f.get("message", ""),
                suggestion=f.get("suggestion")
            ))
        
        for filename, file_findings in files_findings.items():
            review_results.append(ReviewResult(
                file=filename,
                findings=file_findings,
                summary=summary
            ))
        
        # Post to GitHub
        result = post_review_to_github(
            review_results,
            self.client.config,
            inline_comments=True
        )
        
        self.state.review_posted = True
        
        return json.dumps({
            "success": result.get("success", False),
            "inline_comments": result.get("inline_comments", 0),
            "summary": summary,
            "findings_count": len(findings)
        }, indent=2)
    
    def tool_finish(self, input: dict) -> str:
        """Mark the review as complete."""
        reason = input.get("reason", "Review complete")
        self.state.add_reasoning(f"Finishing: {reason}")
        return json.dumps({"status": "complete", "reason": reason})


# =============================================================================
# Agent Loop
# =============================================================================

def run_agent(github_client: GitHubClient, verbose: bool = False) -> AgentState:
    """
    Run the agentic review loop.
    
    The agent will:
    1. Analyze the PR context
    2. Decide what to focus on
    3. Gather necessary context
    4. Perform the review
    5. Self-critique and refine
    6. Post the final review
    """
    
    if not HAS_ANTHROPIC:
        raise RuntimeError("Anthropic API required for agentic mode")
    
    state = AgentState()
    executor = AgentToolExecutor(github_client, state, verbose=verbose)
    client = anthropic.Anthropic()
    
    # Initial system prompt
    system_prompt = """You are an expert code review agent. Your goal is to provide valuable, 
actionable code review feedback on a GitHub Pull Request.

You have access to tools to:
1. Analyze the PR context (always do this first)
2. Fetch changed files and related files for context
3. Perform focused code reviews
4. Self-critique your findings to remove noise
5. Post the final review

## Your Process:
1. First, analyze the PR context to understand what's being changed
2. Based on the context, decide what to focus on (security for auth code, performance for data processing, etc.)
3. If needed, fetch related files for context (imports, base classes, tests)
4. Perform a focused review
5. Self-critique to remove low-value findings
6. Post the review with a clear summary and recommendation

## Important Guidelines:
- Quality over quantity - fewer, better findings
- Be specific with line numbers and suggestions
- Consider the context - a prototype PR needs different feedback than a production PR
- Don't be pedantic about style unless it affects readability
- Praise good code too, not just problems

Think step by step about what to do next. After each tool result, reason about what you learned and what to do next."""

    messages = [
        {"role": "user", "content": "Please review the Pull Request. Start by analyzing the PR context."}
    ]
    
    print("\n" + "=" * 60)
    print("🤖 Agentic Code Review - Starting")
    print("=" * 60)
    
    while state.iteration < state.max_iterations:
        state.iteration += 1
        print(f"\n📍 Iteration {state.iteration}")
        
        # Call Claude with tools
        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4096,
            system=system_prompt,
            tools=AGENT_TOOLS,
            messages=messages
        )
        
        # Process response
        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})
        
        # Check for tool use
        tool_uses = [block for block in assistant_content if block.type == "tool_use"]
        
        if not tool_uses:
            # No tool use - agent is thinking out loud
            for block in assistant_content:
                if hasattr(block, "text"):
                    state.add_reasoning(block.text[:200])
            continue
        
        # Execute tools and gather results
        tool_results = []
        should_finish = False
        
        for tool_use in tool_uses:
            tool_name = tool_use.name
            tool_input = tool_use.input
            
            result = executor.execute(tool_name, tool_input)
            
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result
            })
            
            if tool_name == "finish":
                should_finish = True
        
        messages.append({"role": "user", "content": tool_results})
        
        if should_finish:
            break
        
        # Check if review was posted
        if state.review_posted:
            print("\n✅ Review posted successfully!")
            break
    
    # Print summary
    print("\n" + "=" * 60)
    print("📊 Agent Summary")
    print("=" * 60)
    print(f"Iterations: {state.iteration}")
    print(f"Files reviewed: {len(state.changed_files)}")
    print(f"Related files fetched: {len(state.related_files)}")
    print(f"Final findings: {len(state.findings)}")
    print(f"Review posted: {state.review_posted}")
    
    if verbose and state.reasoning_trace:
        print("\n📝 Reasoning Trace:")
        for step in state.reasoning_trace:
            print(f"  [{step['iteration']}] {step['thought']}")
    
    return state


# =============================================================================
# CLI
# =============================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Agentic Code Review - AI agent that reasons about code reviews",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Review a PR with the agent
    python agent_reviewer.py --github owner/repo --pr 123
    
    # Verbose mode to see reasoning
    python agent_reviewer.py --github owner/repo --pr 123 -v
        """
    )
    
    parser.add_argument("--github", required=True, metavar="OWNER/REPO",
                        help="GitHub repository")
    parser.add_argument("--pr", type=int, required=True,
                        help="Pull request number")
    parser.add_argument("--github-token", help="GitHub token")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show detailed reasoning")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't post to GitHub, just show what would be posted")
    
    args = parser.parse_args()
    
    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY environment variable required")
        sys.exit(1)
    
    # Setup GitHub client
    config = get_github_config(args.github, args.pr, args.github_token)
    client = GitHubClient(config)
    
    # Run the agent
    try:
        state = run_agent(client, verbose=args.verbose)
        
        if not state.review_posted:
            print("\n⚠️  Review was not posted (agent may have encountered an issue)")
            sys.exit(1)
            
    except Exception as e:
        print(f"\n❌ Agent error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
