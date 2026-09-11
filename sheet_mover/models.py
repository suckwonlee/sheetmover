from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CharacterSheet:
    name: str = ""
    ability_scores: dict[str, Any] = field(default_factory=dict)
    proficiencies: list[str] = field(default_factory=list)
    hp: int | None = None
    max_hp: int | None = None
    equipment: list[dict[str, Any]] = field(default_factory=list)
    spells: list[dict[str, Any]] = field(default_factory=list)
    features: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CharacterSheet":
        return cls(
            name=str(data.get("name", "")),
            ability_scores=data.get("ability_scores", {}),
            proficiencies=data.get("proficiencies", []),
            hp=data.get("hp"),
            max_hp=data.get("max_hp"),
            equipment=data.get("equipment", []),
            spells=data.get("spells", []),
            features=data.get("features", []),
            warnings=data.get("warnings", []),
        )
