"""
SSML (Speech Synthesis Markup Language) parser and processor.

Allows fine-grained control over synthesis parameters at the phrase level.

Supported tags:
- <speak> - Root element
- <s> - Sentence
- <p> - Paragraph
- <break> - Silence/pause
- <emphasis> - Emphasize word/phrase
- <prosody> - Modify pitch/rate/volume
- <phoneme> - Explicit pronunciation
- <voice> - Switch voice within document
- <amazon:effect> - Emotional effects
"""

import re
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)


@dataclass
class SSMLSegment:
    """Represents a segment of SSML parsed content."""
    text: str
    break_before: float = 0.0  # Milliseconds
    break_after: float = 0.0  # Milliseconds
    emphasis: str = "none"  # none, mild, moderate, strong
    prosody_pitch: Optional[str] = None  # +20%, -20%, 80Hz, etc
    prosody_rate: Optional[str] = None  # 0.8, 1.2, slow, fast, etc
    prosody_volume: Optional[str] = None  # +5dB, -5dB, silent, soft, normal, loud, x-loud
    voice: Optional[str] = None
    phoneme: Optional[str] = None


class SSMLParser:
    """
    Parse SSML documents into synthesis parameters.
    
    Example:
        ssml = '''
        <speak>
            Hello <emphasis>world</emphasis>.
            <break time="500ms"/>
            <prosody pitch="+20%" rate="slow">What's up?</prosody>
        </speak>
        '''
        parser = SSMLParser()
        segments = parser.parse(ssml)
    """
    
    SUPPORTED_TAGS = {
        "speak", "s", "p", "break", "emphasis", "prosody",
        "phoneme", "voice", "amazon:effect", "sub"
    }
    
    def __init__(self):
        self.segments: List[SSMLSegment] = []
    
    def parse(self, ssml_text: str) -> List[SSMLSegment]:
        """
        Parse SSML document into segments.
        
        Args:
            ssml_text: SSML markup as string
        
        Returns:
            List of SSMLSegment objects
        
        Raises:
            SSMLParseError: Invalid SSML syntax
        """
        self.segments = []
        
        # Validate and normalize SSML
        ssml_text = self._prepare_ssml(ssml_text)
        
        try:
            root = ET.fromstring(ssml_text)
        except ET.ParseError as e:
            raise SSMLParseError(f"Invalid SSML syntax: {e}")
        
        if root.tag != "speak":
            raise SSMLParseError("Root element must be <speak>")
        
        # Process elements
        self._process_element(root)
        
        return self.segments
    
    def _prepare_ssml(self, text: str) -> str:
        """Prepare and validate SSML text."""
        # Add speak tags if missing
        if not text.strip().startswith("<speak"):
            text = f"<speak>{text}</speak>"
        
        # Decode common entities
        text = text.replace("&apos;", "'")
        text = text.replace("&quot;", '"')
        
        return text.strip()
    
    def _process_element(
        self,
        element: ET.Element,
        parent_prosody: Optional[Dict[str, str]] = None,
        parent_emphasis: str = "none",
    ):
        """Recursively process SSML elements."""
        
        if element.tag == "speak":
            # Process children of speak element
            self._process_children(
                element,
                parent_prosody=parent_prosody,
                parent_emphasis=parent_emphasis,
            )
        
        elif element.tag in ("s", "p"):
            # Sentence or paragraph - add break after
            self._process_children(
                element,
                parent_prosody=parent_prosody,
                parent_emphasis=parent_emphasis,
            )
            # Add break after paragraph/sentence
            if element.tail and element.tail.strip():
                break_ms = 500 if element.tag == "p" else 250
                last_segment = self._get_last_segment()
                if last_segment:
                    last_segment.break_after = break_ms
        
        elif element.tag == "break":
            # Silence/pause
            time_ms = self._parse_time(element.get("time", "500ms"))
            last_segment = self._get_last_segment()
            if last_segment and not last_segment.text.strip().endswith("."):
                last_segment.break_after = time_ms
            else:
                # Create empty segment for standalone break
                self.segments.append(SSMLSegment(text="", break_before=time_ms))
        
        elif element.tag == "emphasis":
            # Emphasis level
            level = element.get("level", "moderate")  # mild, moderate, strong
            self._process_children(
                element,
                parent_prosody=parent_prosody,
                parent_emphasis=level,
            )
        
        elif element.tag == "prosody":
            # Pitch, rate, volume
            prosody = parent_prosody.copy() if parent_prosody else {}
            
            if "pitch" in element.attrib:
                prosody["pitch"] = element.get("pitch")
            if "rate" in element.attrib:
                prosody["rate"] = element.get("rate")
            if "volume" in element.attrib:
                prosody["volume"] = element.get("volume")
            
            self._process_children(
                element,
                parent_prosody=prosody,
                parent_emphasis=parent_emphasis,
            )
        
        elif element.tag == "voice":
            # Voice switch
            voice = element.get("name")
            self._process_children(
                element,
                parent_prosody=parent_prosody,
                parent_emphasis=parent_emphasis,
                voice=voice,
            )
        
        elif element.tag == "phoneme":
            # Explicit phoneme (IPA)
            phoneme = element.get("ph")  # IPA characters
            alphabet = element.get("alphabet", "ipa")  # ipa or x-sampa
            
            if element.text:
                segment = SSMLSegment(
                    text=element.text.strip(),
                    emphasis=parent_emphasis,
                    phoneme=phoneme,
                )
                self._apply_prosody(segment, parent_prosody)
                self.segments.append(segment)
        
        elif element.tag == "sub":
            # Substitution - use "alias" attribute
            text = element.get("alias", element.text or "")
            segment = SSMLSegment(
                text=text,
                emphasis=parent_emphasis,
            )
            self._apply_prosody(segment, parent_prosody)
            self.segments.append(segment)
        
        elif element.tag == "amazon:effect":
            # Amazon-specific emotional effects
            effect = element.get("name")  # whispered, excited, etc
            logger.debug(f"Amazon effect not yet supported: {effect}")
            self._process_children(
                element,
                parent_prosody=parent_prosody,
                parent_emphasis=parent_emphasis,
            )
        
        # Handle text nodes
        if element.text and element.text.strip():
            segment = SSMLSegment(
                text=element.text.strip(),
                emphasis=parent_emphasis,
            )
            self._apply_prosody(segment, parent_prosody)
            self.segments.append(segment)
        
        # Handle tail text (text after closing tag)
        if element.tail and element.tail.strip():
            segment = SSMLSegment(
                text=element.tail.strip(),
                emphasis=parent_emphasis,
            )
            self._apply_prosody(segment, parent_prosody)
            self.segments.append(segment)
    
    def _process_children(
        self,
        element: ET.Element,
        parent_prosody: Optional[Dict[str, str]] = None,
        parent_emphasis: str = "none",
        voice: Optional[str] = None,
    ):
        """Process child elements."""
        for child in element:
            self._process_element(
                child,
                parent_prosody=parent_prosody,
                parent_emphasis=parent_emphasis,
            )
    
    def _apply_prosody(
        self,
        segment: SSMLSegment,
        prosody: Optional[Dict[str, str]],
    ):
        """Apply prosody settings to segment."""
        if prosody:
            segment.prosody_pitch = prosody.get("pitch")
            segment.prosody_rate = prosody.get("rate")
            segment.prosody_volume = prosody.get("volume")
    
    def _get_last_segment(self) -> Optional[SSMLSegment]:
        """Get last segment, or None."""
        return self.segments[-1] if self.segments else None
    
    def _parse_time(self, time_str: str) -> float:
        """Parse time string to milliseconds."""
        # Supports: "500ms", "1s", "1.5s"
        match = re.match(r"(\d+(?:\.\d+)?)(ms|s)", time_str)
        if not match:
            return 500.0  # Default
        
        value, unit = match.groups()
        value = float(value)
        
        if unit == "s":
            return value * 1000
        else:  # ms
            return value
    
    def to_plain_text(self) -> str:
        """Extract plain text from segments."""
        return " ".join(s.text for s in self.segments if s.text)
    
    def to_synthesis_config(self) -> Dict[str, Any]:
        """Convert to synthesis engine configuration."""
        config = {
            "segments": []
        }
        
        for segment in self.segments:
            seg_config = {
                "text": segment.text,
                "break_before_ms": segment.break_before,
                "break_after_ms": segment.break_after,
            }
            
            if segment.emphasis != "none":
                seg_config["emphasis"] = segment.emphasis
            
            if segment.prosody_pitch:
                seg_config["pitch"] = segment.prosody_pitch
            if segment.prosody_rate:
                seg_config["rate"] = segment.prosody_rate
            if segment.prosody_volume:
                seg_config["volume"] = segment.prosody_volume
            
            if segment.phoneme:
                seg_config["phoneme"] = segment.phoneme
            
            config["segments"].append(seg_config)
        
        return config


