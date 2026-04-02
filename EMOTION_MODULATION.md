# Emotion Modulation Layer

## Architecture Overview

Emotion modulation is a **100% TTS-layer compatible** feature that dynamically shapes speech synthesis behavior per sentence. It implements a clean separation of concerns:

### Two-Layer Design

```
┌─────────────────────────────────────────────────────────────┐
│ OS LAYER (Orchestration Service) - Coordinate             │
│                                                             │
│ • Decides WHAT emotion to apply                           │
│ • Routes emotion + text to TTS engine                     │
│ • Represents business logic & user intent                  │
│                                                             │
│ Interface:                                                 │
│ POST /synthesize {                                         │
│   "text": "Hello world",                                  │
│   "emotion": "excited"  ← OS decides this                │
│ }                                                          │
└─────────────────────────────────────────────────────────────┘
                          ↓↓↓
┌─────────────────────────────────────────────────────────────┐
│ TTS LAYER (Text-to-Speech Engine) - Execute               │
│                                                             │
│ • Receives emotion decision from OS                       │
│ • Applies HOW to modulate synthesis                       │
│ • Affects pitch, pacing, tone, prosody                   │
│ • Manipulates speech signal characteristics              │
│                                                             │
│ Execution:                                                 │
│ apply_emotion("excited") {                                │
│   pitch +2.0              ← TTS executes                  │
│   speed 1.3x              ← TTS executes                  │
│   prosody_intensity 1.8x  ← TTS executes                  │
│   energy_level 1.8x       ← TTS executes                  │
│ }                                                          │
└─────────────────────────────────────────────────────────────┘
```

## Feature Map

Each emotion modulates these core TTS parameters:

| Emotion | Pitch | Speed | Tone | Prosody | Energy | Breathiness | Tension |
|---------|-------|-------|------|---------|--------|-------------|---------|
| **neutral** | 0.0 | 1.0x | 1.0 | 1.0x | 1.0x | 1.0 | 1.0 |
| **excited** | +1.5 | 1.3x | 1.3 | 1.8x | 1.8x | 1.2 | 1.2 |
| **urgent** | +2.0 | 1.4x | 1.5 | 2.0x | 1.9x | 0.8 | 1.8 |
| **angry** | +1.0 | 1.2x | 1.8 | 2.0x | 2.0x | 0.6 | 2.0 |
| **calm** | -0.8 | 0.85x | 0.8 | 0.7x | 0.7x | 1.3 | 0.7 |
| **sad** | -1.5 | 0.75x | 0.6 | 0.8x | 0.6x | 1.5 | 0.8 |
| **whisper** | -1.0 | 0.8x | 0.5 | 0.5x | 0.4x | 2.0 | 0.5 |
| **confident** | +0.5 | 0.9x | 1.3 | 1.3x | 1.4x | 0.8 | 1.4 |
| **concerned** | -0.5 | 0.9x | 1.1 | 1.2x | 0.9x | 1.2 | 1.3 |
| **playful** | +1.2 | 1.15x | 1.2 | 1.5x | 1.6x | 1.4 | 0.9 |

## Parameter Reference

### Core Modulation Parameters

**pitch_shift** (semitones: -2.0 to +2.0)
- Controls fundamental frequency
- Positive: higher pitch (excited, confident)
- Negative: lower pitch (calm, sad)

**speed_multiplier** (0.5x to 2.0x)
- Controls speech rate and pacing
- > 1.0: faster delivery (urgent, excited)
- < 1.0: slower, deliberate speech (calm, sad)

**tone_sharpness** (0.5 to 2.0)
- Controls high-frequency content
- > 1.0: brighter, sharper tone (urgent, angry)
- < 1.0: softer, rounder tone (sad, calm)

**prosody_intensity** (0.5x to 2.0x)
- Controls natural pitch variations & inflection
- Higher: more exaggerated emotional expression
- Lower: flatter, more monotone delivery

**energy_level** (0.5x to 2.0x)
- Controls amplitude dynamics and vigor
- Higher: louder, more forceful (excited, urgent)
- Lower: quieter, more subdued (sad, whisper)

**breathiness** (0.5 to 2.0)
- Simulates vocal air flow and noise floor
- > 1.0: aspirated/breathy voice (playful, sad)
- < 1.0: crisp, clean edge (urgent, angry)

