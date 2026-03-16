from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


_JSON_FENCE = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_ENTRYPOINT_FILE_CANDIDATES = ("playbook.yml", "site.yml", "main.yml")


@dataclass
class AnsibleArtifactFile:
    path: str
    content: str
    writable: bool = True
    kind: str = "task"

    def normalized_path(self) -> str:
        return self.path.replace("\\", "/").strip("/")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = self.normalized_path()
        return data


@dataclass
class AnsibleArtifactBundle:
    entrypoint_path: str
    files: list[AnsibleArtifactFile]
    manifest: dict[str, Any] = field(default_factory=dict)

    def normalized_entrypoint(self) -> str:
        return self.entrypoint_path.replace("\\", "/").strip("/")

    def file_map(self) -> dict[str, str]:
        return {artifact.normalized_path(): artifact.content for artifact in self.files}

    def to_dict(self) -> dict[str, Any]:
        return {
            "entrypoint_path": self.normalized_entrypoint(),
            "files": [artifact.to_dict() for artifact in self.files],
            "manifest": dict(self.manifest or {}),
        }


def parse_bundle_response(raw_text: str) -> AnsibleArtifactBundle:
    payload_text = _extract_json_payload(raw_text)
    payload = json.loads(payload_text)
    return bundle_from_dict(payload)


def bundle_from_dict(payload: dict[str, Any]) -> AnsibleArtifactBundle:
    entrypoint_path = str(payload.get("entrypoint_path") or "").strip()
    file_payloads = payload.get("files") or []
    if not entrypoint_path:
        raise ValueError("Bundle is missing entrypoint_path")
    if not isinstance(file_payloads, list) or not file_payloads:
        raise ValueError("Bundle must contain a non-empty files list")

    files: list[AnsibleArtifactFile] = []
    for item in file_payloads:
        if not isinstance(item, dict):
            raise ValueError("Each bundle file entry must be an object")
        path = str(item.get("path") or "").strip()
        content = str(item.get("content") or "")
        if not path:
            raise ValueError("Bundle file entry is missing path")
        files.append(
            AnsibleArtifactFile(
                path=path,
                content=content,
                writable=bool(item.get("writable", True)),
                kind=str(item.get("kind") or _infer_kind(path)),
            )
        )

    bundle = AnsibleArtifactBundle(
        entrypoint_path=entrypoint_path,
        files=files,
        manifest=dict(payload.get("manifest") or {}),
    )
    normalize_bundle(bundle)
    return bundle


def normalize_bundle(bundle: AnsibleArtifactBundle) -> AnsibleArtifactBundle:
    bundle.entrypoint_path = bundle.normalized_entrypoint()
    normalized: list[AnsibleArtifactFile] = []
    seen: set[str] = set()
    for artifact in bundle.files:
        artifact.path = artifact.normalized_path()
        if not artifact.path:
            raise ValueError("Bundle contains an empty file path")
        if artifact.path in seen:
            raise ValueError(f"Bundle contains duplicate path '{artifact.path}'")
        seen.add(artifact.path)
        normalized.append(artifact)
    bundle.files = normalized
    if bundle.normalized_entrypoint() not in seen:
        raise ValueError(
            f"Bundle entrypoint '{bundle.normalized_entrypoint()}' is not present in files"
        )
    return bundle


def bundle_from_disk(entrypoint_path: str, files: Iterable[str]) -> AnsibleArtifactBundle:
    artifacts = [
        AnsibleArtifactFile(path=path, content=Path(path).read_text(encoding="utf-8"))
        for path in files
    ]
    return normalize_bundle(
        AnsibleArtifactBundle(entrypoint_path=entrypoint_path, files=artifacts, manifest={})
    )


def _extract_json_payload(raw_text: str) -> str:
    match = _JSON_FENCE.search(raw_text or "")
    if match:
        return match.group(1).strip()
    stripped = (raw_text or "").strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    raise ValueError("LLM response did not contain a JSON bundle payload")


def _infer_kind(path: str) -> str:
    lowered = path.replace("\\", "/").lower()
    if "/tasks/" in lowered:
        return "task"
    if lowered.endswith((".yml", ".yaml")) and any(name in lowered for name in _ENTRYPOINT_FILE_CANDIDATES):
        return "playbook"
    if lowered.endswith(".j2"):
        return "template"
    return "artifact"
