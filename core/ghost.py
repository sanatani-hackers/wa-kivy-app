"""Ghost Mode — real persisted wire-level privacy configuration & guard."""
import json
import os
from dataclasses import asdict, dataclass, fields


@dataclass
class GhostConfig:
    """Persisted privacy settings for WA Core."""

    ghost_read: bool = False
    ghost_presence: bool = False
    ghost_typing: bool = False
    ghost_viewonce_inline: bool = False
    auto_wipe: bool = True
    anti_delete_relay: bool = True

    @property
    def ghost_viewonce(self) -> bool:
        return self.ghost_viewonce_inline

    @classmethod
    def path_for(cls, private_dir: str) -> str:
        return os.path.join(private_dir, "ghost.json")

    @classmethod
    def load(cls, private_dir: str) -> "GhostConfig":
        """Load GhostConfig from private_dir/ghost.json (returns defaults on missing/corrupt)."""
        p = cls.path_for(private_dir)
        if not os.path.exists(p):
            return cls()
        try:
            with open(p, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return cls()
            valid = {fld.name for fld in fields(cls)}
            norm = {}
            for k, v in raw.items():
                key = "ghost_viewonce_inline" if k == "ghost_viewonce" else k
                if key in valid:
                    norm[key] = bool(v)
            return cls(**norm)
        except Exception:
            return cls()

    def save(self, private_dir: str) -> bool:
        """Atomically persist GhostConfig to private_dir/ghost.json."""
        os.makedirs(private_dir, exist_ok=True)
        p = self.path_for(private_dir)
        tmp = f"{p}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2)
            os.replace(tmp, p)
            return True
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return False

    def to_dict(self) -> dict:
        return asdict(self)

    def update_from_dict(self, updates: dict) -> dict:
        """Merge known boolean keys from updates dict and return updated dict."""
        if not isinstance(updates, dict):
            return self.to_dict()
        valid = {fld.name for fld in fields(self)}
        for k, v in updates.items():
            key = "ghost_viewonce_inline" if k == "ghost_viewonce" else k
            if key in valid:
                setattr(self, key, bool(v))
        return self.to_dict()


class GhostGuard:
    """Checks wire-level privacy rules before emitting WhatsApp protocol frames."""

    def __init__(self, config: GhostConfig):
        self.config = config

    def allow_mark_read(self) -> bool:
        return not self.config.ghost_read

    def allow_presence(self) -> bool:
        return not self.config.ghost_presence

    def allow_typing(self) -> bool:
        return not self.config.ghost_typing

    def allow_viewonce_receipt(self) -> bool:
        return not self.config.ghost_viewonce_inline
