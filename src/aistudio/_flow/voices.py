"""Flow's preset character voices, as its "Select a voice" dialog lists them (live, 2026-11-02).

A character stores a preset by its lower-case name and a custom voice by the media id of the
voice's sample (``audio_references``, findings "Flow character voices").
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PresetVoice:
    name: str
    gender: str
    style: str
    pitch: str

    @property
    def ref(self) -> str:
        """What a character stores for this voice (live: "laomedeia")."""
        return self.name.lower()


def _voice(line: str) -> PresetVoice:
    name, gender, style, pitch = (part.strip() for part in line.split("|"))
    return PresetVoice(name, gender, style, pitch)


PRESET_VOICES: tuple[PresetVoice, ...] = tuple(_voice(line) for line in """
Achernar|Female|soft|high pitch
Achird|Male|friendly|mid pitch
Algenib|Male|gravelly|low pitch
Algieba|Male|easy-going|mid-low pitch
Alnilam|Male|firm|mid-low pitch
Aoede|Female|breezy|mid pitch
Autonoe|Female|bright|mid pitch
Callirrhoe|Female|easy-going|mid pitch
Charon|Male|informative|lower pitch
Despina|Female|smooth|mid pitch
Enceladus|Male|breathy|lower pitch
Erinome|Female|clear|mid pitch
Fenrir|Male|excitable|younger pitch
Gacrux|Female|mature|mid pitch
Iapetus|Male|clear|mid-low pitch
Kore|Female|firm|mid pitch
Laomedeia|Female|upbeat|mid-high pitch
Leda|Female|youthful|mid-high pitch
Orus|Male|firm|mid-low pitch
Puck|Male|upbeat|mid pitch
Pulcherrima|Ungendered|forward|mid-high pitch
Rasalgethi|Male|informative|mid pitch
Sadachbia|Male|lively|low pitch
Sadaltager|Male|knowledgeable|mid pitch
Schedar|Male|even|mid-low pitch
Sulafat|Female|warm|mid pitch
Umbriel|Male|smooth|lower pitch
Vindemiatrix|Female|gentle|mid pitch
Zephyr|Female|bright|mid-high pitch
Zubenelgenubi|Male|casual|mid-low pitch
""".strip().splitlines())


def preset_voice(name: str) -> PresetVoice | None:
    """The preset with this name or stored ref, ignoring case."""
    key = name.strip().lower()
    return next((v for v in PRESET_VOICES if v.ref == key), None)
