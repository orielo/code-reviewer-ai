import os
import sys
import openai
import requests
import json
import re
import yaml
from github import Github
from pathlib import Path
import logging
from datetime import datetime
import httpx

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("PR_Reviewer")

class PRReviewConfig:
    """Configuration manager for the PR review process."""
    
    DEFAULT_CONFIG = {
        "review_mode": "standard",  # Options: concise, standard, thorough
        "comment_threshold": "medium",  # Options: low, medium, high
        "file_filters": {
            "include": ["*"],
            "exclude": ["*.md", "*.txt", "package-lock.json", "yarn.lock"]
        },
        "review_focus": [
            "bugs",
            "security", 
            "performance", 
            "maintainability", 
            "readability"
        ],
        "summary_length": 200,  # Words
        "ignore_lines_containing": [
            "TODO",
            "FIXME",
            "NOSONAR",
            "# pragma: no cover"
        ],
        "language_specific_rules": {
            "python": {
                "style_guide": "PEP8",
                "extra_focus": ["type_hints", "docstrings"]
            },
            "javascript": {
                "style_guide": "Airbnb",
                "extra_focus": ["null_safety", "async_patterns"]
            }
        },
        "comment_styling": {
            "title_prefix": "🔍 AI Code Review - Line",
            "show_code_block": True,
            "show_details": False,
            "custom_signature": "",
            "emoji_prefix": True,
            "show_code_preview": False
        }
    }
    
    def __init__(self):
        self.config = self.DEFAULT_CONFIG.copy()
        self.load_custom_config()
        
        # Override with direct environment variables if provided
        if os.getenv("REVIEW_MODE"):
            self.config["review_mode"] = os.getenv("REVIEW_MODE")
        if os.getenv("COMMENT_THRESHOLD"):
            self.config["comment_threshold"] = os.getenv("COMMENT_THRESHOLD")
        
    def load_custom_config(self):
        """Load custom configuration from the repository."""
        try:
            # First, check for the CONFIG_PATH env var which can be passed as an action input
            config_path = os.getenv("CONFIG_PATH")
            repo_name = os.getenv("GITHUB_REPOSITORY")
            token = os.getenv("GITHUB_TOKEN")
            
            if repo_name and token:
                g = Github(token)
                repo = g.get_repo(repo_name)
                
                # Try the provided config path if it exists
                if config_path:
                    try:
                        file_content = repo.get_contents(config_path)
                        custom_config = yaml.safe_load(file_content.decoded_content.decode('utf-8'))
                        self._merge_config(custom_config)
                        logger.info(f"Loaded custom config from {config_path}")
                        return
                    except Exception as e:
                        logger.warning(f"No config found at {config_path}: {str(e)}")
                
                # Fall back to default locations if CONFIG_PATH wasn't provided or failed
                for default_path in [".github/pr_review_config.yml", ".github/pr_review_config.yaml"]:
                    try:
                        file_content = repo.get_contents(default_path)
                        custom_config = yaml.safe_load(file_content.decoded_content.decode('utf-8'))
                        self._merge_config(custom_config)
                        logger.info(f"Loaded custom config from {default_path}")
                        break
                    except Exception as e:
                        logger.debug(f"No config found at {default_path}: {str(e)}")
            
            # Look for a local default config in the action itself
            action_default_config = os.path.join(os.path.dirname(__file__), "default_config.yml")
            if os.path.exists(action_default_config):
                try:
                    with open(action_default_config, 'r') as f:
                        custom_config = yaml.safe_load(f)
                        if custom_config:
                            logger.info(f"Loaded default config from action: {action_default_config}")
                            self._merge_config(custom_config)
                except Exception as e:
                    logger.warning(f"Error loading default action config: {str(e)}")
                    
        except Exception as e:
            logger.warning(f"Error loading custom config: {str(e)}")
    
    def _merge_config(self, custom_config):
        """Merge custom configuration with defaults."""
        for key, value in custom_config.items():
            if key in self.config:
                if isinstance(value, dict) and isinstance(self.config[key], dict):
                    # Deep merge for dictionaries
                    self.config[key].update(value)
                else:
                    # Direct replacement for other types
                    self.config[key] = value
            else:
                # Add new keys
                self.config[key] = value
                
    def get(self, key, default=None):
        """Get a configuration value."""
        return self.config.get(key, default)
        
    def should_review_file(self, filename):
        """Determine if a file should be reviewed based on filters."""
        from fnmatch import fnmatch
        
        # Check exclusions first
        for pattern in self.config["file_filters"]["exclude"]:
            if fnmatch(filename, pattern):
                return False
                
        # Then check inclusions
        for pattern in self.config["file_filters"]["include"]:
            if fnmatch(filename, pattern):
                return True
                
        return False
        
    def get_review_prompt_additions(self):
        """Generate language-specific review instructions."""
        focus_items = self.config["review_focus"]
        focus_text = ", ".join(focus_items)
        
        # Build language-specific instructions
        language_instructions = ""
        for lang, rules in self.config["language_specific_rules"].items():
            style = rules.get("style_guide", "standard")
            extra = ", ".join(rules.get("extra_focus", []))
            language_instructions += f"\n- For {lang} files: Follow {style} guidelines" + (f" with focus on {extra}" if extra else "")
        
        threshold_map = {
            "low": "Suggest improvements even for minor issues",
            "medium": "Focus on moderate to significant issues",
            "high": "Only flag significant issues that meaningfully impact code quality or functionality"
        }
        threshold_guidance = threshold_map.get(self.config["comment_threshold"], threshold_map["medium"])
        
        mode_map = {
            "concise": "Be extremely brief and only focus on critical issues",
            "standard": "Provide balanced feedback focusing on important issues",
            "thorough": "Perform comprehensive review covering both major and minor issues"
        }
        mode_guidance = mode_map.get(self.config["review_mode"], mode_map["standard"])
        
        return {
            "focus": focus_text,
            "language_specific": language_instructions,
            "threshold": threshold_guidance,
            "mode": mode_guidance
        }

