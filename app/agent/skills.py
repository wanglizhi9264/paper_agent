from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.agent.contracts import TOOL_INPUTS, AgentError


class UniqueSafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[object, object]:
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if any(not isinstance(key, str) for key in keys) or len(set(keys)) != len(keys):
            raise AgentError("SKILL_INVALID")
        return super().construct_mapping(node, deep=deep)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    digest: str
    root: Path
    tools: frozenset[str]
    files: tuple[str, ...]
    hashes: dict[str, str]


def _safe_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if (
        path.is_absolute()
        or path.drive
        or ".." in path.parts
        or "\\" in relative
        or ":" in relative
    ):
        raise AgentError("SKILL_RESOURCE_INVALID")
    candidate = root / path
    for parent in (candidate, *candidate.parents):
        if parent.is_symlink() or parent.is_junction():
            raise AgentError("SKILL_RESOURCE_INVALID")
        if parent == root:
            break
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise AgentError("SKILL_RESOURCE_INVALID")
    return candidate


def _read(root: Path, relative: str) -> bytes:
    path = _safe_path(root, relative)
    try:
        if not path.is_file() or path.stat().st_size > 262144:
            raise AgentError("SKILL_INVALID")
        return path.read_bytes()
    except OSError as exc:
        raise AgentError("SKILL_INVALID") from exc


def _text(root: Path, relative: str) -> str:
    try:
        return _read(root, relative).decode("utf-8")
    except UnicodeError as exc:
        raise AgentError("SKILL_INVALID") from exc


class SkillRegistry:
    """Only explicitly supplied roots; user skill installation is outside the runtime."""

    def __init__(self, root: Path, additional: Path | None = None) -> None:
        self.skills: dict[str, Skill] = {}
        if root.is_symlink() or root.is_junction():
            raise AgentError("SKILL_INVALID")
        folders = [root] if (root / "SKILL.md").is_file() else sorted(root.iterdir())
        for folder in folders:
            if not folder.is_dir():
                continue
            if folder.is_symlink() or folder.is_junction():
                raise AgentError("SKILL_INVALID")
            text = _text(folder, "SKILL.md")
            parts = text.split("---", 2)
            if len(parts) != 3 or parts[0].strip():
                raise AgentError("SKILL_INVALID")
            try:
                metadata = yaml.load(parts[1], Loader=UniqueSafeLoader)
            except yaml.YAMLError as exc:
                raise AgentError("SKILL_INVALID") from exc
            if not isinstance(metadata, dict):
                raise AgentError("SKILL_INVALID")
            name, description = metadata.get("name"), metadata.get("description")
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                or len(name) > 64
                or name != folder.name
                or name in self.skills
                or not isinstance(description, str)
                or not 1 <= len(description) <= 1024
            ):
                raise AgentError("SKILL_INVALID")
            requested = metadata.get("allowed-tools", "list_papers search_papers read_chunks")
            if not isinstance(requested, str):
                raise AgentError("SKILL_INVALID")
            tools = frozenset(requested.split())
            if not tools.issubset(TOOL_INPUTS) or "finish" in tools:
                raise AgentError("SKILL_INVALID")
            for entry in folder.rglob("*"):
                if entry.is_symlink() or entry.is_junction():
                    raise AgentError("SKILL_RESOURCE_INVALID")
            files = tuple(
                sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
            )
            digest = hashlib.sha256()
            hashes: dict[str, str] = {}
            size = 0
            for relative in files:
                content = _read(folder, relative)
                hashes[relative] = hashlib.sha256(content).hexdigest()
                size += len(content)
                digest.update(relative.encode() + b"\0" + hashlib.sha256(content).digest())
            if size > 2097152:
                raise AgentError("SKILL_INVALID")
            self.skills[name] = Skill(
                name, description, digest.hexdigest(), folder, tools, files, hashes
            )
        if additional is not None and additional.is_dir():
            other = SkillRegistry(additional)
            if self.skills.keys() & other.skills.keys():
                raise AgentError("SKILL_NAME_CONFLICT")
            self.skills.update(other.skills)

    def get(self, name: str) -> Skill:
        skill = self.skills.get(name)
        if skill is None:
            raise AgentError("SKILL_NOT_FOUND")
        return skill

    def read(self, name: str, relative: str = "SKILL.md") -> str:
        skill = self.get(name)
        if relative not in skill.files or (
            relative != "SKILL.md"
            and (
                not relative.startswith(("references/", "assets/")) or not relative.endswith(".md")
            )
        ):
            raise AgentError("SKILL_RESOURCE_INVALID")
        content = _read(skill.root, relative)
        if hashlib.sha256(content).hexdigest() != skill.hashes[relative]:
            raise AgentError("SKILL_CHANGED")
        try:
            return content.decode("utf-8")
        except UnicodeError as exc:
            raise AgentError("SKILL_INVALID") from exc

    @property
    def signature(self) -> str:
        return hashlib.sha256(
            "\n".join(f"{s.name}:{s.digest}" for s in self.skills.values()).encode()
        ).hexdigest()