**tension** (0.5x to 2.0x)
- Controls vocal tract tension & formant structure
- Higher: tense, straining voice (angry, urgent)
- Lower: relaxed, loose voice (playful, calm)

## API Usage

### Basic Emotion Synthesis

```bash
curl -X POST http://localhost:8000/synthesize \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "This is absolutely exciting!",
    "voice_id": "voice-123",
    "emotion": "excited"
  }'
```

### Blending Emotions

Blend two emotions for nuanced delivery:

```python
from app.services.emotion_engine import EmotionEngine

# 70% excited, 30% calm
blended = EmotionEngine.blend_emotions(
    primary="excited",
    secondary="calm",
    weight=0.3  # 0 = all primary, 1 = all secondary
)
```

### Available Emotions

```python
from app.services.emotion_engine import EmotionEngine

emotions = EmotionEngine.list_emotions()
# ['neutral', 'excited', 'urgent', 'angry', 'calm', 'sad', 
#  'whisper', 'confident', 'concerned', 'playful']
```

### Custom Emotion Profile

```python
from app.services.emotion_engine import EmotionProfile, EmotionEngine

# Create custom profile
custom = {
    "pitch_shift": 1.0,
    "speed_multiplier": 1.2,
    "tone_sharpness": 1.4,
    "prosody_intensity": 1.5,
    "energy_level": 1.6,
    "breathiness": 0.9,
    "tension": 1.3,
}

# Validate and use
profile = EmotionEngine.validate_custom_profile(custom)
```

## Implementation Details

### File Structure

```
app/services/emotion_engine.py      ← OS-layer: emotion coordination
├─ EmotionProfile               ← Data class for parameters
├─ EMOTION_PRESETS              ← 10 predefined emotions
└─ EmotionEngine                ← OS interface

app/services/tts_engine.py          ← TTS-layer: emotion execution
├─ synthesize()                 ← Updated with emotion parameter
├─ _apply_emotion_processing()  ← Main execution method
├─ _apply_tone_sharpness()      ← EQ filtering
├─ _apply_prosody_modulation()  ← Pitch variation
└─ _apply_tension_effect()      ← Formant modification
```

### Synthesis Flow

1. **Request** → POST /synthesize with `emotion="excited"`
2. **OS Layer** → EmotionEngine.get_emotion_profile("excited")
3. **TTS Layer** → synthesize() receives profile
4. **Audio Generation** → TTS generates base audio
5. **Emotion Processing** → _apply_emotion_processing() shapes audio
6. **Signal Processing** → Individual modulation functions applied
7. **Return** → Emotionally modulated audio

### Signal Processing Chain

```
[Raw Audio from TTS]
        ↓
[Energy Level Adjustment] → Scale amplitude
        ↓
[Breathiness Addition] → Harmonic noise floor
        ↓
[Tone Sharpness EQ] → High/low frequency boost/cut
        ↓
[Prosody Modulation] → Pitch variation envelope
        ↓
[Tension Effect] → Formant shaping
        ↓
[Final Emotionally-Modulated Audio]
```

## Testing

### Unit Tests for Emotion Engine

```python
from app.services.emotion_engine import EmotionEngine

# Test emotion profile retrieval
profile = EmotionEngine.get_emotion_profile("excited")
assert profile.pitch_shift == 1.5
assert profile.speed_multiplier == 1.3

# Test emotion blending
blended = EmotionEngine.blend_emotions("excited", "calm", weight=0.5)
assert 0.8 < blended.pitch_shift < 1.0

# Test all emotions available
emotions = EmotionEngine.list_emotions()
assert "excited" in emotions
assert len(emotions) >= 10
```

### Integration Test with TTS

```python
from app.services.tts_engine import TTSEngine

engine = TTSEngine()
engine.initialize()

# Test without emotion
audio1, sr1 = engine.synthesize("Hello world", speed=1.0)

# Test with emotion
audio2, sr2 = engine.synthesize(
    "Hello world", 
    speed=1.0,
    emotion="excited"
)

# Audio should differ
assert not np.array_equal(audio1, audio2)
```

### API Integration Test

```bash
# Test emotion parameter accepted
curl -X POST http://localhost:8000/synthesize \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Test with emotion",
    "voice_id": "voice-123",
    "emotion": "urgent"
  }' | jq .

# Should return synthesis job (not error about unknown field)
```

