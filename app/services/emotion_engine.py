"""Emotion modulation engine for TTS.

Bridges OS (orchestration) with TTS (synthesis):
- OS decides WHAT emotion to apply
- TTS engine executes HOW to apply it (pitch, speed, tone, prosody)
"""

from typing import Optional, Dict, List, NamedTuple
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class EmotionProfile:
    """Emotion parameters for TTS execution."""

    # Core pitch adjustments (relative to baseline)
    pitch_shift: float = 0.0  # -2.0 to +2.0 semitones

    # Speed/pacing adjustments
    speed_multiplier: float = 1.0  # 0.5 to 2.0x

    # Tone sharpness (0=smooth, 1=default, 2=very sharp)
    tone_sharpness: float = 1.0

    # Prosody intensity (emotional emphasis)
    prosody_intensity: float = 1.0  # 0.5 to 2.0x

    # Energy level (affects volume dynamics)
    energy_level: float = 1.0  # 0.5 to 2.0x

    # Breathiness (0=crisp, 1=default, 2=very breathy)
    breathiness: float = 1.0

    # Tension (jaw/throat tightness effect)
    tension: float = 1.0  # 0.5 to 2.0x


# Predefined emotion presets
EMOTION_PRESETS: Dict[str, EmotionProfile] = {
    "neutral": EmotionProfile(
        pitch_shift=0.0, speed_multiplier=1.0, tone_sharpness=1.0,
        prosody_intensity=1.0, energy_level=1.0, breathiness=1.0, tension=1.0,
    ),
    "excited": EmotionProfile(
        pitch_shift=+1.5, speed_multiplier=1.3, tone_sharpness=1.3,
        prosody_intensity=1.8, energy_level=1.8, breathiness=1.2, tension=1.2,
    ),
    "urgent": EmotionProfile(
        pitch_shift=+2.0, speed_multiplier=1.4, tone_sharpness=1.5,
        prosody_intensity=2.0, energy_level=1.9, breathiness=0.8, tension=1.8,
    ),
    "angry": EmotionProfile(
        pitch_shift=+1.0, speed_multiplier=1.2, tone_sharpness=1.8,
        prosody_intensity=2.0, energy_level=2.0, breathiness=0.6, tension=2.0,
    ),
    "calm": EmotionProfile(
        pitch_shift=-0.8, speed_multiplier=0.85, tone_sharpness=0.8,
        prosody_intensity=0.7, energy_level=0.7, breathiness=1.3, tension=0.7,
    ),
    "sad": EmotionProfile(
        pitch_shift=-1.5, speed_multiplier=0.75, tone_sharpness=0.6,
        prosody_intensity=0.8, energy_level=0.6, breathiness=1.5, tension=0.8,
    ),
    "whisper": EmotionProfile(
        pitch_shift=-1.0, speed_multiplier=0.8, tone_sharpness=0.5,
        prosody_intensity=0.5, energy_level=0.4, breathiness=2.0, tension=0.5,
    ),
    "confident": EmotionProfile(
        pitch_shift=+0.5, speed_multiplier=0.9, tone_sharpness=1.3,
        prosody_intensity=1.3, energy_level=1.4, breathiness=0.8, tension=1.4,
    ),
    "concerned": EmotionProfile(
        pitch_shift=-0.5, speed_multiplier=0.9, tone_sharpness=1.1,
        prosody_intensity=1.2, energy_level=0.9, breathiness=1.2, tension=1.3,
    ),
    "playful": EmotionProfile(
        pitch_shift=+1.2, speed_multiplier=1.15, tone_sharpness=1.2,
        prosody_intensity=1.5, energy_level=1.6, breathiness=1.4, tension=0.9,
    ),
}


# ---------------------------------------------------------------------------
# Prosody template resolved type (public immutable API)
# ---------------------------------------------------------------------------

