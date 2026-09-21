"""A replay corpus: rewritten documents, the repository they describe, labels.

scripts/replay_check.py, rejudge_replay.py and agreement.py measure the
checker on documents rewritten earlier, and all three read the same
description of where those documents live, `corpus.toml` in the corpus
directory:

    name = "sample"
    labels = "labels.json"        # the default
    pairs = "pairs"               # the default: <pairs>/<name>.old.md and .new.md
    repo = "repo"                 # a directory of plain files in the corpus,
                                  # made into a temporary git repository per run
    # checkout = "~/src/project"  # or a git checkout; each document names a commit

    [[documents]]
    name = "README"
    path = "README.md"            # the document's path inside the repository
    # commit = "0ab7338"          # needed with a checkout
    # repo = "stationlog"         # a repository from [repos.<name>] instead
    # old = "...", new = "..."    # the two sides, when they are not in pairs/

    [repos.stationlog]            # more repositories, for a corpus that spans several
    checkout = "~/src/stationlog"

The top-level repository is named after the corpus. A label belongs to a
document when its `file` is the document's path and its `corpus`, if it has
one, names the document's repository.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class CorpusError(Exception):
    pass


@dataclass
class Repo:
    name: str
    kind: str        # "files" or "checkout"
    path: Path


@dataclass
class Document:
    name: str
    repo: str
    path: str
    old: Path
    new: Path
    commit: str = ""

    def texts(self) -> tuple[str, str]:
        return self.old.read_text(encoding="utf-8"), self.new.read_text(encoding="utf-8")


@dataclass
class Corpus:
    root: Path
    name: str
    documents: list = field(default_factory=list)
    labels: dict = field(default_factory=dict)
    repos: dict = field(default_factory=dict)

    def find(self, repo: str, path: str) -> Document:
        for d in self.documents:
            if d.repo == repo and d.path == path:
                return d
        raise CorpusError(f"no document {path} in repository {repo} of corpus {self.name}")

    def select(self, selectors) -> list:
        """The documents named by each selector (a name, a path, or the end of
        a name), or every document when there is none."""
        if not selectors:
            return list(self.documents)
        chosen = []
        for s in selectors:
            match = ([d for d in self.documents if s in (d.name, d.path, f"{d.repo}/{d.path}")]
                     or [d for d in self.documents if d.name.endswith(s) or d.name.endswith(s.removesuffix(".md"))])
            if len(match) != 1:
                names = ", ".join(d.name for d in match) or "none"
                raise CorpusError(f"{s!r} names {len(match)} documents ({names})")
            chosen += match
        return chosen

    def labels_for(self, doc: Document) -> tuple[list, list]:
        """The labelled wrong sentences (set A) and true sentences (set C) of a document."""
        def mine(entry):
            return entry.get("file") == doc.path and entry.get("corpus", doc.repo) == doc.repo
        over = self.labels.get("over_cap") or {}
        set_a = [a for a in self.labels.get("set_a", []) + over.get("set_a", []) if mine(a)]
        set_c = [c for c in self.labels.get("set_c", []) + over.get("set_c", []) if mine(c)]
        return set_a, set_c


def _repo(name: str, table: dict, base: Path) -> Repo:
    if "repo" in table:
        return Repo(name, "files", (base / table["repo"]).resolve())
    if "checkout" in table:
        return Repo(name, "checkout", Path(os.path.expandvars(table["checkout"])).expanduser())
    raise CorpusError(f"repository {name} needs `repo` (a directory) or `checkout` (a git checkout)")


def load(directory) -> Corpus:
    root = Path(directory).resolve()
    try:
        conf = tomllib.loads((root / "corpus.toml").read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise CorpusError(f"{root} has no corpus.toml") from e
    name = conf.get("name") or root.name
    corpus = Corpus(root=root, name=name)
    if "repo" in conf or "checkout" in conf:
        corpus.repos[name] = _repo(name, conf, root)
    for key, table in (conf.get("repos") or {}).items():
        corpus.repos[key] = _repo(key, table, root)
    pairs = root / conf.get("pairs", "pairs")
    for entry in conf.get("documents", []):
        repo = entry.get("repo", name)
        if repo not in corpus.repos:
            raise CorpusError(f"document {entry.get('name')} names an unknown repository {repo}")
        doc_name = entry.get("name") or Path(entry["path"]).stem
        old = root / entry["old"] if "old" in entry else pairs / f"{doc_name}.old.md"
        new = root / entry["new"] if "new" in entry else pairs / f"{doc_name}.new.md"
        commit = entry.get("commit", "")
        if corpus.repos[repo].kind == "checkout" and not commit:
            raise CorpusError(f"document {doc_name} is in a checkout and names no commit")
        corpus.documents.append(Document(doc_name, repo, entry["path"], old, new, commit))
    labels = root / conf.get("labels", "labels.json")
    if labels.exists():
        corpus.labels = json.loads(labels.read_text(encoding="utf-8"))
    return corpus


def default_out() -> Path:
    """Where replay records go when --out is not given."""
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "writing-register" / "replay"


def _git(args, cwd, **kw):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True, **kw)


class Checkouts:
    """The git repository and commit behind each document, for as long as the
    context is open. A directory of files is copied and committed once, into a
    temporary repository that is removed on exit, so its `.gitignore` applies
    exactly as it would in a real checkout."""

    def __init__(self, corpus: Corpus):
        self.corpus, self.made = corpus, {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for path in self.made.values():
            shutil.rmtree(path.parent, ignore_errors=True)

    def repository(self, doc: Document) -> tuple[Path, str]:
        repo = self.corpus.repos[doc.repo]
        if repo.kind == "checkout":
            return repo.path, doc.commit
        if repo.name not in self.made:
            target = Path(tempfile.mkdtemp(prefix="wr-corpus-")) / repo.name
            shutil.copytree(repo.path, target, symlinks=True)
            _git(["init", "-q"], target)
            _git(["add", "-A"], target)
            _git(["-c", "user.name=wr", "-c", "user.email=wr@example.com", "commit", "-qm", "corpus"], target)
            self.made[repo.name] = target
        return self.made[repo.name], "HEAD"

    def export(self, doc: Document, text: str) -> Path:
        """The repository's files at the document's commit, with `text` as the
        document: what the checker reads. Remove it with shutil.rmtree."""
        repo, commit = self.repository(doc)
        return export_at(repo, commit, doc.path, text)


def export_at(repo: Path, commit: str, rel: str, text: str) -> Path:
    # The same neutral parent wr gives the checker, outside any repository.
    from writing_register.spawn import _neutral_cwd
    parent = _neutral_cwd()
    parent.mkdir(parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix="replay-", dir=parent))
    archive = _git(["archive", commit], repo)
    subprocess.run(["tar", "-x", "-C", str(target)], input=archive.stdout, check=True)
    (target / rel).parent.mkdir(parents=True, exist_ok=True)
    (target / rel).write_text(text, encoding="utf-8")
    return target
