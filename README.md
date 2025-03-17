# Code Reviewer AI

A GitHub Action that performs AI-powered code reviews on pull requests using OpenAI's API.

## Features

- AI-powered automated code reviews
- Customizable review focus (bugs, security, performance, maintainability)
- Language-specific rules and style guide enforcement
- Adjustable comment threshold and review thoroughness
- File filtering to focus on relevant code

## Usage

Add this GitHub Action to your repository by creating a workflow file (e.g., `.github/workflows/code-review.yml`):

```yaml
name: AI Code Review

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v3
        with:
          fetch-depth: 0
      
      - name: Run AI Code Review
        uses: orielo/code-reviewer-ai@main
        with:
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
          # Optional parameters
          # review_mode: "thorough"
          # comment_threshold: "low"
```

## Configuration

### Required Input

- `openai_api_key`: Your OpenAI API key (should be stored as a GitHub secret)

### Optional Inputs

- `github_token`: GitHub token for accessing repository data (default: `${{ github.token }}`)
- `config_path`: Path to custom configuration file (default: `.github/pr_review_config.yml`)
- `review_mode`: Review mode - concise, standard, or thorough (default: `standard`)
- `comment_threshold`: Comment threshold - low, medium, or high (default: `medium`)

## Custom Configuration

Create a `.github/pr_review_config.yml` file in your repository to customize the review process:

```yaml
# Review configuration
review_mode: standard          # Options: concise, standard, thorough
comment_threshold: medium      # Options: low, medium, or high

# Define what to focus on
review_focus:
  - bugs
  - security
  - performance
  - maintainability

# File filtering
file_filters:
  include: ["*"]
  exclude: ["*.md", "*.txt", "package-lock.json", "yarn.lock"]

# How many words max in the summary
summary_length: 150

# Language-specific settings
language_specific_rules:
  python:
    style_guide: PEP8
    extra_focus: ["type_hints"]
  javascript:
    style_guide: Airbnb
    extra_focus: ["null_safety"]
```

## License

MIT 