# Emotion Modulation - Quick Reference

## 🎯 The Split

```
┌─────────────────────────────────────────────────────────────┐
│ OS LAYER: Decides WHAT emotion                             │
│  emotion = "urgent"    ← Business logic decides            │
└─────────────────────────────────────────────────────────────┘
                          ↓↓↓
┌─────────────────────────────────────────────────────────────┐
│ TTS LAYER: Executes HOW to apply it                        │
│  pitch +2.0, speed 1.4x, prosody 2.0x, energy 1.9x        │
└─────────────────────────────────────────────────────────────┘
```

## 📝 Emotions (10 presets)

| Name | Use Case | Key Effect |
|------|----------|-----------|
| **neutral** | Baseline | No modification |
| **excited** | Enthusiasm, engagement | Higher pitch, faster |
| **urgent** | Alerts, critical info | Highest pitch, very fast |
| **angry** | Frustration, warnings | Sharper tone, tense |
| **calm** | Meditation, relaxation | Lower pitch, slower |
| **sad** | Empathy, concern | Lowest pitch, slowest |
| **whisper** | Secrets, quiet mode | Minimal energy, breathy |
| **confident** | Authority, expertise | Moderate pitch, steady |
| **concerned** | Empathy, reassurance | Slightly lower pitch |
| **playful** | Entertainment, humor | High energy, engaging |

## 🔧 API Usage

### POST /synthesize with emotion

```bash
curl -X POST http://localhost:8000/synthesize \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "text": "Critical alert!",
    "voice_id": "voice-123",
    "emotion": "urgent"
  }'
```

### Response includes emotion-modulated audio

```json
{
  "id": "job-abc123",
  "status": "pending",
  "audio_url": "/audio/job-abc123.wav",  // with emotion applied
  "mode": "balanced"
}
```

## 💻 Code Integration

### Basic synthesis with emotion

```python
from app.services.tts_engine import get_tts_engine

engine = get_tts_engine()
audio, sr = engine.synthesize(
    text="Hello world",
    emotion="excited"  # ← Emotion parameter
)
```

### Get all available emotions

```python
from app.services.emotion_engine import EmotionEngine

emotions = EmotionEngine.list_emotions()
# ['neutral', 'excited', 'urgent', 'angry', 'calm', 'sad', 
#  'whisper', 'confident', 'concerned', 'playful']
```

### Blend two emotions

```python
from app.services.emotion_engine import EmotionEngine

# 70% excited, 30% calm
profile = EmotionEngine.blend_emotions(
    primary="excited",
    secondary="calm", 
    weight=0.3  # 0 = 100% primary, 1 = 100% secondary
)
```

### Create custom emotion profile

```python
from app.services.emotion_engine import EmotionEngine

custom = {
    "pitch_shift": 1.5,
    "speed_multiplier": 1.3,
    "tone_sharpness": 1.4,
    "prosody_intensity": 1.5,
    "energy_level": 1.6,
    "breathiness": 1.2,
    "tension": 1.2,
}

profile = EmotionEngine.validate_custom_profile(custom)
```

## 📊 Parameter Values

All parameters are multipliers or shifts:

- **pitch_shift**: -2.0 to +2.0 (semitones)
- **speed_multiplier**: 0.5x to 2.0x
- **tone_sharpness**: 0.5 to 2.0 (1.0 = baseline)
- **prosody_intensity**: 0.5x to 2.0x
- **energy_level**: 0.5x to 2.0x
- **breathiness**: 0.5 to 2.0 (1.0 = baseline)
- **tension**: 0.5x to 2.0x

| Value | Effect |
|-------|--------|
| < 1.0 | Reduce (lower energy/sharpness/etc) |
| 1.0 | Neutral/baseline |
| > 1.0 | Enhance (higher energy/sharpness/etc) |

## 🧪 Testing

### Test emotion is accepted

```python
from app.services.emotion_engine import EmotionEngine

profile = EmotionEngine.get_emotion_profile("excited")
assert profile.pitch_shift == 1.5
```

### Test synthesis with emotion

```python
from app.services.tts_engine import get_tts_engine
import numpy as np

engine = get_tts_engine()
audio1, sr = engine.synthesize("Test", emotion="excited")
audio2, sr = engine.synthesize("Test", emotion="calm")

assert not np.array_equal(audio1, audio2)  # Should differ
```

## 🚨 Error Handling

### Invalid emotion name

```python
from app.services.emotion_engine import EmotionEngine

try:
    profile = EmotionEngine.get_emotion_profile("invalid")
except ValueError as e:
    print(f"Invalid emotion: {e}")
    # Fallback to neutral
    profile = EmotionEngine.get_emotion_profile("neutral")
```

### Graceful degradation

```python
emotion = request.emotion or "neutral"  # Default to neutral
audio, sr = engine.synthesize(
    text=request.text,
    emotion=emotion  # Never fails, emotion is optional
)
```

## 📈 Performance Notes

- **Processing overhead**: ~10-50ms per audio segment
- **Post-synthesis**: Emotion applied AFTER TTS synthesis (doesn't slow TTS)
- **Caching**: Results cached with emotion as cache key component
- **Memory**: Minimal - operates on audio signal in-place

## 🎨 Design Principles

1. **OS decides intent** → "This needs urgency"
2. **TTS executes technique** → "Apply +2.0 pitch, 1.4x speed"
3. **100% TTS-compatible** → Works with any TTS backend
4. **Extensible** → Easy to add new emotions
5. **Testable** → OS and TTS logic tested separately

## 📚 Documentation

- Full details: See [EMOTION_MODULATION.md](EMOTION_MODULATION.md)
- Architecture: See "Two-Layer Design" section
- Use cases: See "Use Cases" section
- Best practices: See "Best Practices" section

## ✅ Checklist for Implementation

- [ ] Add `emotion: Optional[str]` to SynthesisRequest
- [ ] Pass emotion to synthesize_text_task
- [ ] Update TTS engine synthesize() signature
- [ ] Add _apply_emotion_processing() to TTS engine
- [ ] Test with each emotion preset
- [ ] Validate error handling for invalid emotions
- [ ] Update API documentation
- [ ] Add emotion parameter to client SDKs

## 🔗 Related Files

| File | Purpose |
|------|---------|
| `app/services/emotion_engine.py` | OS Layer: Emotion profiles & presets |
| `app/services/tts_engine.py` | TTS Layer: Audio processing |
| `app/schemas/tts.py` | API Schema with emotion field |
| `app/main.py` | Synthesis endpoint integration |
| `app/tasks/synthesis.py` | Async task with emotion |
| `tests/test_emotion_modulation.py` | Comprehensive test suite |
| `EMOTION_MODULATION.md` | Full documentation |

---

**Remember**: Emotion is OS layer (coordination) + TTS layer (execution). Clean separation = maintainable, testable, extensible.