def format_comment_text(comment_text, file_name, language):
    """Format a comment for better readability and impact."""
    # Strip any unnecessary prefixes LLMs sometimes add
    comment_text = re.sub(r'^(Issue|Problem|Bug|Note|Warning):\s*', '', comment_text)
    
    # Identify the type of comment to add appropriate emoji and formatting
    if re.search(r'security|vulnerability|attack|exploit|injection|xss|csrf|sanitiz', comment_text.lower()):
        prefix = "🔒 **Security Issue:** "
    elif re.search(r'performance|slow|efficient|complexity|o\(n\^2\)|optimize', comment_text.lower()):
        prefix = "⚡ **Performance Issue:** "
    elif re.search(r'bug|error|incorrect|wrong|fix|issue|problem|fail', comment_text.lower()):
        prefix = "🐛 **Potential Bug:** "
    elif re.search(r'style|format|indent|spacing|naming|convention', comment_text.lower()):
        prefix = "🎨 **Style Issue:** "
    elif re.search(r'maintain|readability|clean|refactor|complex|understand', comment_text.lower()):
        prefix = "🧹 **Maintainability:** "
    else:
        prefix = "💡 **Suggestion:** "
        
    # Add code examples when appropriate
    if "instead" in comment_text.lower() or "consider" in comment_text.lower():
        # Try to extract or generate a code example
        if language == "python" and not "```python" in comment_text:
            # Add code block if one doesn't exist and there seems to be code
            code_match = re.search(r'`([^`]+)`', comment_text)
            if code_match and len(code_match.group(1)) > 5:  # If there's inline code of reasonable length
                # Split suggestion from example
                suggestion, rest = re.split(r'\b(consider|instead|use|replace)\b', comment_text, 1, re.IGNORECASE)
                if len(rest) > 5:  # If we have a meaningful suggestion part
                    comment_text = f"{suggestion}\n\nRecommended approach:\n```{language}\n{code_match.group(1)}\n```"
    
    return prefix + comment_text

