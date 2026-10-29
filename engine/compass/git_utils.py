"""Git utilities for Compass workspaces.

Auto-creates git repositories for new projects with appropriate .gitignore.
Supports pushing workspaces to GitHub via the GitHub API.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

GITIGNORE_CONTENT = """\
# Compass internal data (embeddings, vector DB)
.compass/knowledge/

# Encrypted files
*.enc

# Python
__pycache__/
*.pyc
.venv/

# OS
.DS_Store
Thumbs.db

# Environment
.env
.env.local
"""


def init_git_repo(workspace_path: Path) -> bool:
    """Initialize a git repo in the workspace if not already one.

    Creates .gitignore and makes an initial commit with compass.yaml.
    Returns True if a new repo was created, False if one already existed.
    """
    try:
        from git import InvalidGitRepositoryError, Repo
    except ImportError:
        logger.warning("gitpython not installed, skipping git init")
        return False

    # Check if already a git repo
    try:
        Repo(workspace_path)
        logger.debug("Workspace %s is already a git repo", workspace_path)
        return False
    except InvalidGitRepositoryError:
        pass

    try:
        repo = Repo.init(workspace_path)

        # Write .gitignore
        gitignore_path = workspace_path / ".gitignore"
        if not gitignore_path.exists():
            gitignore_path.write_text(GITIGNORE_CONTENT)

        # Stage compass.yaml and .gitignore
        files_to_add = [".gitignore"]
        if (workspace_path / "compass.yaml").exists():
            files_to_add.append("compass.yaml")

        repo.index.add(files_to_add)
        repo.index.commit("Initial commit — Compass workspace")

        logger.info("Initialized git repo at %s", workspace_path)
        return True
    except Exception as e:
        logger.warning("Failed to initialize git repo at %s: %s", workspace_path, e, exc_info=True)
        return False


def push_to_github(workspace_path: Path, repo_name: str, token: str, private: bool = True) -> dict:
    """Create a GitHub repo and push the workspace to it.

    Args:
        workspace_path: Path to the git workspace.
        repo_name: Name for the new GitHub repo.
        token: GitHub access token.
        private: Whether the repo should be private.

    Returns:
        Dict with status, repo_url, and any error.
    """
    try:
        from git import InvalidGitRepositoryError, Repo
    except ImportError:
        return {"status": "error", "error": "gitpython not installed"}

    # Verify workspace is a git repo
    try:
        repo = Repo(workspace_path)
    except InvalidGitRepositoryError:
        return {"status": "error", "error": "Not a git repository"}

    # Create repo on GitHub
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    try:
        res = httpx.post(
            "https://api.github.com/user/repos",
            headers=headers,
            json={"name": repo_name, "private": private, "auto_init": False},
            timeout=30,
        )

        if res.status_code == 422:
            # Repo might already exist
            error_msg = res.json().get("message", "")
            if "already exists" in error_msg.lower():
                return {"status": "error", "error": f"Repository '{repo_name}' already exists"}
            return {"status": "error", "error": error_msg}

        if res.status_code not in (200, 201):
            return {"status": "error", "error": f"GitHub API error: {res.status_code} {res.text[:200]}"}

        repo_data = res.json()
        clone_url = repo_data.get("clone_url", "")
        html_url = repo_data.get("html_url", "")

    except httpx.HTTPError as e:
        return {"status": "error", "error": f"Failed to create repo: {e}"}

    # Set remote and push
    try:
        # Use token-authenticated URL for push
        auth_url = clone_url.replace("https://", f"https://x-access-token:{token}@")

        if "origin" in [r.name for r in repo.remotes]:
            repo.delete_remote("origin")
        repo.create_remote("origin", auth_url)

        # Push
        repo.remotes.origin.push(refspec="HEAD:refs/heads/main")

        # Replace remote URL with non-token version
        repo.delete_remote("origin")
        repo.create_remote("origin", clone_url)

        logger.info("Pushed workspace to %s", html_url)
        return {"status": "ok", "repo_url": html_url, "clone_url": clone_url}

    except Exception as e:
        logger.warning("Failed to push to GitHub: %s", e, exc_info=True)
        return {"status": "error", "error": f"Push failed: {e}"}
