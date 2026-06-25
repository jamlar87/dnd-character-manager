"""Pydantic models for request validation."""

from pydantic import BaseModel, Field, field_validator
from typing import Optional
from pydantic import ConfigDict


class CreateCharacter(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    name: str = Field(..., min_length=1, max_length=100)
    race: str = Field(..., min_length=1)
    subrace: Optional[str] = ""
    class_name: str = Field(..., min_length=1)
    subclass: Optional[str] = ""
    level: int = Field(default=1, ge=1, le=20)
    strength: int = Field(default=10, ge=3, le=20)
    dexterity: int = Field(default=10, ge=3, le=20)
    constitution: int = Field(default=10, ge=3, le=20)
    intelligence: int = Field(default=10, ge=3, le=20)
    wisdom: int = Field(default=10, ge=3, le=20)
    charisma: int = Field(default=10, ge=3, le=20)
    asi_picks: list[str] = []
    background: Optional[str] = ""
    alignment: Optional[str] = ""
    hp_max: int = Field(default=10, ge=1, le=999)
    gp: int = Field(default=0, ge=0)
    cp: int = Field(default=0, ge=0)

    @field_validator("subrace", "subclass", "background", "alignment", mode="before")
    @classmethod
    def none_to_empty(cls, v: Optional[str]) -> str:
        return v or ""

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be blank")
        return v


class AddSpell(BaseModel):
    name: str = Field(..., min_length=1)
    level: int = Field(..., ge=0, le=9)
    prepared: bool = False
    slots_max: Optional[int] = None


class EditASI(BaseModel):
    level: int = Field(..., ge=1, le=20)
    entry: dict | None = None


class ApplyLevelUp(BaseModel):
    target_level: int = Field(..., ge=1, le=20)
    class_to_level: dict[str, int] = Field(default_factory=dict)
    # Optional choice systems — each endpoint validates structure at runtime
    asi_choices: dict[str, dict | str] = {}
    expertise_skills: list[str] = []
    metamagic: list[str] = []
    invocations: list[str] = []
    maneuvers: list[str] = []
    fighting_style: Optional[str] = ""
    pact_boon: Optional[str] = ""
    totem_choices: list[str] = []
    hunters_prey: Optional[str] = ""
    favored_enemy: Optional[str] = ""
    favored_terrain: Optional[str] = ""
    infusions: list[str] = []
    magical_secrets: list[str] = []
    feat: Optional[str] = ""

    @field_validator("fighting_style", "pact_boon", "hunters_prey", "favored_enemy", "favored_terrain", "feat", mode="before")
    @classmethod
    def none_to_empty(cls, v: Optional[str]) -> str:
        return v or ""


class UpdateCharacter(BaseModel):
    # All fields optional — only provided fields are updated
    hp_current: Optional[int] = Field(None, ge=0)
    hp_max: Optional[int] = Field(None, ge=1)
    temp_hp: Optional[int] = Field(None, ge=0)
    ac: Optional[int] = Field(None, ge=0)
    level: Optional[int] = Field(None, ge=1, le=20)
    name: Optional[str] = None
    race: Optional[str] = None
    subrace: Optional[str] = None
    class_name: Optional[str] = None
    subclass: Optional[str] = None
    background: Optional[str] = None
    alignment: Optional[str] = None
    gp: Optional[int] = None
    cp: Optional[int] = None

    class Config:
        extra = "allow"  # Accept all allowed fields from the existing handler
