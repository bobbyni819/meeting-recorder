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

    # Tried in order; the first that loads wins. A newer model that the user
    # hasn't accepted on HF (or that needs a newer pyannote) falls through to
    # the pinned 3.1 baseline.
    _FALLBACK_MODEL = "pyannote/speaker-diarization-3.1"

    def __init__(
        self,
        huggingface_token: str,
        min_speakers: int = 2,
        max_speakers: int = 6,
        model: str = "pyannote/speaker-diarization-3.1",
    ):
        self.huggingface_token = huggingface_token
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        self.model = model or self._FALLBACK_MODEL
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

        # Try the configured model first; on any load failure (not accepted
        # on HF, needs a newer pyannote, network), fall back to the pinned
        # 3.1 baseline so diarization still works.
        candidates = [self.model]
        if self._FALLBACK_MODEL not in candidates:
            candidates.append(self._FALLBACK_MODEL)
        last_error: Exception | None = None
        for model_name in candidates:
            try:
                logger.info("Loading pyannote diarization pipeline: %s", model_name)
                self._pipeline = Pipeline.from_pretrained(model_name)
                self.model = model_name
                break
            except Exception as e:
                last_error = e
                logger.warning(
                    "Could not load diarization model %s (%s)%s",
                    model_name, e,
                    "; trying fallback" if model_name != candidates[-1] else "",
                )
        if self._pipeline is None:
            raise RuntimeError(
                f"No diarization model could be loaded: {last_error}"
            )

        # Move to GPU if available and cuDNN works
        try:
            import torch
            if torch.cuda.is_available():
                # Test cuDNN before moving pipeline — some installs have
                # mismatched cuDNN that causes a hard C-level abort.
                try:
                    _test = torch.randn(2, 2, device="cuda")
                    torch.nn.functional.conv1d(
                        _test.unsqueeze(0), torch.randn(1, 2, 1, device="cuda")
                    )
                    del _test
                    self._pipeline.to(torch.device("cuda"))
                    logger.info("Diarization pipeline moved to CUDA.")
                except Exception:
                    logger.info("cuDNN test failed, diarization running on CPU.")
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
