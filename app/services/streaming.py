import asyncio
import logging
from typing import AsyncGenerator
import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
import json

from app.services.tts_engine import get_tts_engine
from app.services.cache import get_redis_cache
from app.core.metrics import MetricsCollector, SynthesisTimer
from app.models.db import Voice, SynthesisJob
from sqlmodel import Session

logger = logging.getLogger(__name__)


class AudioStreamBuffer:
    """Buffer audio chunks for streaming with configurable chunk size."""
    
    CHUNK_SIZE = 4096  # Samples per chunk (~93ms at 22050 Hz)
    
    def __init__(self, audio_array: np.ndarray, sample_rate: int):
        self.audio = audio_array.astype(np.float32)
        self.sample_rate = sample_rate
        self.position = 0
        self.total_samples = len(audio_array)
    
    async def stream_chunks(self) -> AsyncGenerator[bytes, None]:
        """Stream audio as binary chunks."""
        while self.position < self.total_samples:
            chunk = self.audio[self.position : self.position + self.CHUNK_SIZE]
            self.position += self.CHUNK_SIZE
            
            # Convert to bytes
            chunk_bytes = chunk.tobytes()
            
            yield chunk_bytes
            
            # Small delay to simulate natural streaming
            await asyncio.sleep(0.01)


class StreamingTTSManager:
    """Manages streaming TTS requests with real-time audio delivery."""
    
    def __init__(self):
        self.engine = get_tts_engine()
        self.cache = get_redis_cache()
    
    async def synthesize_and_stream(
        self,
        text: str,
        voice_id: str,
        voice_embedding: np.ndarray = None,
        language: str = "en",
        style: str = "normal",
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> AsyncGenerator[dict, None]:
        """
        Synthesize text and stream audio as JSON chunks with metadata.
        
        Yields JSON objects with structure:
        {
            "type": "start|chunk|complete|error",
            "data": "base64-encoded audio or metadata",
            "progress": 0.0-1.0
        }
        """
        
        try:
            # Send start event
            yield {
                "type": "start",
                "message": "Synthesis started",
                "text_length": len(text),
                "language": language,
                "timestamp": asyncio.get_event_loop().time(),
            }
            
            # Generate audio
            with SynthesisTimer(model="tortoise"):
                audio_array, sample_rate = self.engine.synthesize(
                    text=text,
                    voice_embedding=voice_embedding,
                    speed=speed,
                    pitch=pitch,
                    style=style,
                )
            
            # Create streaming buffer
            buffer = AudioStreamBuffer(audio_array, sample_rate)
            
            # Stream audio chunks
            chunk_count = 0
            async for chunk in buffer.stream_chunks():
                import base64
                chunk_b64 = base64.b64encode(chunk).decode("utf-8")
                progress = (chunk_count * buffer.CHUNK_SIZE) / buffer.total_samples
                
                yield {
                    "type": "chunk",
                    "data": chunk_b64,
                    "chunk_index": chunk_count,
                    "progress": min(progress, 0.99),
                    "sample_rate": sample_rate,
                }
                chunk_count += 1
            
            # Send completion event
            audio_duration = len(audio_array) / sample_rate
            yield {
                "type": "complete",
                "message": "Synthesis complete",
                "audio_duration": audio_duration,
                "total_chunks": chunk_count,
                "progress": 1.0,
            }
            
            MetricsCollector.record_synthesis_complete(
                duration=len(audio_array) / sample_rate,
                success=True,
                num_characters=len(text),
            )
        
        except Exception as e:
            logger.error(f"Stream synthesis failed: {e}", exc_info=True)
            yield {
                "type": "error",
                "error": str(e),
                "error_type": type(e).__name__,
            }
            MetricsCollector.record_synthesis_failure(error_type=type(e).__name__)


class WebSocketConnectionManager:
    """Manages WebSocket connections for streaming synthesis."""
    
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        self.streaming_manager = StreamingTTSManager()
    
    async def connect(self, websocket: WebSocket, client_id: str):
        """Register a new WebSocket connection."""
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info(f"WebSocket connected: {client_id}")
    
    async def disconnect(self, client_id: str):
        """Close and remove a WebSocket connection."""
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger.info(f"WebSocket disconnected: {client_id}")
    
    async def handle_synthesis_stream(
        self,
        websocket: WebSocket,
        client_id: str,
        text: str,
        voice_id: str,
        voice_embedding: np.ndarray = None,
        **kwargs,
    ):
        """
        Handle streaming synthesis request over WebSocket.
        
        Client sends:
        {
            "action": "synthesize",
            "text": "...",
            "voice_id": "...",
            "language": "en",
            "style": "normal",
            "speed": 1.0,
            "pitch": 1.0
        }
        
        Server streams back events with audio chunks.
        """
        
        try:
            async for event in self.streaming_manager.synthesize_and_stream(
                text=text,
                voice_id=voice_id,
                voice_embedding=voice_embedding,
                **kwargs,
            ):
                await websocket.send_json(event)
        
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected during streaming: {client_id}")
            await self.disconnect(client_id)
        
        except Exception as e:
            logger.error(f"WebSocket error: {e}", exc_info=True)
            try:
                await websocket.send_json({
                    "type": "error",
                    "error": str(e),
                })
            except:
                pass
            await self.disconnect(client_id)


# Singleton manager
_ws_manager: StreamingTTSManager = None


def get_websocket_manager() -> StreamingTTSManager:
    """Get or create WebSocket manager."""
    global _ws_manager
    if _ws_manager is None:
        _ws_manager = StreamingTTSManager()
    return _ws_manager
