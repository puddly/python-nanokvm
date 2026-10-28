"""Tests for H.264 WebRTC signaling and the Pro offer splitting."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from aiohttp import WSMsgType
import pytest

from nanokvm.client import NanoKVMClient, NanoKVMVideoModeError
from nanokvm.models import HWVersion
from nanokvm.webrtc import (
    _TRANSPORT_SESSION_PREFIXES,
    IceCandidate,
    ProMediaSection,
    ProWebRTCOffer,
    WebRTCAnswer,
    WebRTCCandidate,
    WebRTCSignaling,
    _media_kind,
    _split_sdp_sections,
    _status_code,
    merge_pro_webrtc_answers,
    split_pro_webrtc_offer,
)

from .common import BASE_URL, mark_detected

COMBINED_OFFER = "\r\n".join(
    (
        "v=0",
        "o=- 1 1 IN IP4 127.0.0.1",
        "s=-",
        "t=0 0",
        "a=group:BUNDLE video-main audio-main data-main duplicate-video",
        "a=group:BUNDLE ignored-duplicate",
        "m=video 9 UDP/TLS/RTP/SAVPF 96",
        "c=IN IP4 0.0.0.0",
        "a=mid:video-main",
        "a=sendrecv",
        "m=audio 9 UDP/TLS/RTP/SAVPF 111",
        "c=IN IP4 0.0.0.0",
        "a=mid:audio-main",
        "a=sendrecv",
        "m=application 9 UDP/DTLS/SCTP webrtc-datachannel",
        "a=mid:data-main",
        "m=video 9 UDP/TLS/RTP/SAVPF 97",
        "a=mid:duplicate-video",
        "",
    )
)
VIDEO_ONLY_OFFER = "\r\n".join(
    ("v=0", "a=group:BUNDLE camera", "m=video 9 RTP 96", "a=mid:camera", "")
)
VIDEO_ANSWER = "\r\n".join(
    (
        "v=0",
        "o=- 2 2 IN IP4 127.0.0.1",
        "s=-",
        "t=0 0",
        "a=group:BUNDLE 0",
        "a=ice-ufrag:video-session-ufrag",
        "a=ice-pwd:video-session-pwd",
        "a=ice-options:trickle",
        "a=fingerprint:sha-256 VIDEO",
        "a=setup:active",
        "m=video 9 UDP/TLS/RTP/SAVPF 96",
        "c=IN IP4 192.0.2.10",
        "a=mid:0",
        "a=ice-ufrag:video-media-ufrag",
        "a=recvonly",
        "",
    )
)
AUDIO_ANSWER = "\n".join(
    (
        "v=0",
        "o=- 3 3 IN IP4 127.0.0.1",
        "s=-",
        "t=0 0",
        "a=ice-ufrag:audio-ufrag",
        "a=ice-pwd:audio-pwd",
        "a=fingerprint:sha-256 AUDIO",
        "a=setup:active",
        "m=audio 9 UDP/TLS/RTP/SAVPF 111",
        "c=IN IP4 192.0.2.11",
        "a=mid:0",
        "a=recvonly",
        "",
    )
)


class FakeWebSocket:
    """A WebSocket that records sends and replays queued messages."""

    def __init__(self, *messages: object) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.close_code: int | None = None
        self.messages = list(messages)
        self.send_error: Exception | None = None

    def __aiter__(self) -> FakeWebSocket:
        return self

    async def __anext__(self) -> object:
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True

    def exception(self) -> None:
        return None

    def sent_events(self) -> list[tuple[str, Any]]:
        """Return each sent event with its JSON-decoded data."""
        return [
            (p["event"], json.loads(p["data"]) if p["data"] else p["data"])
            for p in self.sent
        ]


def signal(event: str, data: object = "") -> SimpleNamespace:
    """Build a device message, with the data JSON-encoded as the device does."""
    if not isinstance(data, str):
        data = json.dumps(data)
    return SimpleNamespace(
        type=WSMsgType.TEXT, data=json.dumps({"event": event, "data": data})
    )


async def events(signaling: WebRTCSignaling) -> list[object]:
    return [event async for event in signaling]


def test_split_offer_keeps_first_supported_section_per_kind() -> None:
    """Only the first video and audio sections become Pro offers."""
    offer = split_pro_webrtc_offer(COMBINED_OFFER)

    assert offer.expected_kinds == ("video", "audio")
    assert [(s.mid, s.index) for s in offer.media_sections] == [
        ("video-main", 0),
        ("audio-main", 1),
    ]
    for split_sdp in offer.offers.values():
        assert split_sdp.count("a=group:BUNDLE 0") == 1
        assert "ignored-duplicate" not in split_sdp
        assert split_sdp.count("m=") == 1
        assert "a=mid:0\r\n" in split_sdp
    assert "m=application" not in offer.offers["video"]


def test_split_offer_uses_media_index_when_mid_is_missing() -> None:
    offer = split_pro_webrtc_offer("v=0\rm=audio 9 RTP/AVP 0\ra=sendrecv\r")

    assert offer.media_sections[0].mid == "0"
    assert "a=mid:0\r\n" in offer.offers["audio"]


def test_candidate_routing_prefers_mid_then_index_then_all() -> None:
    offer = split_pro_webrtc_offer(COMBINED_OFFER)

    def kinds(**kwargs: Any) -> tuple[str, ...]:
        return offer.kinds_for_candidate(IceCandidate(candidate="c", **kwargs))

    assert kinds(sdp_mid="audio-main", sdp_m_line_index=0) == ("audio",)
    assert kinds(sdp_mid="unknown", sdp_m_line_index=0) == ("video",)
    assert kinds(sdp_mid="unknown", sdp_m_line_index=99) == ("video", "audio")


def test_candidate_mapping_between_offer_and_pro_peers() -> None:
    offer = split_pro_webrtc_offer(COMBINED_OFFER)
    candidate = IceCandidate(
        candidate="c", sdp_mid="x", sdp_m_line_index=5, username_fragment="u"
    )

    assert offer.upstream_candidate_for_kind("video", candidate) == IceCandidate(
        candidate="c", sdp_mid="0", sdp_m_line_index=0, username_fragment="u"
    )
    assert offer.client_candidate_for_kind("audio", candidate) == (
        IceCandidate(
            candidate="c",
            sdp_mid="audio-main",
            sdp_m_line_index=1,
            username_fragment="u",
        )
    )
    empty = ProWebRTCOffer(offers={}, media_sections=())
    assert empty.upstream_candidate_for_kind("audio", candidate) is None
    assert empty.client_candidate_for_kind("video", candidate) is None


def test_merge_answers_restores_mids_and_per_media_transport() -> None:
    merged = merge_pro_webrtc_answers(
        split_pro_webrtc_offer(COMBINED_OFFER),
        {"audio": AUDIO_ANSWER, "video": VIDEO_ANSWER},
    )
    session_lines, media = _split_sdp_sections(merged)

    assert not any(line.startswith("a=group:BUNDLE") for line in session_lines)
    assert not any(
        line.startswith(_TRANSPORT_SESSION_PREFIXES) for line in session_lines
    )
    assert [section[0].split()[0] for section in media] == ["m=video", "m=audio"]
    assert "a=mid:video-main" in media[0]
    assert "a=mid:audio-main" in media[1]
    assert "a=ice-ufrag:video-media-ufrag" in media[0]
    assert "a=ice-ufrag:video-session-ufrag" not in media[0]
    assert "a=ice-pwd:video-session-pwd" in media[0]
    assert "a=fingerprint:sha-256 VIDEO" in media[0]
    assert "a=ice-ufrag:audio-ufrag" in media[1]
    assert merged.endswith("\r\n")


def test_merge_rejects_missing_or_mislabeled_answers() -> None:
    offer = split_pro_webrtc_offer(COMBINED_OFFER)
    with pytest.raises(ValueError, match="No NanoKVM Pro WebRTC answers"):
        merge_pro_webrtc_answers(offer, {})
    with pytest.raises(ValueError, match="Missing audio WebRTC answer"):
        merge_pro_webrtc_answers(offer, {"video": VIDEO_ANSWER})
    with pytest.raises(ValueError, match="video answer has no media section"):
        merge_pro_webrtc_answers(
            ProWebRTCOffer(
                offers={"video": "offer"},
                media_sections=(ProMediaSection("video", "v", 0, ("m=video 9",)),),
            ),
            {"video": AUDIO_ANSWER},
        )


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        ((), None),
        (("a=mid:0",), None),
        (("m=application 9 UDP/DTLS/SCTP 5000",), None),
        (("m=video 9 UDP/TLS/RTP/SAVPF 96",), "video"),
    ],
)
def test_media_kind(lines: tuple[str, ...], expected: str | None) -> None:
    assert _media_kind(lines) == expected


@pytest.mark.parametrize(
    ("data", "raw", "expected"),
    [
        ({"status": "-4"}, "", -4),
        ({"code": 1}, "", 1),
        (None, -1, -1),
        (None, "-4", -4),
        (None, '"1"', 1),
        (None, "bad", None),
        (None, True, None),
    ],
)
def test_status_code_accepts_device_payload_variants(
    data: dict[str, object] | None, raw: object, expected: int | None
) -> None:
    assert _status_code(data, raw) == expected


async def test_non_pro_passes_offer_answer_and_candidates_through() -> None:
    ws = FakeWebSocket(
        SimpleNamespace(type=WSMsgType.BINARY, data=b""),
        SimpleNamespace(type=WSMsgType.TEXT, data="not json"),
        signal("heartbeat"),
        signal("video-answer", {"type": "answer", "sdp": "answer-sdp"}),
        signal("video-candidate", {"candidate": "c", "sdpMid": "0"}),
        signal("video-candidate", {"sdpMid": "0"}),
        signal("unknown", {}),
    )
    signaling = WebRTCSignaling(ws, pro=False)  # type: ignore[arg-type]

    await signaling.send_offer("offer-sdp")
    await signaling.send_candidate(
        IceCandidate(candidate="b", sdp_mid="0", username_fragment="u")
    )

    assert ws.sent_events() == [
        ("video-offer", {"type": "offer", "sdp": "offer-sdp"}),
        ("video-candidate", {"candidate": "b", "sdpMid": "0", "usernameFragment": "u"}),
    ]
    assert await events(signaling) == [
        WebRTCAnswer("answer-sdp"),
        WebRTCCandidate(IceCandidate(candidate="c", sdp_mid="0")),
    ]


async def test_pro_splits_offer_and_merges_answers_and_candidates() -> None:
    ws = FakeWebSocket(
        signal("video-status", "1"),
        signal("video-candidate", {"candidate": "early", "sdpMid": "0"}),
        signal("video-answer", {"sdp": VIDEO_ANSWER}),
        signal("audio-answer", {"sdp": AUDIO_ANSWER}),
        signal("audio-answer", {"sdp": AUDIO_ANSWER}),
        signal("audio-candidate", {"candidate": "late", "sdpMid": "0"}),
    )
    signaling = WebRTCSignaling(ws, pro=True)  # type: ignore[arg-type]
    try:
        with pytest.raises(RuntimeError, match="offer before candidates"):
            await signaling.send_candidate(IceCandidate(candidate="c"))
        await signaling.send_offer(COMBINED_OFFER)
        await signaling.send_candidate(
            IceCandidate(candidate="c", sdp_mid="audio-main")
        )

        sent = ws.sent_events()
        assert [event for event, _ in sent] == [
            "video-offer",
            "audio-offer",
            "audio-candidate",
        ]
        assert sent[2][1] == {"candidate": "c", "sdpMid": "0", "sdpMLineIndex": 0}

        received = await events(signaling)
    finally:
        await signaling.close()

    assert len(received) == 3
    assert isinstance(received[0], WebRTCAnswer)
    assert "a=mid:audio-main" in received[0].sdp
    assert received[1:] == [
        WebRTCCandidate(
            IceCandidate(candidate="early", sdp_mid="video-main", sdp_m_line_index=0)
        ),
        WebRTCCandidate(
            IceCandidate(candidate="late", sdp_mid="audio-main", sdp_m_line_index=1)
        ),
    ]


async def test_pro_video_only_offer_answers_after_one_answer() -> None:
    ws = FakeWebSocket(signal("video-answer", {"sdp": VIDEO_ANSWER}))
    async with WebRTCSignaling(ws, pro=True) as signaling:  # type: ignore[arg-type]
        await signaling.send_offer(VIDEO_ONLY_OFFER)
        (answer,) = await events(signaling)

    assert [event for event, _ in ws.sent_events()] == ["video-offer"]
    assert isinstance(answer, WebRTCAnswer)
    assert "a=mid:camera" in answer.sdp
    assert ws.closed


async def test_pro_rejects_offer_without_media() -> None:
    async with WebRTCSignaling(FakeWebSocket(), pro=True) as signaling:  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="does not contain"):
            await signaling.send_offer("v=0\r\nm=application 9 SCTP 5000\r\n")


async def test_pro_inconsistent_video_mode_raises() -> None:
    ws = FakeWebSocket(signal("video-status", "-4"))
    async with WebRTCSignaling(ws, pro=True) as signaling:  # type: ignore[arg-type]
        with pytest.raises(NanoKVMVideoModeError):
            await events(signaling)


async def test_pro_heartbeat_failure_closes_the_websocket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nanokvm.webrtc._PRO_HEARTBEAT_SECONDS", 0)
    ws = FakeWebSocket()
    signaling = WebRTCSignaling(ws, pro=True)  # type: ignore[arg-type]
    await asyncio.sleep(0.01)
    assert ws.sent[0] == {"event": "heartbeat", "data": ""}

    ws.send_error = ConnectionError("gone")
    async with asyncio.timeout(1):
        while not ws.closed:
            await asyncio.sleep(0)
    await signaling.close()


@pytest.mark.parametrize(
    ("hw_version", "path", "heartbeat"),
    [
        (HWVersion.PCIE, "stream/h264", 30.0),
        (HWVersion.PRO, "stream/h264/webrtc", None),
    ],
)
async def test_open_webrtc_signaling_picks_the_endpoint_by_hardware(
    client: NanoKVMClient, hw_version: HWVersion, path: str, heartbeat: float | None
) -> None:
    mark_detected(client, hw_version)
    ws = FakeWebSocket()
    client._session.open_ws = AsyncMock(return_value=ws)  # type: ignore[method-assign]

    async with await client.open_webrtc_signaling() as signaling:
        assert signaling.websocket is ws  # type: ignore[comparison-overlap]

    args, kwargs = client._session.open_ws.call_args
    assert args == (path,)
    assert kwargs["heartbeat"] == heartbeat


async def test_open_webrtc_signaling_detects_hardware_first() -> None:
    async with NanoKVMClient(BASE_URL, token="t") as client:
        client._session.detect_hardware = AsyncMock(  # type: ignore[method-assign]
            side_effect=lambda: mark_detected(client, HWVersion.PRO)
        )
        client._session.open_ws = AsyncMock(return_value=FakeWebSocket())  # type: ignore[method-assign]

        signaling = await client.open_webrtc_signaling()
        await signaling.close()

    assert client._session.open_ws.call_args.args == ("stream/h264/webrtc",)