## Use Cases

### 1. Customer Service Bot
```json
{
  "text": "I understand your frustration and we'll fix this immediately",
  "emotion": "concerned" 
  // Conveys empathy while maintaining professionalism
}
```

### 2. Urgency Indicators
```json
{
  "text": "Critical system alert: Please act now",
  "emotion": "urgent"
  // +2.0 pitch, 1.4x speed conveys immediacy
}
```

### 3. Educational Content
```json
{
  "text": "Let's explore this fascinating concept",
  "emotion": "excited"
  // Engages learners with enthusiasm
}
```

### 4. Meditation/Wellness
```json
{
  "text": "Take a deep breath and relax",
  "emotion": "calm"
  // Slower speech, lower pitch supports relaxation
}
```

### 5. Entertainment/Storytelling
```json
{
  "text": "And then the dragon appeared!",
  "emotion": "playful"
  // Higher energy delivery adds drama
}
```

## Best Practices

### ✅ DO

- Use emotion for user intent, not random variation
- Blend emotions (0.3-0.7 weight) for subtle nuance
- Test with target audience for appropriateness
- Cache results with same text+emotion combination
- Monitor emotion+language interactions (some languages more suited to certain emotions)

### ❌ DON'T

- Apply extreme emotions (urgent/angry) for routine content
- Change emotions mid-sentence (breaks coherence)
- Use whisper emotion for safety-critical announcements
- Assume same emotion works across all languages/cultures
- Forget to validate emotion name before synthesis

## Future Enhancements

### Planned Features

1. **Sentence-Level Emotion** → Parse text, apply different emotions per sentence
2. **Emotion Intensity Control** → Let users scale 0.5-2.0 instead of fixed presets
3. **Emotion Mixing** → Support named combinations like "excited+confident"
4. **Language-Specific Presets** → Adjust baselines per language for cultural appropriateness
5. **Emotion Chaining** → Smooth transitions between emotions in sequence
6. **Acoustic Feature Analysis** → Real-time validation of emotion parameters against target metrics

### Performance Optimization

- Pre-computed emotion filter kernels (for tone sharpness)
- GPU-accelerated signal processing for tone/prosody
- Emotion profile caching by voice+emotion pair
- Batch emotion processing for multiple segments

## Architecture Decisions

### Why Separate OS and TTS Layers?

**Decoupling** → Allows swapping TTS backends without changing emotion logic
**Clarity** → Explicit responsibility: OS decides intent, TTS executes technique
**Extensibility** → Easy to add new emotions (just add EmotionProfile)
**Testing** → Can test OS decision logic independently from audio processing
**Performance** → OS Layer can route to optimal TTS variant based on emotion requirements

### Why Signal Processing Instead of Vocoder?

**Compatibility** → Works with any TTS backend (Tortoise, VITS, etc.)
**Speed** → Post-processing is faster than re-vocoding
**Simplicity** → No dependency on vocoder architecture
**Flexibility** → Can mix multiple processing chains per emotion

### Why These 10 Emotions?

Selected based on:
- High-frequency business use cases (urgent, calm, confident)
- Strong acoustic signatures (easy to distinguish)
- Cross-cultural recognition
- Orthogonal parameter spaces (minimal parameter conflicts)

## Troubleshooting

### Issue: Audio sounds distorted with high emotion intensity

**Solution**: Reduce energy_level or use emotion blending with lower weight

### Issue: Emotion not applied consistently across batch

**Solution**: Ensure same voice_seed and speed/pitch settings in batch request

### Issue: "Emotion not found" error

**Solution**: Check EmotionEngine.list_emotions() for valid options

### Issue: Emotion changes too subtly

**Solution**: Reduce tone_sharpness or increase prosody_intensity for more dramatic effect

## Architecture Compliance

✅ **100% TTS-compatible**
- Operates on audio post-synthesis
- No changes to TTS model architecture
- Works with any speech synthesis backend

✅ **Clean separation of concerns**
- OS layer: decision-making
- TTS layer: execution
- Emotion layer: parameter mapping

✅ **Extensible design**
- Easy to add new emotions
- Support for emotion blending
- Custom profile validation

✅ **Production-ready**
- Error handling and fallbacks
- Comprehensive logging
- Performance-optimized signal processing