class ProsodyTemplateResolved(NamedTuple):
    """Named prosody preset — expanded, immutable form returned to callers."""
    emotion: str
    secondary_emotion: Optional[str]
    emotion_blend: float        # [0.0, 1.0]
    emotion_intensity: float    # [0.0, 2.0]
    emotion_curve: str          # static | rise | fall | arc | wave
    description: str


# ---------------------------------------------------------------------------
# Template axis + parametric template definition
# ---------------------------------------------------------------------------

@dataclass
class TemplateAxis:
    """A tunable dimension of a prosody template.

    Axis value is always normalised to [0.0, 1.0].  All effects scale linearly
    with the axis value so callers get smooth, predictable control.
    """
    description: str
    default: float = 0.5              # neutral position
    d_intensity: float = 0.0          # delta added to emotion_intensity at axis=1.0
    d_blend: float = 0.0              # delta added to emotion_blend at axis=1.0 (clamped [0,1])
    override_curve: Optional[str] = None   # adopt this curve when axis > 0.7


@dataclass
class ProsodyTemplateDefinition:
    """Full parametric definition of a named prosody template.

    The public API returns the immutable ProsodyTemplateResolved; this class
    holds the mutable axes and resolves them on demand.
    """
    emotion: str
    secondary_emotion: Optional[str]
    emotion_blend: float
    emotion_intensity: float
    emotion_curve: str
    description: str
    axes: Dict[str, TemplateAxis] = field(default_factory=dict)

    def resolve(self, **axis_overrides: float) -> ProsodyTemplateResolved:
        """Apply axis overrides and return an immutable resolved template."""
        intensity = self.emotion_intensity
        blend = self.emotion_blend
        curve = self.emotion_curve

        for ax_name, ax_val in axis_overrides.items():
            if ax_name not in self.axes:
                continue
            ax = self.axes[ax_name]
            v = max(0.0, min(1.0, float(ax_val)))
            intensity = max(0.0, min(2.0, intensity + ax.d_intensity * v))
            blend = max(0.0, min(1.0, blend + ax.d_blend * v))
            if ax.override_curve and v > 0.7:
                curve = ax.override_curve

        return ProsodyTemplateResolved(
            emotion=self.emotion,
            secondary_emotion=self.secondary_emotion,
            emotion_blend=blend,
            emotion_intensity=intensity,
            emotion_curve=curve,
            description=self.description,
        )


# ---------------------------------------------------------------------------
# Built-in prosody template registry
# ---------------------------------------------------------------------------