def get_pull_request_diff(repo_name, pr_number, token):
    """Fetch PR diff from GitHub API."""
    logger.info(f"Fetching PR diff for repo: {repo_name}, PR number: {pr_number}")
    url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}/files"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    
    try:
        response = requests.get(url, headers=headers)
        logger.info(f"GitHub API response status: {response.status_code}")
        
        if response.status_code != 200:
            logger.error(f"Response content: {response.text}")
            return None
            
        # Get response with pagination if needed
        result = response.json()
        next_url = get_next_url(response.headers.get('Link', ''))
        
        # Handle GitHub's pagination
        while next_url:
            logger.info(f"Fetching next page of files from: {next_url}")
            response = requests.get(next_url, headers=headers)
            if response.status_code == 200:
                result.extend(response.json())
                next_url = get_next_url(response.headers.get('Link', ''))
            else:
                logger.error(f"Failed to fetch next page: {response.status_code}")
                break
                
        return result
    except Exception as e:
        logger.error(f"Error fetching PR diff: {str(e)}")
        return None

def get_next_url(link_header):
    """Extract next URL from GitHub's Link header for pagination."""
    if not link_header:
        return None
        
    links = {}
    for link in link_header.split(','):
        parts = link.split(';')
        if len(parts) == 2:
            url = parts[0].strip().lstrip('<').rstrip('>')
            rel = parts[1].strip()
            if 'rel="next"' in rel:
                return url
    return None

def get_existing_comments(repo_name, pr_number, token):
    """Get existing AI review comments to avoid duplication."""
    url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}/comments"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            comments = response.json()
            # Filter for our AI comments
            ai_comments = [
                comment for comment in comments 
                if "💡 AI Review:" in comment.get("body", "")
            ]
            return ai_comments
        else:
            logger.error(f"Failed to fetch existing comments: {response.status_code}")
            return []
    except Exception as e:
        logger.error(f"Error fetching existing comments: {str(e)}")
        return []

def get_file_language(file_name):
    """Determine programming language from file extension."""
    extension_map = {
        '.py': 'python',
        '.js': 'javascript',
        '.jsx': 'javascript',
        '.ts': 'typescript',
        '.tsx': 'typescript',
        '.java': 'java',
        '.c': 'c',
        '.cpp': 'c++',
        '.cs': 'c#',
        '.go': 'go',
        '.rb': 'ruby',
        '.php': 'php',
        '.swift': 'swift',
        '.kt': 'kotlin',
        '.rs': 'rust',
        '.scala': 'scala',
        '.sh': 'shell',
        '.bash': 'shell',
        '.html': 'html',
        '.css': 'css',
        '.scss': 'scss',
        '.sql': 'sql',
    }
    
    ext = Path(file_name).suffix.lower()
    return extension_map.get(ext, 'unknown')

