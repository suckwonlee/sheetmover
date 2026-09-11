from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass
class CharacterSheet:
    """Platform-neutral character model.

    Fields are intentionally broader than the current Roll20 writer.  Later
    stages can calculate final values and map them without having to re-read
    D&D Beyond's raw response.
    """

    # Identity / biography
    source_id: str = ""
    name: str = ""
    race: dict[str, Any] = field(default_factory=dict)
    background: dict[str, Any] = field(default_factory=dict)
    classes: list[dict[str, Any]] = field(default_factory=list)
    total_level: int | None = None
    experience: int | float | None = None
    alignment: str = ""

    # Core statistics. Final calculated values may remain None until stage 2.
    ability_scores: dict[str, Any] = field(default_factory=dict)
    proficiency_bonus: int | None = None
    armor_class: int | None = None
    initiative: int | None = None
    speed: dict[str, Any] = field(default_factory=dict)

    # Hit points / survival
    hp: int | float | None = None
    max_hp: int | float | None = None
    temp_hp: int | float | None = None
    hit_dice: list[dict[str, Any]] = field(default_factory=list)
    death_saves: dict[str, Any] = field(default_factory=dict)
    inspiration: bool | int | None = None

    # Proficiencies / defenses
    proficiencies: list[str] = field(default_factory=list)
    proficiency_entries: list[dict[str, Any]] = field(default_factory=list)
    saving_throw_proficiencies: list[str] = field(default_factory=list)
    skill_proficiencies: list[dict[str, Any]] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    senses: list[dict[str, Any]] = field(default_factory=list)
    defenses: list[dict[str, Any]] = field(default_factory=list)

    # Character content
    equipment: list[dict[str, Any]] = field(default_factory=list)
    spells: list[dict[str, Any]] = field(default_factory=list)
    features: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    attacks: list[dict[str, Any]] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)

    # Spellcasting / inventory bookkeeping
    spellcasting: dict[str, Any] = field(default_factory=dict)
    currencies: dict[str, Any] = field(default_factory=dict)

    # Roleplay / free-form source fields
    character_traits: dict[str, Any] = field(default_factory=dict)
    notes: dict[str, Any] = field(default_factory=dict)

    # Inputs deliberately preserved for stage 2 calculations.
    # These are source facts, not final Roll20 values.
    calculation_inputs: dict[str, Any] = field(default_factory=dict)

    warnings: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CharacterSheet":
        if not isinstance(data, dict):
            raise TypeError("CharacterSheet JSON must be an object.")

        allowed = {item.name for item in fields(cls)}
        values = {
            key: deepcopy(value)
            for key, value in data.items()
            if key in allowed
        }
        return cls(**values)