PROSODY_TEMPLATES: Dict[str, ProsodyTemplateDefinition] = {

    # Persuasion / Sales
    "sales_call": ProsodyTemplateDefinition(
        emotion="confident",
        secondary_emotion="excited",
        emotion_blend=0.30,
        emotion_intensity=1.20,
        emotion_curve="arc",
        description=(
            "Energetic, persuasive tone. Builds through the phrase then settles. "
            "Ideal for pitches, CTAs, and sales narration."
        ),
        axes={
            "urgency": TemplateAxis(
                description="Ramp intensity and tension for time-critical messages.",
                default=0.5, d_intensity=+0.40, d_blend=-0.10, override_curve="rise",
            ),
            "warmth": TemplateAxis(
                description="Soften edges; increase blend toward excited secondary.",
                default=0.5, d_intensity=+0.10, d_blend=+0.20,
            ),
        },
    ),

    # Narrative / Long-form
    "storytelling": ProsodyTemplateDefinition(
        emotion="playful",
        secondary_emotion="calm",
        emotion_blend=0.40,
        emotion_intensity=1.10,
        emotion_curve="wave",
        description=(
            "Dynamic, varied cadence with wave-shaped energy. "
            "Keeps listener attention across long passages. "
            "Best for audiobooks, explainers, and narrative content."
        ),
        axes={
            "wonder": TemplateAxis(
                description="Amplify awe and discovery; higher pitch variation.",
                default=0.5, d_intensity=+0.30, d_blend=-0.10,
            ),
            "tension": TemplateAxis(
                description="Build suspense; tighter phrasing, less calm secondary.",
                default=0.5, d_intensity=+0.20, d_blend=-0.15, override_curve="rise",
            ),
        },
    ),

    # Authority / Corporate
    "executive_brief": ProsodyTemplateDefinition(
        emotion="confident",
        secondary_emotion="calm",
        emotion_blend=0.45,
        emotion_intensity=1.00,
        emotion_curve="fall",
        description=(
            "Measured, authoritative delivery with a falling cadence. "
            "Projects command without aggression. "
            "Ideal for board updates, investor briefings, and announcements."
        ),
        axes={
            "authority": TemplateAxis(
                description="Increase gravitas; reduce calm blend, raise intensity.",
                default=0.5, d_intensity=+0.30, d_blend=-0.15,
            ),
            "accessibility": TemplateAxis(
                description="Soften formal edge; increase calm blend for broader audiences.",
                default=0.5, d_intensity=-0.10, d_blend=+0.20,
            ),
        },
    ),

    # Ghost Signature
    "raven_mode": ProsodyTemplateDefinition(
        emotion="confident",
        secondary_emotion="angry",
        emotion_blend=0.18,
        emotion_intensity=1.30,
        emotion_curve="static",
        description=(
            "Ghost\'s signature voice profile — commanding, intense, and unflinching. "
            "Flat temporal curve with elevated tension. "
            "Use for dramatic reveals, warnings, and high-stakes moments."
        ),
        axes={
            "menace": TemplateAxis(
                description="Push toward the angry secondary; increase raw intensity.",
                default=0.5, d_intensity=+0.40, d_blend=+0.12,
            ),
            "control": TemplateAxis(
                description="Dial back aggression; slower, more deliberate delivery.",
                default=0.5, d_intensity=-0.20, d_blend=-0.08,
            ),
        },
    ),

    # Support / Empathy
    "empathy_support": ProsodyTemplateDefinition(
        emotion="concerned",
        secondary_emotion="calm",
        emotion_blend=0.40,
        emotion_intensity=0.80,
        emotion_curve="fall",
        description=(
            "Warm, gentle, decelerating tone that signals care. "
            "Avoids clinical flatness. "
            "Ideal for support agents, mental health contexts, and apology flows."
        ),
        axes={
            "warmth": TemplateAxis(
                description="Increase calm blend; softer, more reassuring delivery.",
                default=0.5, d_intensity=-0.10, d_blend=+0.20,
            ),
            "concern": TemplateAxis(
                description="Lean into concerned primary; signals deeper engagement.",
                default=0.5, d_intensity=+0.20, d_blend=-0.10,
            ),
        },
    ),

    # Alerts / Urgency
    "urgent_alert": ProsodyTemplateDefinition(
        emotion="urgent",
        secondary_emotion=None,
        emotion_blend=0.0,
        emotion_intensity=1.50,
        emotion_curve="rise",
        description=(
            "Maximum urgency with rising energy contour. "
            "Short, clipped, high tension. "
            "Use for system alerts, emergency notices, and time-critical prompts."
        ),
        axes={
            "severity": TemplateAxis(
                description="Increase absolute intensity for the most critical alerts.",
                default=0.5, d_intensity=+0.35,
            ),
            "clarity": TemplateAxis(
                description="Slightly slower, crisper delivery for high-noise environments.",
                default=0.5, d_intensity=+0.15,
            ),
        },
    ),

    # Trust / Advisory
    "trusted_advisor": ProsodyTemplateDefinition(
        emotion="calm",
        secondary_emotion="confident",
        emotion_blend=0.35,
        emotion_intensity=0.95,
        emotion_curve="arc",
        description=(
            "Balanced, unhurried, arc-shaped delivery that builds credibility. "
            "Calm foundation with confident undertone. "
            "Ideal for financial, legal, and medical guidance contexts."
        ),
        axes={
            "gravitas": TemplateAxis(
                description="Increase authoritative undertone; reduce calm, boost confident.",
                default=0.5, d_intensity=+0.20, d_blend=-0.10,
            ),
            "warmth": TemplateAxis(
                description="Softer, more approachable; increase confident blend.",
                default=0.5, d_intensity=-0.05, d_blend=+0.15,
            ),
        },
    ),

    # Hype / Launch
    "hype_mode": ProsodyTemplateDefinition(
        emotion="excited",
        secondary_emotion="playful",
        emotion_blend=0.25,
        emotion_intensity=1.60,
        emotion_curve="rise",
        description=(
            "High-energy, ascending excitement. "
            "Peaks toward phrase end. "
            "Ideal for product launches, trailers, and event openers."
        ),
        axes={
            "energy": TemplateAxis(
                description="Pure intensity boost; pushes toward peak excitement.",
                default=0.5, d_intensity=+0.30, d_blend=-0.10,
            ),
            "playfulness": TemplateAxis(
                description="Increase playful blend; lighter, more fun delivery.",
                default=0.5, d_intensity=+0.10, d_blend=+0.20,
            ),
        },
    ),
}