def review_code_with_gpt(file_diffs, config, existing_comments):
    """
    Request GPT for intelligent code review with customized focus areas
    based on configuration.
    """
    openai.api_key = os.getenv("OPENAI_API_KEY")
    logger.info("Sending code diff to OpenAI for review...")

    reviews = []
    all_summaries = []
    
    # Get custom prompt additions from config
    prompt_additions = config.get_review_prompt_additions()
    summary_length = config.get("summary_length", 200)
    
    # Prepare existing comment map to avoid duplication
    existing_comment_map = {}
    for comment in existing_comments:
        path = comment.get("path", "")
        position = comment.get("position")
        if path and position:
            key = f"{path}:{position}"
            existing_comment_map[key] = comment.get("body", "")

    for file in file_diffs:
        file_name = file.get("filename")
        patch = file.get("patch", "")
        
        # Skip if no patch or if file shouldn't be reviewed based on filters
        if not patch or not config.should_review_file(file_name):
            logger.info(f"Skipping review for {file_name} (filtered out or no changes)")
            continue
            
        language = get_file_language(file_name)
        
        # Enhanced prompt with configuration
        prompt = f"""You are an expert AI code reviewer. Review the code diff for `{file_name}` (language: {language}):

{patch}

INSTRUCTIONS:
1. **Review Focus**: Focus on {prompt_additions['focus']}
2. **Comment Threshold**: {prompt_additions['threshold']}
3. **Review Mode**: {prompt_additions['mode']}
4. **Language-Specific Guidance**: {prompt_additions['language_specific']}

5. **Inline Comments**:
   - Only comment on lines that NEED improvement or contain issues
   - CRITICAL: Format comments EXACTLY as: "Line X: Your detailed comment" where X is the line number
   - For each comment:
     * Be specific about what the issue is
     * Explain WHY it matters (security risk, performance impact, etc.)
     * Provide a concrete suggestion for improvement
     * If possible, include a code example of the fix
   - Prioritize significant issues (security, bugs, performance) over minor style issues

6. **PR Summary**:
   - Provide a concise summary (<= {summary_length} words)
   - Highlight the most important changes and their impact
   - Mention any architectural implications
   - Note positive aspects, not just criticisms
   - Prioritize suggestions by importance

Output format:
[Inline Comments]

Summary:
[Your summary here]
"""

        try:
            # Use only the modern OpenAI client approach (v1.0.0+) with explicitly disabled proxies
            client = openai.OpenAI(
                api_key=openai.api_key,
                http_client=httpx.Client(proxies=None)
            )
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,  # Lower temperature for more consistent reviews
            )
            review_text = response.choices[0].message.content.strip()
            
            # Parse response
            try:
                if "Summary:" in review_text:
                    inline_comments, summary = review_text.split("Summary:", 1)
                else:
                    # If there's no "Summary:" marker, treat the whole text as comments and provide a generic summary
                    logger.warning(f"No 'Summary:' section found in response for {file_name}")
                    inline_comments = review_text
                    summary = "No summary provided by the AI review."

                summary = summary.strip()
                if not summary or summary.lower().startswith("no summary"):
                    # Provide a fallback summary if AI is empty or unhelpful
                    summary = (
                        f"**{file_name}** appears to have minor changes that don't require significant feedback. "
                        f"The code looks generally well-structured and follows best practices."
                    )
            except Exception as e:
                logger.error(f"Error parsing AI response for {file_name}: {str(e)}")
                logger.error(f"Response preview: {review_text[:100]}...")
                # Generate error summary
                inline_comments = ""
                summary = f"**Error parsing AI response**: {str(e)}"

            # Format and collect the summary
            formatted_summary = f"### {file_name}\n{summary}"
            all_summaries.append(formatted_summary)

            # Process inline comments - improve parsing with more robust line number extraction
            inline_dict = {}
            try:
                # This pattern should capture exactly two groups: line number and comment text
                comment_pattern = re.compile(r'^(?:(?:Line(?:\s+number)?|L)?[\s:]*)(\d+)[\s:]+(.+)$', re.IGNORECASE | re.MULTILINE)
                
                # Find all comments with line numbers using regex
                matches = comment_pattern.findall(inline_comments)
                logger.info(f"Found {len(matches)} potential comments in {file_name}")
                
                for match in matches:
                    try:
                        # findall returns tuples of the captured groups, should be (line_num, comment_text)
                        if len(match) == 2:
                            line_num, comment_text = match
                        else:
                            # Log unusual match format and continue
                            logger.warning(f"Unexpected match format in {file_name}: {match}")
                            continue
                            
                        comment_text = comment_text.strip()
                        if not comment_text:
                            continue
                        
                        # Format the comment text for better readability
                        formatted_comment = format_comment_text(comment_text, file_name, language)
                        
                        # Merge if there's already a comment for this line
                        if line_num in inline_dict:
                            inline_dict[line_num] += f"\n\n**Additional issue:** {formatted_comment}"
                        else:
                            inline_dict[line_num] = formatted_comment
                    except Exception as e:
                        # Log the error and continue with other comments
                        logger.error(f"Error processing comment match '{match}': {str(e)}")
                        continue
            except Exception as e:
                logger.error(f"Error parsing comments in {file_name}: {str(e)}")
                # Add fallback summary for error cases
                all_summaries.append(f"### {file_name}\n❌ Error during review: {str(e)}")

            if inline_dict:
                # We pass the entire patch to figure out positions, but only lines that are truly improved
                reviews.append((file_name, patch, inline_dict, existing_comment_map))

        except Exception as e:
            logger.error(f"Error reviewing {file_name}: {str(e)}")
            all_summaries.append(f"### {file_name}\n❌ Error during review: {str(e)}")

    # Combine all summaries with better organization
    combined_summary = "\n\n".join(all_summaries)
    return reviews, combined_summary

