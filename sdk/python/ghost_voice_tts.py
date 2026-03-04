"""
Ghost Voice TTS Python SDK

Installation:
    pip install ghost-voice-tts

Quick Start:
    from ghost_voice_tts import GhostVoiceTTS
    
    client = GhostVoiceTTS(api_key="sk_...")
    
    # Synthesize text
    audio = client.synthesize("Hello, world!", voice_id="v-123")
    audio.save("output.mp3")
    
    # Streaming synthesis
    for chunk in client.synthesize_stream("Long text...", voice_id="v-123"):
        play_audio_chunk(chunk)
"""

import requests
import json
from typing import Optional, List, Dict, Any, Generator
from dataclasses import dataclass
from datetime import datetime
import io


@dataclass
class Audio:
    """Audio data with metadata."""
    data: bytes
    format: str = "mp3"
    duration_seconds: Optional[float] = None
    sample_rate: int = 22050
    
    def save(self, path: str) -> None:
        """Save audio to file."""
        with open(path, 'wb') as f:
            f.write(self.data)
    
    def to_bytes(self) -> bytes:
        """Get raw audio bytes."""
        return self.data


@dataclass
class Voice:
    """Voice metadata."""
    id: str
    name: str
    description: Optional[str]
    gender: Optional[str]
    accent: Optional[str]
    language: str
    quality_score: float
    is_public: bool
    created_at: datetime