# ---------------------------------------------------------------------------
# Hierarchical prosody types
# ---------------------------------------------------------------------------

@dataclass
class WordEmphasis:
    """Emphasis directive for a single word position."""
    word_index: int              # 0-based index in text.split()
    energy_boost: float = 1.35  # amplitude multiplier (1.0 = no change)
    pitch_semitones: float = 1.0  # pitch shift in semitones (positive = higher)


@dataclass
class PauseDirective:
    """Silence injection after a specific word."""
    after_word_index: int    # insert silence after this word
    duration_ms: int = 200   # silence length in milliseconds


@dataclass
class HierarchicalProsodyDirective:
    """Complete phrase-level + word-level prosody specification."""
    sentence_template: str
    word_emphases: List[WordEmphasis] = field(default_factory=list)
    pause_directives: List[PauseDirective] = field(default_factory=list)
    auto_emphasis: bool = True
    auto_pauses: bool = True
    pause_scale: float = 1.0


# ---------------------------------------------------------------------------
# TextAnalyzer — derives directives from raw text (rule-based, no ML)
# ---------------------------------------------------------------------------

class TextAnalyzer:
    """Derives hierarchical prosody directives from raw text."""

    PAUSE_MAP: Dict[str, int] = {
        ",":   150,
        ";":   220,
        ":":   200,
        ".":   350,
        "!":   300,
        "?":   300,
        "\u2014":   250,
        "\u2013":   200,
        "...": 450,
    }

    _STOPWORDS: frozenset = frozenset({
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "with", "is", "are", "was", "were", "be", "been",
        "have", "has", "had", "do", "does", "did", "will", "would",
        "can", "could", "i", "you", "we", "they", "it", "this", "that",
    })

    @classmethod
    def build_directive(
        cls,
        text: str,
        template: str,
        auto_emphasis: bool = True,
        auto_pauses: bool = True,
        pause_scale: float = 1.0,
        extra_emphases: Optional[List[WordEmphasis]] = None,
        extra_pauses: Optional[List[PauseDirective]] = None,
    ) -> HierarchicalProsodyDirective:
        """Build a HierarchicalProsodyDirective from text + optional overrides."""
        words = text.split()
        emphases: List[WordEmphasis] = list(extra_emphases or [])
        pauses: List[PauseDirective] = list(extra_pauses or [])

        if auto_emphasis:
            emphases.extend(cls._detect_emphases(words))
        if auto_pauses:
            pauses.extend(cls._detect_pauses(text, words, pause_scale))

        return HierarchicalProsodyDirective(
            sentence_template=template,
            word_emphases=emphases,
            pause_directives=pauses,
            auto_emphasis=auto_emphasis,
            auto_pauses=auto_pauses,
            pause_scale=pause_scale,
        )

    @classmethod
    def _detect_emphases(cls, words: List[str]) -> List[WordEmphasis]:
        """Detect words that carry emphasis signals."""
        emphases: List[WordEmphasis] = []
        for i, raw_word in enumerate(words):
            clean = raw_word.strip(".,!?;:\"'()-\u2014*_")

            # ALL_CAPS (>=3 chars, not a stopword)
            if clean.isupper() and len(clean) >= 3 and clean.lower() not in cls._STOPWORDS:
                emphases.append(WordEmphasis(word_index=i, energy_boost=1.50, pitch_semitones=1.5))
                continue

            # *starred* or _underscored_ markers
            if (raw_word.startswith("*") and raw_word.endswith("*")) or \
               (raw_word.startswith("_") and raw_word.endswith("_")):
                emphases.append(WordEmphasis(word_index=i, energy_boost=1.40, pitch_semitones=1.0))
                continue

            # Numerals with currency/percent symbols
            if any(c in raw_word for c in ("$", "%", "\u20ac", "\u00a3")) and any(c.isdigit() for c in raw_word):
                emphases.append(WordEmphasis(word_index=i, energy_boost=1.30, pitch_semitones=0.5))

        return emphases

    @classmethod
    def _detect_pauses(
        cls, text: str, words: List[str], pause_scale: float
    ) -> List[PauseDirective]:
        """Derive pause directives from punctuation in the text."""
        pauses: List[PauseDirective] = []
        remaining = text

        for i, word in enumerate(words):
            start = remaining.find(word)
            if start == -1:
                continue
            after = start + len(word)
            trailing = remaining[after:after + 6]

            for punct, base_ms in cls.PAUSE_MAP.items():
                if trailing.lstrip().startswith(punct):
                    ms = max(50, int(base_ms * pause_scale))
                    pauses.append(PauseDirective(after_word_index=i, duration_ms=ms))
                    break

            remaining = remaining[after:]

        return pauses