class SSMLParseError(Exception):
    """Raised when SSML parsing fails."""
    pass


# Utility functions
def is_ssml(text: str) -> bool:
    """Check if text contains SSML."""
    return "<speak>" in text or "<s>" in text or "<p>" in text or "<break" in text


def ssml_to_plain_text(ssml_text: str) -> str:
    """Extract plain text from SSML."""
    parser = SSMLParser()
    segments = parser.parse(ssml_text)
    return parser.to_plain_text()


def validate_ssml(ssml_text: str) -> tuple[bool, Optional[str]]:
    """
    Validate SSML syntax.
    
    Returns:
        (is_valid, error_message)
    """
    try:
        SSMLParser().parse(ssml_text)
        return True, None
    except Exception as e:
        return False, str(e)


# Example usage
if __name__ == "__main__":
    ssml_example = """
    <speak>
        Hello <emphasis level="strong">world</emphasis>!
        <break time="500ms"/>
        <prosody pitch="+20%" rate="slow">
            This is slow and high-pitched.
        </prosody>
    </speak>
    """
    
    parser = SSMLParser()
    segments = parser.parse(ssml_example)
    
    print("Parsed segments:")
    for segment in segments:
        print(f"  Text: '{segment.text}'")
        print(f"    Emphasis: {segment.emphasis}")
        print(f"    Pitch: {segment.prosody_pitch}")
        print(f"    Rate: {segment.prosody_rate}")
        print(f"    Breaks: {segment.break_before}ms before, {segment.break_after}ms after")