@dataclass
class SynthesisJob:
    """Synthesis job status."""
    id: str
    status: str  # pending, processing, completed, failed
    progress: float  # 0-1
    audio_url: Optional[str] = None
    audio_duration: Optional[float] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class GhostVoiceTTS:
    """
    Client for Ghost Voice TTS API.
    
    Args:
        api_key: Your API key (starts with 'sk_')
        base_url: API base URL (default: https://api.ghostvoice.tts)
        timeout: Request timeout in seconds
    """
    
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.ghostvoice.tts",
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})
    
    def synthesize(
        self,
        text: str,
        voice_id: str,
        language: str = "en",
        style: str = "normal",
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> Audio:
        """
        Synthesize text to speech.
        
        Args:
            text: Text to synthesize (up to 5000 chars)
            voice_id: Voice ID to use
            language: Language code (en, es, fr, etc)
            style: Speech style (normal, dramatic, whisper, upbeat, calm)
            speed: Speed multiplier (0.5-2.0)
            pitch: Pitch multiplier (0.5-2.0)
        
        Returns:
            Audio object with MP3 data
        
        Raises:
            GhostVoiceError: API error
        """
        response = self._post(
            "/synthesize",
            data={
                "text": text,
                "voice_id": voice_id,
                "language": language,
                "style": style,
                "speed": speed,
                "pitch": pitch,
                "stream": False,
            },
        )
        
        # Get the audio file
        audio_url = response.get("audio_url")
        if not audio_url:
            raise GhostVoiceError("No audio URL returned")
        
        audio_data = self._download_audio(audio_url)
        
        return Audio(
            data=audio_data,
            format="mp3",
            duration_seconds=response.get("audio_duration"),
        )
    
    def synthesize_stream(
        self,
        text: str,
        voice_id: str,
        language: str = "en",
    ) -> Generator[bytes, None, None]:
        """
        Synthesize with audio streaming (real-time playback).
        
        Args:
            text: Text to synthesize
            voice_id: Voice ID
            language: Language code
        
        Yields:
            Audio chunks (PCM bytes)
        """
        url = f"{self.base_url}/ws/synthesize"
        
        # This would typically use websocket-client library
        # For HTTP fallback, you could use Server-Sent Events
        try:
            import websocket
            
            ws = websocket.create_connection(url)
            ws.send(json.dumps({
                "text": text,
                "voice_id": voice_id,
                "language": language,
            }))
            
            while True:
                msg = ws.recv()
                data = json.loads(msg)
                
                if data.get("type") == "chunk":
                    import base64
                    yield base64.b64decode(data.get("data", ""))
                elif data.get("type") == "error":
                    raise GhostVoiceError(data.get("data"))
                elif data.get("type") == "complete":
                    break
            
            ws.close()
        except ImportError:
            raise GhostVoiceError(
                "Streaming requires: pip install websocket-client"
            )
    
    def synthesize_batch(
        self,
        texts: List[str],
        voice_id: str,
        language: str = "en",
    ) -> List[SynthesisJob]:
        """
        Batch synthesize multiple texts.
        
        Args:
            texts: List of texts to synthesize (up to 100 items)
            voice_id: Voice ID
            language: Language code
        
        Returns:
            List of job IDs to poll for results
        """
        response = self._post(
            "/synthesize-batch",
            data={
                "voice_id": voice_id,
                "items": [
                    {"text": text, "language": language, "style": "normal"}
                    for text in texts
                ],
            },
        )
        
        job_ids = response.get("job_ids", [])
        return [self.get_synthesis_status(jid) for jid in job_ids]
    
    def synthesize_ssml(
        self,
        ssml: str,
        voice_id: str,
        language: str = "en",
    ) -> Audio:
        """
        Synthesize SSML with phrase-level control.
        
        Args:
            ssml: SSML markup
            voice_id: Voice ID
            language: Language code
        
        Returns:
            Audio object
        """
        response = self._post(
            "/synthesize-ssml",
            data={
                "ssml": ssml,
                "voice_id": voice_id,
                "language": language,
            },
        )
        
        # Poll for completion
        job_id = response.get("id")
        while True:
            job = self.get_synthesis_status(job_id)
            if job.status == "completed":
                audio_data = self._download_audio(job.audio_url)
                return Audio(
                    data=audio_data,
                    duration_seconds=job.audio_duration,
                )
            elif job.status == "failed":
                raise GhostVoiceError(f"Synthesis failed: {job_id}")
    
    def get_synthesis_status(self, job_id: str) -> SynthesisJob:
        """Get status of a synthesis job."""
        response = self._get(f"/synthesis/{job_id}")
        
        return SynthesisJob(
            id=response["id"],
            status=response["status"],
            progress=response.get("progress", 0),
            audio_url=response.get("audio_url"),
            audio_duration=response.get("audio_duration"),
            created_at=datetime.fromisoformat(response["created_at"]) if response.get("created_at") else None,
            completed_at=datetime.fromisoformat(response["completed_at"]) if response.get("completed_at") else None,
        )
    
    def list_voices(self, public_only: bool = False) -> List[Voice]:
        """List available voices."""
        params = {"public_only": public_only}
        response = self._get("/voices", params=params)
        
        voices = []
        for v in response.get("voices", []):
            voices.append(Voice(
                id=v["id"],
                name=v["name"],
                description=v.get("description"),
                gender=v.get("gender"),
                accent=v.get("accent"),
                language=v.get("language", "en"),
                quality_score=v.get("quality_score", 0),
                is_public=v.get("is_public", False),
                created_at=datetime.fromisoformat(v["created_at"]) if v.get("created_at") else None,
            ))
        
        return voices
    
    def get_voice(self, voice_id: str) -> Voice:
        """Get voice details."""
        response = self._get(f"/voices/{voice_id}")
        
        return Voice(
            id=response["id"],
            name=response["name"],
            description=response.get("description"),
            gender=response.get("gender"),
            accent=response.get("accent"),
            language=response.get("language", "en"),
            quality_score=response.get("quality_score", 0),
            is_public=response.get("is_public", False),
            created_at=datetime.fromisoformat(response["created_at"]) if response.get("created_at") else None,
        )
    
    def create_voice(
        self,
        name: str,
        description: str = "",
        gender: Optional[str] = None,
        accent: Optional[str] = None,
        language: str = "en",
    ) -> Voice:
        """Create a new voice."""
        response = self._post(
            "/voices/create",
            data={
                "name": name,
                "description": description,
                "gender": gender,
                "accent": accent,
                "language": language,
            },
        )
        
        return Voice(
            id=response["id"],
            name=response["name"],
            description=response.get("description"),
            gender=response.get("gender"),
            accent=response.get("accent"),
            language=response.get("language", "en"),
            quality_score=response.get("quality_score", 0),
            is_public=response.get("is_public", False),
            created_at=datetime.fromisoformat(response["created_at"]) if response.get("created_at") else None,
        )
    
    def upload_voice_sample(self, voice_id: str, audio_path: str) -> Dict[str, Any]:
        """Upload audio sample for voice cloning."""
        with open(audio_path, 'rb') as f:
            files = {'file': f}
            response = self.session.post(
                f"{self.base_url}/voices/{voice_id}/upload-sample",
                files=files,
                timeout=self.timeout,
            )
        
        return self._handle_response(response)
    
    def clone_voice(
        self,
        source_voice_id: str,
        name: str,
        description: str = "",
    ) -> Voice:
        """Clone an existing voice."""
        response = self._post(
            f"/voices/{source_voice_id}/clone",
            data={
                "name": name,
                "description": description,
            },
        )
        
        return Voice(
           id=response["id"],
            name=response["name"],
            description=response.get("description"),
            gender=response.get("gender"),
            accent=response.get("accent"),
            language=response.get("language", "en"),
            quality_score=response.get("quality_score", 0),
            is_public=response.get("is_public", False),
            created_at=datetime.fromisoformat(response["created_at"]) if response.get("created_at") else None,
        )
    
    def get_quota(self) -> Dict[str, Any]:
        """Get current quota and usage."""
        return self._get("/me/quota")
    
    def check_quota(self, text_length: int) -> Dict[str, Any]:
        """Pre-check if quota is available."""
        return self._post("/quota/check", data={"text_length": text_length})
    
    def _get(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """Make GET request."""
        url = f"{self.base_url}{endpoint}"
        response = self.session.get(url, params=params, timeout=self.timeout)
        return self._handle_response(response)
    
    def _post(self, endpoint: str, data: Optional[Dict] = None) -> Dict:
        """Make POST request."""
        url = f"{self.base_url}{endpoint}"
        response = self.session.post(
            url,
            json=data,
            timeout=self.timeout,
        )
        return self._handle_response(response)
    
    def _download_audio(self, url: str) -> bytes:
        """Download audio file."""
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.content
    
    def _handle_response(self, response: requests.Response) -> Dict:
        """Handle API response."""
        try:
            data = response.json()
        except:
            data = {"raw": response.text}
        
        if response.status_code >= 400:
            detail = data.get("detail", response.text)
            raise GhostVoiceError(
                f"API Error {response.status_code}: {detail}"
            )
        
        return data


class GhostVoiceError(Exception):
    """Ghost Voice API error."""
    pass


# Example usage
if __name__ == "__main__":
    client = GhostVoiceTTS(api_key="sk_test_example")
    
    # List voices
    voices = client.list_voices()
    print(f"Found {len(voices)} voices")
    
    # Synthesize text
    audio = client.synthesize(
        "Hello, this is a test",
        voice_id=voices[0].id if voices else "default",
    )
    audio.save("output.mp3")
    print("Saved to output.mp3")
    
    # Check quota
    quota = client.get_quota()
    print(f"Quota: {quota}")