# ---------------------------------------------------------------------------
# TemplateSelector — context-aware OS-layer auto-selection
# ---------------------------------------------------------------------------

class TemplateSelector:
    """Rule-based OS-layer auto-selection of prosody template from text."""

    _SIGNAL_MAP: List[tuple] = [
        (frozenset({"urgent", "emergency", "immediately", "critical", "warning",
                    "alert", "asap", "danger", "breach", "failure", "error"}),
         "urgent_alert", 0.40),
        (frozenset({"offer", "deal", "buy", "purchase", "limited", "exclusive",
                    "opportunity", "save", "discount", "free", "today", "only"}),
         "sales_call", 0.35),
        (frozenset({"once", "story", "imagine", "then", "suddenly", "journey",
                    "adventure", "discover", "legend", "tale", "moment"}),
         "storytelling", 0.30),
        (frozenset({"report", "update", "quarter", "strategy", "board", "results",
                    "summary", "performance", "revenue", "forecast", "outlook"}),
         "executive_brief", 0.30),
        (frozenset({"sorry", "apologize", "understand", "difficult", "help",
                    "support", "concern", "frustration", "care", "here", "listen"}),
         "empathy_support", 0.35),
        (frozenset({"launch", "announcing", "introducing", "reveal", "world",
                    "first", "biggest", "epic", "incredible", "amazing", "new"}),
         "hype_mode", 0.35),
    ]

    @classmethod
    def select(cls, text: str) -> tuple:
        """Return (template_name, confidence) for the given text."""
        lowered = text.lower()
        words = set(lowered.split())
        scores: Dict[str, float] = {}

        for signal_words, template, increment in cls._SIGNAL_MAP:
            hits = len(signal_words & words)
            if hits:
                scores[template] = scores.get(template, 0.0) + increment * min(hits, 3) / 3

        if "!" in text:
            scores["hype_mode"] = scores.get("hype_mode", 0.0) + 0.20
        if "?" in text:
            scores["trusted_advisor"] = scores.get("trusted_advisor", 0.0) + 0.15

        if len(text.split()) > 30 and "urgent_alert" in scores:
            scores["urgent_alert"] *= 0.5

        if not scores:
            return ("neutral", 0.0)

        best = max(scores, key=lambda k: scores[k])
        return (best, min(1.0, scores[best]))

    @classmethod
    def select_and_resolve(
        cls,
        text: str,
        min_confidence: float = 0.25,
    ) -> Optional[ProsodyTemplateResolved]:
        """Select a template and resolve it, or return None if confidence is too low."""
        name, confidence = cls.select(text)
        if confidence < min_confidence or name == "neutral":
            return None
        return EmotionEngine.resolve_template(name)


