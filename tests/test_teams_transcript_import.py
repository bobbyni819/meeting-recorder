"""Tests for Microsoft Teams transcript import."""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from meeting_recorder.transcription import vtt_import


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _write_docx(path: Path, paragraphs: list[str]) -> Path:
    body = "".join(
        f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"
        for text in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(path, "w") as docx:
        docx.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        docx.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        docx.writestr("word/document.xml", document)
    return path


class _NoopRecordingIndex:
    def index_recording(self, recording_dir: Path) -> None:
        pass

    def close(self) -> None:
        pass


def _disable_search_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "meeting_recorder.search.index.RecordingIndex",
        lambda *a, **k: _NoopRecordingIndex(),
    )


def _recording(tmp_path: Path, name: str | None = None) -> Path:
    rec = tmp_path / (
        name
        or "2026-06-12_08-04-42_Meeting_in__Modeling_of_Early_Stage_Influenza_Infection____Microsoft_Teams"
    )
    rec.mkdir()
    (rec / "metadata.json").write_text(
        json.dumps(
            {
                "app_name": "Teams",
                "status": "error",
                "meeting_subject": "",
                "start_time": "2026-06-12T08:04:42",
            }
        ),
        encoding="utf-8",
    )
    return rec


def test_parse_teams_docx_orders_speakers_text_and_merges(tmp_path):
    docx = _write_docx(
        tmp_path / "teams.docx",
        [
            "Faye Guo",
            "0:03",
            "First sentence.",
            "Second paragraph.",
            "Faye Guo",
            "00:04",
            "Continues the same turn.",
            "Alex Liu",
            "00:00:10",
            "Different speaker.",
            "not a speaker",
            "0:12",
            "Unnamed speaker line.",
        ],
    )

    segments = vtt_import.parse_teams_docx(docx)

    assert [segment.speaker for segment in segments] == ["Faye Guo", "Alex Liu", ""]
    assert segments[0].start == 3.0
    assert segments[0].end == 10.0
    assert segments[0].text == (
        "First sentence. Second paragraph. Continues the same turn."
    )
    assert segments[1].start == 10.0
    assert segments[1].text == "Different speaker."
    assert segments[2].text == "Unnamed speaker line."


def test_extract_docx_paragraphs_corrupt_file_returns_empty(tmp_path):
    corrupt = _write(tmp_path, "bad.docx", "not a zip")

    assert vtt_import._extract_docx_paragraphs(corrupt) == []


def test_parse_transcript_file_dispatches_docx_and_vtt(tmp_path):
    docx = _write_docx(tmp_path / "teams.docx", ["Faye Guo", "0:03", "Hello docx"])
    vtt = _write(
        tmp_path,
        "teams.vtt",
        "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Alex Liu>Hello vtt</v>\n",
    )

    assert vtt_import.parse_transcript_file(docx)[0].text == "Hello docx"
    assert vtt_import.parse_transcript_file(vtt)[0].text == "Hello vtt"


def test_find_teams_transcript_files_newest_first_and_missing_dir(tmp_path):
    old = _write(tmp_path, "old.docx", "")
    ignored = _write(tmp_path, "notes.txt", "")
    newest = _write(tmp_path, "newest.vtt", "")
    os.utime(old, (100, 100))
    os.utime(ignored, (200, 200))
    os.utime(newest, (300, 300))

    found = vtt_import.find_teams_transcript_files(tmp_path)

    assert found == [newest, old]
    assert ignored not in found
    assert vtt_import.find_teams_transcript_files(tmp_path / "missing") == []


def test_find_teams_transcript_for_recording_uses_derived_subject_tokens(tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    target = _write(downloads, "Modeling Early Stage Influenza.docx", "")
    decoy = _write(downloads, "Project Budget Review.docx", "")
    os.utime(target, (200, 200))
    os.utime(decoy, (300, 300))
    rec = _recording(tmp_path)

    found = vtt_import.find_teams_transcript_for_recording(
        rec, downloads, max_age_hours=1
    )

    assert found == target


def test_find_teams_transcript_for_recording_returns_none_without_match(tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    old = _write(downloads, "Completely Different.docx", "")
    os.utime(old, (100, 100))
    rec = _recording(tmp_path)

    assert (
        vtt_import.find_teams_transcript_for_recording(
            rec, downloads, max_age_hours=1
        )
        is None
    )


def test_import_docx_writes_canonical_outputs_and_docx_provenance(
    tmp_path, monkeypatch
):
    rec = _recording(tmp_path, "2026-06-12_08-00-00_Teams")
    docx = _write_docx(
        tmp_path / "teams.docx",
        [
            "Faye Guo",
            "0:03",
            "Hello from Teams.",
            "Alex Liu",
            "00:00:10",
            "Reply from Alex.",
        ],
    )
    _disable_search_index(monkeypatch)

    result = vtt_import.import_vtt_to_recording(rec, docx)

    assert result["segments"] == 2
    assert result["speakers"] == ["Alex Liu", "Faye Guo"]
    data = json.loads((rec / "transcript.json").read_text(encoding="utf-8"))
    assert data["segments"][0]["text"] == "Hello from Teams."
    assert (rec / "transcript.txt").exists()
    assert (rec / "transcript.srt").exists()
    assert (rec / "teams_transcript.docx").read_bytes() == docx.read_bytes()
    meta = json.loads((rec / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "completed"
    assert meta["transcription_source"] == "teams_docx"
    assert meta["speaker_count"] == 2
