"""Speaker diarization using pyannote.audio."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SpeakerSegment:
    """A speaker diarization segment."""
    start: float  # seconds
    end: float    # seconds
    speaker: str  # e.g. "SPEAKER_00"


class SpeakerDiarizer:
    """Speaker diarization using pyannote.audio 3.1.

    Identifies different speakers in an audio file and returns
    time-stamped speaker segments.

    Requires a HuggingFace token with access to pyannote models.
    """

    def __init__(
        self,
        huggingface_token: str,
        min_speakers: int = 2,
        max_speakers: int = 6,
    ):
        self.huggingface_token = huggingface_token
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        self._pipeline = None

    def load(self) -> None:
        """Load the diarization pipeline."""
        import os
        from pyannote.audio import Pipeline

        # Set HF_TOKEN env var so huggingface_hub picks it up automatically.
        if self.huggingface_token:
            os.environ["HF_TOKEN"] = self.huggingface_token
            os.environ["HUGGING_FACE_HUB_TOKEN"] = self.huggingface_token

        # pyannote.audio 3.4.0 passes use_auth_token= throughout its codebase,
        # but huggingface_hub >= 1.0 removed that kwarg from hf_hub_download.
        # Patch the raw underlying function so ALL call sites are covered,
        # regardless of which pyannote module imports it.
        try:
            import functools
            import huggingface_hub.file_download as _fd

            if not getattr(_fd.hf_hub_download, "_patched_use_auth_token", False):
                _orig = _fd.hf_hub_download

                @functools.wraps(_orig)
                def _patched(*args, **kwargs):
                    kwargs.pop("use_auth_token", None)
                    return _orig(*args, **kwargs)

                _patched._patched_use_auth_token = True
                _fd.hf_hub_download = _patched

                # Propagate to all modules that imported hf_hub_download
                import sys
                for mod in list(sys.modules.values()):
                    try:
                        if getattr(mod, "hf_hub_download", None) is _orig:
                            mod.hf_hub_download = _patched
                    except Exception:
                        pass
        except Exception:
            logger.debug("Could not patch hf_hub_download", exc_info=True)

        logger.info("Loading pyannote diarization pipeline...")
        self._pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
        )

        # Move to GPU if available
        try:
            import torch
            if torch.cuda.is_available():
                self._pipeline.to(torch.device("cuda"))
                logger.info("Diarization pipeline moved to CUDA.")
        except Exception:
            logger.info("Diarization running on CPU.")

        logger.info("Diarization pipeline loaded.")

    def diarize(self, audio_path: Path) -> list[SpeakerSegment]:
        """Run speaker diarization on an audio file.

        Args:
            audio_path: Path to a WAV audio file.

        Returns:
            List of SpeakerSegment with speaker labels and timestamps.
        """
        if self._pipeline is None:
            self.load()

        logger.info("Running diarization on: %s", audio_path.name)

        diarization = self._pipeline(
            str(audio_path),
            min_speakers=self.min_speakers,
            max_speakers=self.max_speakers,
        )

        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append(SpeakerSegment(
                start=turn.start,
                end=turn.end,
                speaker=speaker,
            ))

        logger.info("Diarization complete: %d segments, speakers found: %s",
                     len(segments), sorted(set(s.speaker for s in segments)))
        return segments