# ---------------------------------------------------------------------------
# EmotionEngine — OS-layer interface
# ---------------------------------------------------------------------------

class EmotionEngine:
    """OS layer: Manages emotion selection and profile retrieval."""

    @staticmethod
    def get_emotion_profile(emotion: str) -> EmotionProfile:
        """OS-layer interface: Retrieve emotion profile by name."""
        if emotion not in EMOTION_PRESETS:
            available = ", ".join(EMOTION_PRESETS.keys())
            raise ValueError(f"Emotion \'{emotion}\' not found. Available: {available}")
        return EMOTION_PRESETS[emotion]

    @staticmethod
    def blend_emotions(
        primary: str,
        secondary: Optional[str] = None,
        weight: float = 0.5,
    ) -> EmotionProfile:
        """OS-layer interface: Blend two emotions."""
        if not secondary:
            return EmotionEngine.get_emotion_profile(primary)

        primary_profile = EmotionEngine.get_emotion_profile(primary)
        secondary_profile = EmotionEngine.get_emotion_profile(secondary)

        blended = EmotionProfile(
            pitch_shift=(
                primary_profile.pitch_shift * (1 - weight) +
                secondary_profile.pitch_shift * weight
            ),
            speed_multiplier=(
                primary_profile.speed_multiplier * (1 - weight) +
                secondary_profile.speed_multiplier * weight
            ),
            tone_sharpness=(
                primary_profile.tone_sharpness * (1 - weight) +
                secondary_profile.tone_sharpness * weight
            ),
            prosody_intensity=(
                primary_profile.prosody_intensity * (1 - weight) +
                secondary_profile.prosody_intensity * weight
            ),
            energy_level=(
                primary_profile.energy_level * (1 - weight) +
                secondary_profile.energy_level * weight
            ),
            breathiness=(
                primary_profile.breathiness * (1 - weight) +
                secondary_profile.breathiness * weight
            ),
            tension=(
                primary_profile.tension * (1 - weight) +
                secondary_profile.tension * weight
            ),
        )
        logger.info(f"Blended emotions: {primary}({1-weight:.1%}) + {secondary}({weight:.1%})")
        return blended

    @staticmethod
    def list_emotions() -> list:
        """OS-layer interface: Get all available emotions."""
        return list(EMOTION_PRESETS.keys())

    @staticmethod
    def validate_custom_profile(profile: Dict) -> EmotionProfile:
        """OS-layer interface: Validate and convert custom emotion profile."""
        try:
            return EmotionProfile(**profile)
        except TypeError as e:
            raise ValueError(f"Invalid emotion profile: {e}")

    @staticmethod
    def build_profile(
        emotion: Optional[str],
        secondary_emotion: Optional[str] = None,
        secondary_weight: float = 0.0,
        intensity: float = 1.0,
    ) -> EmotionProfile:
        """Build a final execution profile from optional primary/secondary emotions."""
        primary = emotion or "neutral"
        weight = max(0.0, min(1.0, secondary_weight))
        scale = max(0.0, min(2.0, intensity))

        if secondary_emotion:
            base = EmotionEngine.blend_emotions(primary, secondary_emotion, weight)
        else:
            base = EmotionEngine.get_emotion_profile(primary)

        return EmotionProfile(
            pitch_shift=base.pitch_shift * scale,
            speed_multiplier=1.0 + ((base.speed_multiplier - 1.0) * scale),
            tone_sharpness=1.0 + ((base.tone_sharpness - 1.0) * scale),
            prosody_intensity=1.0 + ((base.prosody_intensity - 1.0) * scale),
            energy_level=1.0 + ((base.energy_level - 1.0) * scale),
            breathiness=1.0 + ((base.breathiness - 1.0) * scale),
            tension=1.0 + ((base.tension - 1.0) * scale),
        )

    @staticmethod
    def resolve_template(
        template_name: str,
        **axis_overrides: float,
    ) -> ProsodyTemplateResolved:
        """OS-layer interface: Expand a named prosody template into emotion fields.

        Optional axis_overrides apply parametric tuning before resolution.
        Unknown axis names are silently ignored so callers stay future-proof.
        """
        if template_name not in PROSODY_TEMPLATES:
            available = ", ".join(PROSODY_TEMPLATES.keys())
            raise ValueError(
                f"Prosody template \'{template_name}\' not found. Available: {available}"
            )
        return PROSODY_TEMPLATES[template_name].resolve(**axis_overrides)

    @staticmethod
    def list_templates() -> list:
        """OS-layer interface: Get all available prosody template names."""
        return list(PROSODY_TEMPLATES.keys())

    @staticmethod
    def compose_templates(
        templates: List[str],
        weights: Optional[List[float]] = None,
        axis_overrides: Optional[List[Optional[Dict[str, float]]]] = None,
    ) -> EmotionProfile:
        """Blend N templates into a single EmotionProfile (behaviour stacking).

        Example::

            profile = EmotionEngine.compose_templates(
                ["sales_call", "empathy_support"],
                weights=[0.70, 0.30],
                axis_overrides=[{"urgency": 0.6}, None],
            )
        """
        if not templates:
            raise ValueError("compose_templates requires at least one template")

        n = len(templates)
        if weights is None:
            weights = [1.0] * n
        if len(weights) != n:
            raise ValueError("len(weights) must equal len(templates)")
        if axis_overrides is None:
            axis_overrides = [None] * n

        w_total = sum(max(0.0, w) for w in weights)
        if w_total == 0.0:
            raise ValueError("All template weights are zero or negative")

        profiles: List[EmotionProfile] = []
        norm_weights: List[float] = []

        for tmpl_name, w, axes in zip(templates, weights, axis_overrides):
            resolved = EmotionEngine.resolve_template(tmpl_name, **(axes or {}))
            profile = EmotionEngine.build_profile(
                emotion=resolved.emotion,
                secondary_emotion=resolved.secondary_emotion,
                secondary_weight=resolved.emotion_blend,
                intensity=resolved.emotion_intensity,
            )
            profiles.append(profile)
            norm_weights.append(max(0.0, w) / w_total)

        def _blend(field_name: str) -> float:
            return sum(
                getattr(p, field_name) * wt
                for p, wt in zip(profiles, norm_weights)
            )

        result = EmotionProfile(
            pitch_shift=_blend("pitch_shift"),
            speed_multiplier=_blend("speed_multiplier"),
            tone_sharpness=_blend("tone_sharpness"),
            prosody_intensity=_blend("prosody_intensity"),
            energy_level=_blend("energy_level"),
            breathiness=_blend("breathiness"),
            tension=_blend("tension"),
        )
        logger.info(
            "Composed templates %s -> pitch_shift=%.2f speed=%.2f",
            templates, result.pitch_shift, result.speed_multiplier,
        )
        return result