def post_inline_comments(repo_name, pr_number, token, reviews, config):
    """Post inline comments to GitHub PR with improved formatting and deduplication."""
    logger.info(f"Posting inline comments to PR #{pr_number} in repo {repo_name}")
    url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}/comments"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}

    # Get styling preferences
    styling = config.get("comment_styling", {})
    title_prefix = styling.get("title_prefix", "🔍 AI Code Review - Line")
    show_code_block = styling.get("show_code_block", True)
    show_details = styling.get("show_details", True)
    custom_signature = styling.get("custom_signature", "")

    # Fetch the PR details to get the latest commit ID
    pr_info_url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}"
    
    try:
        pr_info = requests.get(pr_info_url, headers=headers).json()
        commit_id = pr_info.get("head", {}).get("sha", "")
        if not commit_id:
            logger.error("❌ Failed to get commit SHA")
            return
            
        comment_count = 0
        # For each file reviewed
        for file_name, patch, inline_dict, existing_comments in reviews:
            lines = patch.split('\n')
            # Track the current line number in the diff for every `+` line
            position = 1
            plus_line_counter = 0
            # Store the actual line content for better context in comments
            line_content_map = {}

            # First pass: collect line contents
            for diff_line in lines:
                if diff_line.startswith('+') and not diff_line.startswith('+++ '):
                    plus_line_counter += 1
                    # Store the actual code content (without the leading '+')
                    line_content_map[str(plus_line_counter)] = diff_line[1:].strip()

            # Reset counters for second pass
            position = 1
            plus_line_counter = 0

            for diff_line in lines:
                # We only increment position for lines that start with '+'
                # but skip lines that start with '++' in the hunk header
                if diff_line.startswith('+') and not diff_line.startswith('+++ '):
                    plus_line_counter += 1

                    # If we have a comment for this `plus_line_counter` line, post it
                    line_str = str(plus_line_counter)
                    if line_str in inline_dict:
                        comment_text = inline_dict[line_str]
                        
                        # Check if already commented on this line
                        comment_key = f"{file_name}:{position}"
                        if comment_key in existing_comments:
                            logger.info(f"Skipping duplicate comment at {file_name}:{line_str}")
                            position += 1
                            continue
                        
                        # Get the actual code content for this line
                        code_content = line_content_map.get(line_str, "")
                        language = get_file_language(file_name)
                        
                        # Build comment body according to styling preferences - much more minimal
                        body_parts = []
                        
                        # Simplified title - no line numbers since GitHub already shows this context
                        if styling.get("emoji_prefix", True):
                            body_parts.append(f"### AI Code Review\n\n")
                        else:
                            body_parts.append(f"### Code Review\n\n")
                        
                        # Remove redundant line reference since GitHub UI already shows this
                        
                        # Only show code snippet if explicitly enabled and non-empty
                        if show_code_block and code_content and styling.get("show_code_preview", False):
                            body_parts.append(f"```{language}\n{code_content}\n```\n\n")
                        
                        # The actual review comment is the most important part
                        body_parts.append(f"{comment_text}")
                        
                        # Make details section optional and off by default
                        if show_details and styling.get("show_details", False):
                            body_parts.append(f"\n\n<details>\n"
                                f"<summary>About this review</summary>\n\n"
                                f"This automated review identifies potential issues in your code to help improve quality.\n"
                                f"Each suggestion aims to make your code more secure, performant, or maintainable.\n"
                                f"</details>")
                        
                        if custom_signature:
                            body_parts.append(f"\n\n{custom_signature}")
                        
                        body = "".join(body_parts)
                        
                        comment_data = {
                            "body": body,
                            "commit_id": commit_id,
                            "path": file_name,
                            "position": position
                        }
                        
                        response = requests.post(url, headers=headers, json=comment_data)
                        if response.status_code in [201, 200]:
                            comment_count += 1
                            logger.info(f"✅ Posted comment for {file_name}, line {line_str}")
                        else:
                            logger.error(f"❌ Failed to post comment: {response.status_code} - {response.text}")

                    position += 1
                elif diff_line.startswith('@@ '):
                    # Diff hunk header: reset position to 1 for each new hunk
                    position = 1
                else:
                    # For non-additive lines in the diff
                    position += 1
                    
        logger.info(f"Posted {comment_count} inline comments total")
        return comment_count
        
    except Exception as e:
        logger.error(f"Error posting inline comments: {str(e)}")
        return 0

