"""Optional GitHub API access. Every caller must cope with `None`.

Set GITHUB_TOKEN (or GH_TOKEN) for higher rate limits. Without the API,
ContribOS still works from the git clone alone, with less evidence.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"


class GitHub:
    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.available = True
        self.last_error: str | None = None

    def get(self, path: str, params: dict | None = None):
        """GET an API path; returns parsed JSON or None on any failure."""
        if not self.available:
            return None
        url = f"{API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "contribos",
            **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            self.last_error = f"HTTP {e.code} for {path}"
            if e.code in (401, 403):
                self.available = False  # blocked or rate-limited: stop trying
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            self.last_error = f"{type(e).__name__} for {path}"
            self.available = False
            return None

    def issue(self, owner: str, name: str, number: int):
        return self.get(f"/repos/{owner}/{name}/issues/{number}")

    def issue_comments(self, owner: str, name: str, number: int):
        return self.get(f"/repos/{owner}/{name}/issues/{number}/comments", {"per_page": 100}) or []

    def pr_review_comments(self, owner: str, name: str, number: int):
        return self.get(f"/repos/{owner}/{name}/pulls/{number}/comments", {"per_page": 100}) or []

    def pulls(self, owner: str, name: str, state: str = "closed", per_page: int = 50):
        return self.get(f"/repos/{owner}/{name}/pulls",
                        {"state": state, "per_page": per_page, "sort": "updated",
                         "direction": "desc"}) or []

    def repo(self, owner: str, name: str):
        return self.get(f"/repos/{owner}/{name}")

    # Discovery and follow-through.

    def search_issues(self, query: str, per_page: int = 30):
        res = self.get("/search/issues", {"q": query, "per_page": per_page, "sort": "updated", "order": "desc"})
        return (res or {}).get("items", [])

    def timeline(self, owner: str, name: str, number: int):
        return self.get(f"/repos/{owner}/{name}/issues/{number}/timeline", {"per_page": 100}) or []

    def pull(self, owner: str, name: str, number: int):
        return self.get(f"/repos/{owner}/{name}/pulls/{number}")

    def pr_reviews(self, owner: str, name: str, number: int):
        return self.get(f"/repos/{owner}/{name}/pulls/{number}/reviews", {"per_page": 100}) or []

    def check_runs(self, owner: str, name: str, ref: str):
        res = self.get(f"/repos/{owner}/{name}/commits/{ref}/check-runs", {"per_page": 100})
        return (res or {}).get("check_runs", [])

    def has_file(self, owner: str, name: str, path: str) -> bool:
        return self.get(f"/repos/{owner}/{name}/contents/{path}") is not None

    def me(self) -> str | None:
        """Login of the token's owner, cached."""
        if not hasattr(self, "_me"):
            self._me = (self.get("/user") or {}).get("login") if self.token else None
        return self._me

    def list_dir(self, owner: str, name: str, path: str) -> list[str]:
        res = self.get(f"/repos/{owner}/{name}/contents/{path}")
        return [x.get("name", "") for x in res] if isinstance(res, list) else []
