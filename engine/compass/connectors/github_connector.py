"""GitHub connector — the CODE source of truth.

Ingests: README, key source files, recent commits, open issues/PRs.
Answers: "What CAN the product do?"

Dual-mode:
  - Live API: Fetches via GitHub REST API when OAuth credentials are available
  - File import: Reads from local repo path (fallback)
"""

from __future__ import annotations

import logging
from pathlib import Path

from compass.connectors.live_base import LiveConnector
from compass.models.sources import Evidence, SourceType

logger = logging.getLogger(__name__)

RELEVANT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".rb", ".swift", ".kt", ".scala", ".md", ".yaml", ".yml",
    ".toml", ".json",
}

SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".cache", "vendor",
}

MAX_FILE_SIZE = 50_000  # chars
MAX_FILES = 50

GITHUB_API = "https://api.github.com"


class GitHubConnector(LiveConnector):
    """Ingests code evidence from a local repo or GitHub API."""

    connector_type = "github"
    provider_id = "github"
    rate_limit_rpm = 60  # GitHub allows 5000/hr for authenticated users

    def validate(self) -> bool:
        path = self.config.path
        if path and Path(path).exists():
            return True
        url = self.config.url
        if url:
            return True
        return bool(self.has_credentials())

    # ------------------------------------------------------------------
    # Live API ingestion
    # ------------------------------------------------------------------

    def ingest_live(self) -> list[Evidence]:
        """Fetch evidence from GitHub REST API."""
        evidence: list[Evidence] = []

        repo_slug = self._get_repo_slug()
        if not repo_slug:
            logger.warning("No repo slug configured for GitHub live connector")
            return self.ingest_file()

        owner, repo = repo_slug.split("/", 1)

        # Repo metadata
        try:
            meta = self._api_get(f"{GITHUB_API}/repos/{owner}/{repo}").json()
            evidence.append(Evidence(
                source_type=SourceType.CODE,
                connector="github",
                title=f"Repository: {meta.get('full_name', repo_slug)}",
                content=(
                    f"Name: {meta.get('full_name')}\n"
                    f"Description: {meta.get('description', 'N/A')}\n"
                    f"Language: {meta.get('language', 'N/A')}\n"
                    f"Stars: {meta.get('stargazers_count', 0)}\n"
                    f"Forks: {meta.get('forks_count', 0)}\n"
                    f"Open issues: {meta.get('open_issues_count', 0)}\n"
                    f"Default branch: {meta.get('default_branch', 'main')}\n"
                    f"Topics: {', '.join(meta.get('topics', []))}\n"
                ),
                metadata={"type": "repo_metadata", "repo": repo_slug, "source": "api"},
            ))
        except Exception as e:
            logger.warning("Failed to fetch repo metadata: %s", e, exc_info=True)

        # README
        try:
            readme_res = self._api_get(
                f"{GITHUB_API}/repos/{owner}/{repo}/readme",
                headers={"Accept": "application/vnd.github.raw+json"},
            )
            readme_content = readme_res.text[:MAX_FILE_SIZE]
            evidence.append(Evidence(
                source_type=SourceType.CODE,
                connector="github",
                title=f"README: {repo}",
                content=readme_content,
                metadata={"type": "readme", "repo": repo_slug, "source": "api"},
            ))
        except Exception as e:
            logger.debug("No README or failed to fetch: %s", e, exc_info=True)

        # Recent commits (last 30)
        try:
            commits = self._api_get(
                f"{GITHUB_API}/repos/{owner}/{repo}/commits",
                params={"per_page": "30"},
            ).json()
            lines = []
            for c in commits:
                date = c.get("commit", {}).get("author", {}).get("date", "")[:10]
                msg = c.get("commit", {}).get("message", "").split("\n")[0]
                author = c.get("commit", {}).get("author", {}).get("name", "")
                lines.append(f"- {date} | {author} | {msg}")
            if lines:
                evidence.append(Evidence(
                    source_type=SourceType.CODE,
                    connector="github",
                    title=f"Recent commits: {repo}",
                    content="\n".join(lines),
                    metadata={"type": "git_log", "repo": repo_slug, "source": "api"},
                ))
        except Exception as e:
            logger.warning("Failed to fetch commits: %s", e, exc_info=True)

        # Open issues (last 30)
        try:
            issues = self._api_get(
                f"{GITHUB_API}/repos/{owner}/{repo}/issues",
                params={"state": "open", "per_page": "30", "sort": "updated"},
            ).json()
            for issue in issues:
                if issue.get("pull_request"):
                    continue  # Skip PRs (handled below)
                labels = ", ".join(lb.get("name", "") for lb in issue.get("labels", []))
                body = (issue.get("body") or "")[:5000]
                evidence.append(Evidence(
                    source_type=SourceType.CODE,
                    connector="github",
                    title=f"Issue #{issue['number']}: {issue.get('title', '')}",
                    content=(
                        f"State: {issue.get('state')}\n"
                        f"Labels: {labels}\n"
                        f"Author: {issue.get('user', {}).get('login', '')}\n"
                        f"Created: {issue.get('created_at', '')[:10]}\n"
                        f"Updated: {issue.get('updated_at', '')[:10]}\n\n"
                        f"{body}"
                    ),
                    metadata={
                        "type": "issue",
                        "number": str(issue["number"]),
                        "repo": repo_slug,
                        "source": "api",
                    },
                ))
        except Exception as e:
            logger.warning("Failed to fetch issues: %s", e, exc_info=True)

        # Open pull requests (last 30)
        try:
            prs = self._api_get(
                f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
                params={"state": "open", "per_page": "30", "sort": "updated"},
            ).json()
            for pr in prs:
                body = (pr.get("body") or "")[:5000]
                evidence.append(Evidence(
                    source_type=SourceType.CODE,
                    connector="github",
                    title=f"PR #{pr['number']}: {pr.get('title', '')}",
                    content=(
                        f"State: {pr.get('state')}\n"
                        f"Author: {pr.get('user', {}).get('login', '')}\n"
                        f"Base: {pr.get('base', {}).get('ref', '')}\n"
                        f"Head: {pr.get('head', {}).get('ref', '')}\n"
                        f"Created: {pr.get('created_at', '')[:10]}\n"
                        f"Updated: {pr.get('updated_at', '')[:10]}\n\n"
                        f"{body}"
                    ),
                    metadata={
                        "type": "pull_request",
                        "number": str(pr["number"]),
                        "repo": repo_slug,
                        "source": "api",
                    },
                ))
        except Exception as e:
            logger.warning("Failed to fetch PRs: %s", e, exc_info=True)

        logger.info("GitHub live: fetched %d evidence items from %s", len(evidence), repo_slug)
        return evidence

    def _get_repo_slug(self) -> str | None:
        """Extract owner/repo from config URL or options."""
        # Check options first
        slug = self.config.options.get("repo")
        if slug:
            return slug

        # Parse from URL (e.g. https://github.com/owner/repo)
        url = self.config.url
        if url:
            parts = url.rstrip("/").split("/")
            if len(parts) >= 2:
                return f"{parts[-2]}/{parts[-1]}"

        return None

    # ------------------------------------------------------------------
    # File-based ingestion (original behavior)
    # ------------------------------------------------------------------

    def ingest_file(self) -> list[Evidence]:
        evidence: list[Evidence] = []
        path = self.config.path
        if path:
            repo_path = Path(path).expanduser().resolve()
            if repo_path.exists():
                evidence.extend(self._ingest_local(repo_path))
        return evidence

    def _ingest_local(self, repo_path: Path) -> list[Evidence]:
        evidence: list[Evidence] = []

        readme = self._find_readme(repo_path)
        if readme:
            evidence.append(Evidence(
                source_type=SourceType.CODE,
                connector="github",
                title=f"README: {repo_path.name}",
                content=readme.read_text(errors="ignore")[:MAX_FILE_SIZE],
                metadata={"file": str(readme), "type": "readme"},
            ))

        source_files = self._find_source_files(repo_path)
        for fpath in source_files[:MAX_FILES]:
            try:
                content = fpath.read_text(errors="ignore")
                if len(content) > MAX_FILE_SIZE:
                    content = content[:MAX_FILE_SIZE] + "\n... (truncated)"
                rel = fpath.relative_to(repo_path)
                evidence.append(Evidence(
                    source_type=SourceType.CODE,
                    connector="github",
                    title=f"Source: {rel}",
                    content=content,
                    metadata={
                        "file": str(rel),
                        "type": "source",
                        "extension": fpath.suffix,
                    },
                ))
            except Exception as e:
                logger.warning("Skipping %s: %s", fpath, e, exc_info=True)
                continue

        git_log = self._get_git_log(repo_path)
        if git_log:
            evidence.append(Evidence(
                source_type=SourceType.CODE,
                connector="github",
                title=f"Recent commits: {repo_path.name}",
                content=git_log,
                metadata={"type": "git_log"},
            ))

        return evidence

    def _find_readme(self, repo_path: Path) -> Path | None:
        for name in ["README.md", "README.rst", "README.txt", "README"]:
            p = repo_path / name
            if p.exists():
                return p
        return None

    def _find_source_files(self, repo_path: Path) -> list[Path]:
        files: list[Path] = []
        for fpath in repo_path.rglob("*"):
            if any(skip in fpath.parts for skip in SKIP_DIRS):
                continue
            if fpath.is_symlink():
                continue
            if fpath.is_file() and fpath.suffix in RELEVANT_EXTENSIONS:
                files.append(fpath)
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        return files

    def _get_git_log(self, repo_path: Path) -> str:
        try:
            import git
            repo = git.Repo(repo_path)
            commits = list(repo.iter_commits(max_count=30))
            lines = []
            for c in commits:
                lines.append(f"- {c.committed_datetime.strftime('%Y-%m-%d')} | {c.summary}")
            return "\n".join(lines)
        except Exception:
            logger.debug("Could not read the repository git log; omitting commit evidence.", exc_info=True)
            return ""