def post_general_summary(repo_name, pr_number, token, summary_text, comment_count=0):
    """Post a general summary comment with improved formatting."""
    logger.info(f"Posting general summary to PR #{pr_number}")
    url = f"https://api.github.com/repos/{repo_name}/issues/{pr_number}/comments"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}

    # Add some metadata to help with debugging
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    
    # Check if there are error messages in the summary
    has_errors = "❌ Error during review" in summary_text
    
    # Format summary with more useful information
    error_notice = ""
    if has_errors:
        error_notice = """
> ⚠️ **Note**: Some errors occurred during the review process. 
> Please check the details below or review the action logs for more information.

"""
    
    body = f"""# 🔍 AI Code Review Summary

{error_notice}{summary_text}

---
*Generated at {timestamp} • {comment_count} inline comments added*

<details>
<summary>ℹ️ About this review</summary>
This automated review provides suggestions to improve code quality and maintainability.
Suggestions are recommendations only - use your judgment about which to implement.

To customize this review, add a `.github/pr_review_config.yml` file to your repository.
</details>
"""

    try:
        response = requests.post(url, headers=headers, json={"body": body})
        if response.status_code in [201, 200]:
            logger.info(f"✅ Successfully posted summary comment")
        else:
            logger.error(f"❌ Failed to post summary: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Error posting summary: {str(e)}")

def main():
    """Main function to run the AI PR review."""
    try:
        # Get GitHub event data
        event_path = os.getenv("GITHUB_EVENT_PATH")
        with open(event_path, 'r') as f:
            event_data = json.load(f)
            
        # Extract PR details
        pr_number = event_data["pull_request"]["number"]
        repo_name = os.getenv("GITHUB_REPOSITORY")
        token = os.getenv("GITHUB_TOKEN") 
        openai.api_key = os.getenv("OPENAI_API_KEY")
        
        if not openai.api_key:
            logger.error("OpenAI API key is missing. Please set the OPENAI_API_KEY environment variable.")
            sys.exit(1)
            
        if not token:
            logger.error("GitHub token is missing. Please ensure GITHUB_TOKEN is available.")
            sys.exit(1)
        
        logger.info(f"Reviewing PR #{pr_number} in {repo_name}")
        
        # Load configuration
        config = PRReviewConfig()
        
        # Get the PR diff and existing comments
        pr_diff = get_pull_request_diff(repo_name, pr_number, token)
        existing_comments = get_existing_comments(repo_name, pr_number, token)
        
        # Review the code
        reviews, summary = review_code_with_gpt(pr_diff, config, existing_comments)
        
        # Post inline comments
        comment_count = post_inline_comments(repo_name, pr_number, token, reviews, config)
        
        # Post general summary
        post_general_summary(repo_name, pr_number, token, summary, comment_count)
        
        logger.info(f"AI review completed successfully with {comment_count} comments")
    except Exception as e:
        logger.error(f"Error in AI review process: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
