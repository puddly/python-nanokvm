"""H.264 WebRTC signaling with the NanoKVM.

The non-Pro firmware negotiates one video peer connection on
``stream/h264``. The Pro negotiates separate video and audio peer
connections on ``stream/h264/webrtc``; ``WebRTCSignaling`` splits a
combined browser offer for it and merges the answers back, so callers see
one offer and one answer on either hardware.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import contextlib
from dataclasses import dataclass
import json
import logging
from typing import Any, Literal

from aiohttp import ClientWebSocketResponse, WSMsgType
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .exceptions import NanoKVMVideoModeError

_LOGGER = logging.getLogger(__name__)

MediaKind = Literal["audio", "video"]

_SUPPORTED_KINDS: tuple[MediaKind, ...] = ("audio", "video")
_PRO_OFFER_ORDER: tuple[MediaKind, ...] = ("video", "audio")
_TRANSPORT_SESSION_PREFIXES = (
    "a=ice-ufrag:",
    "a=ice-pwd:",
    "a=ice-options:",
    "a=fingerprint:",
    "a=setup:",
)
_ANSWER_EVENTS: dict[str, MediaKind] = {
    "video-answer": "video",
    "audio-answer": "audio",
}
_CANDIDATE_EVENTS: dict[str, MediaKind] = {
    "video-candidate": "video",
    "audio-candidate": "audio",
}
_STATUS_EVENTS = {"video-status", "audio-status"}
_PRO_HEARTBEAT_SECONDS = 60.0
_PRO_VIDEO_STATUS_INCONSISTENT_MODE = -4
_PRO_VIDEO_STATUS_NAMES = {
    1: "normal",
    -1: "no_image",
    _PRO_VIDEO_STATUS_INCONSISTENT_MODE: "inconsistent_video_mode",
}


class IceCandidate(BaseModel):
    """An ICE candidate, as the browser's ``RTCIceCandidateInit``."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    candidate: str
    sdp_mid: str | None = Field(default=None, alias="sdpMid")
    sdp_m_line_index: int | None = Field(default=None, alias="sdpMLineIndex", ge=0)
    username_fragment: str | None = Field(default=None, alias="usernameFragment")


@dataclass(frozen=True, slots=True)
class WebRTCAnswer:
    """The device's SDP answer to the offer."""

    sdp: str


@dataclass(frozen=True, slots=True)
class WebRTCCandidate:
    """A device ICE candidate, mapped onto the original offer."""

    candidate: IceCandidate


WebRTCEvent = WebRTCAnswer | WebRTCCandidate


class WebRTCSignaling:
    """One WebRTC signaling session on a caller-owned WebSocket.

    Send the offer first, then trickle candidates both ways: iterate for the
    device's answer and candidates. Iteration ends when the WebSocket closes,
    and raises ``NanoKVMVideoModeError`` when the Pro drops WebRTC because
    another video mode took the encoder. Close it before the client exits.
    """

    def __init__(self, websocket: ClientWebSocketResponse, *, pro: bool) -> None:
        self.websocket = websocket
        self._pro = pro
        self._offer: ProWebRTCOffer | None = None
        self._heartbeat: asyncio.Task[None] | None = None
        if pro:
            self._heartbeat = asyncio.create_task(self._send_heartbeats())

    async def __aenter__(self) -> WebRTCSignaling:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Stop the heartbeat and close the WebSocket."""
        if (
            self._heartbeat is not None
            and self._heartbeat is not asyncio.current_task()
        ):
            self._heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat
        await self.websocket.close()

    async def send_offer(self, sdp: str) -> None:
        """Send the browser's SDP offer.

        Raises ``ValueError`` when a Pro offer has neither audio nor video.
        """
        if not self._pro:
            await self._send("video-offer", {"type": "offer", "sdp": sdp})
            return

        offer = split_pro_webrtc_offer(sdp)
        if not offer.offers:
            raise ValueError("WebRTC offer does not contain audio or video media")
        self._offer = offer
        for kind in _PRO_OFFER_ORDER:
            if (kind_sdp := offer.offers.get(kind)) is not None:
                await self._send(f"{kind}-offer", {"type": "offer", "sdp": kind_sdp})

    async def send_candidate(self, candidate: IceCandidate) -> None:
        """Send one browser ICE candidate, after the offer."""
        if not self._pro:
            await self._send("video-candidate", _candidate_payload(candidate))
            return

        if self._offer is None:
            raise RuntimeError("Send the offer before candidates")
        for kind in self._offer.kinds_for_candidate(candidate):
            mapped = self._offer.upstream_candidate_for_kind(kind, candidate)
            if mapped is not None:
                await self._send(f"{kind}-candidate", _candidate_payload(mapped))

    def __aiter__(self) -> AsyncGenerator[WebRTCEvent, None]:
        return self._events()

    async def _events(self) -> AsyncGenerator[WebRTCEvent, None]:
        answers: dict[MediaKind, str] = {}
        # The Pro's candidates arrive per peer; hold them until the merged
        # answer, which is what gives them a section to belong to.
        early: list[tuple[MediaKind, IceCandidate]] = []
        answered = False

        async for msg in self.websocket:
            if msg.type is not WSMsgType.TEXT:
                if msg.type in (WSMsgType.CLOSED, WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
                continue
            payload = _decode_json_object(msg.data)
            event = payload.get("event") if payload is not None else None
            if not isinstance(event, str) or event == "heartbeat":
                continue
            assert payload is not None
            raw_data = payload.get("data")
            data = _decode_signal_data(raw_data)

            if not self._pro:
                if event == "video-answer":
                    if sdp := _answer_sdp(data):
                        yield WebRTCAnswer(sdp)
                elif event == "video-candidate":
                    if (candidate := _parse_candidate(event, data)) is not None:
                        yield WebRTCCandidate(candidate)
                else:
                    _LOGGER.debug("Unhandled WebRTC event: %s", event)
                continue

            offer = self._offer
            if event in _STATUS_EVENTS:
                code = _status_code(data, raw_data)
                _LOGGER.debug(
                    "Pro WebRTC %s: %s",
                    event,
                    _PRO_VIDEO_STATUS_NAMES.get(code, code)
                    if event == "video-status" and code is not None
                    else raw_data,
                )
                if (
                    event == "video-status"
                    and code == _PRO_VIDEO_STATUS_INCONSISTENT_MODE
                ):
                    raise NanoKVMVideoModeError(
                        "NanoKVM Pro stopped WebRTC video because another "
                        "video mode is active"
                    )
            elif (kind := _ANSWER_EVENTS.get(event)) is not None:
                sdp = _answer_sdp(data)
                if offer is None or answered or kind not in offer.offers or not sdp:
                    continue
                answers[kind] = sdp
                if set(offer.expected_kinds).issubset(answers):
                    answered = True
                    yield WebRTCAnswer(merge_pro_webrtc_answers(offer, answers))
                    for early_kind, early_candidate in early:
                        if mapped := offer.home_assistant_candidate_for_kind(
                            early_kind, early_candidate
                        ):
                            yield WebRTCCandidate(mapped)
                    early.clear()
            elif (kind := _CANDIDATE_EVENTS.get(event)) is not None:
                if offer is None:
                    continue
                if (candidate := _parse_candidate(event, data)) is None:
                    continue
                if not answered:
                    early.append((kind, candidate))
                elif mapped := offer.home_assistant_candidate_for_kind(kind, candidate):
                    yield WebRTCCandidate(mapped)
            else:
                _LOGGER.debug("Unhandled Pro WebRTC event: %s", event)

        _LOGGER.debug(
            "WebRTC signaling ended: close_code=%s exception=%r",
            self.websocket.close_code,
            self.websocket.exception(),
        )

    async def _send(self, event: str, data: str | dict[str, Any]) -> None:
        await self.websocket.send_json(
            {
                "event": event,
                "data": data if isinstance(data, str) else json.dumps(data),
            }
        )

    async def _send_heartbeats(self) -> None:
        """Keep the Pro session alive; it ignores WebSocket-level pings."""
        while not self.websocket.closed:
            await asyncio.sleep(_PRO_HEARTBEAT_SECONDS)
            if self.websocket.closed:
                return
            try:
                await self._send("heartbeat", "")
            except Exception as err:
                _LOGGER.debug("Pro WebRTC heartbeat failed: %s", err)
                # Closing ends iteration, which is how the caller hears of it.
                await self.close()
                return


def _answer_sdp(data: dict[str, object] | None) -> str | None:
    sdp = data.get("sdp") if data is not None else None
    return sdp if isinstance(sdp, str) and sdp else None


def _candidate_payload(candidate: IceCandidate) -> dict[str, Any]:
    return candidate.model_dump(by_alias=True, exclude_none=True)


def _parse_candidate(event: str, data: dict[str, object] | None) -> IceCandidate | None:
    if data is None:
        return None
    try:
        return IceCandidate.model_validate(data)
    except ValidationError as err:
        _LOGGER.debug("Invalid %s payload: %s (%r)", event, err, data)
        return None


def _decode_json_object(raw: object) -> dict[str, object] | None:
    try:
        decoded = json.loads(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        _LOGGER.debug("Invalid WebRTC signaling JSON: %r", raw)
        return None
    return decoded if isinstance(decoded, dict) else None


def _decode_signal_data(raw_data: object) -> dict[str, object] | None:
    """Decode the ``data`` field, which the device sends as a JSON string."""
    if isinstance(raw_data, dict):
        return raw_data
    if isinstance(raw_data, str):
        return _decode_json_object(raw_data)
    return None


def _status_code(data: dict[str, object] | None, raw_data: object) -> int | None:
    """Return the numeric code of a Pro status event, in any of its shapes."""
    candidates: list[object] = []
    if data is not None:
        candidates += [data.get(key) for key in ("status", "code", "value")]
    candidates.append(raw_data)
    if isinstance(raw_data, str):
        with contextlib.suppress(ValueError):
            candidates.append(json.loads(raw_data))

    for value in candidates:
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            continue
        with contextlib.suppress(ValueError):
            return int(value)
    return None


@dataclass(frozen=True, slots=True)
class ProMediaSection:
    """A media section of the original combined offer."""

    kind: MediaKind
    mid: str
    index: int
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProWebRTCOffer:
    """Per-kind Pro offers, and the way back to the original offer."""

    offers: dict[MediaKind, str]
    media_sections: tuple[ProMediaSection, ...]

    @property
    def expected_kinds(self) -> tuple[MediaKind, ...]:
        """Return the media kinds the Pro is expected to answer."""
        return tuple(section.kind for section in self.media_sections)

    def section_for_kind(self, kind: MediaKind) -> ProMediaSection | None:
        """Return the original offer section for a media kind."""
        for section in self.media_sections:
            if section.kind == kind:
                return section
        return None

    def kinds_for_candidate(self, candidate: IceCandidate) -> tuple[MediaKind, ...]:
        """Return the Pro peers that should receive a browser candidate."""
        if candidate.sdp_mid is not None:
            for section in self.media_sections:
                if section.mid == candidate.sdp_mid:
                    return (section.kind,)

        if candidate.sdp_m_line_index is not None:
            for section in self.media_sections:
                if section.index == candidate.sdp_m_line_index:
                    return (section.kind,)

        return self.expected_kinds

    def upstream_candidate_for_kind(
        self, kind: MediaKind, candidate: IceCandidate
    ) -> IceCandidate | None:
        """Map a browser candidate onto the single-section Pro peer."""
        if self.section_for_kind(kind) is None:
            return None
        return candidate.model_copy(update={"sdp_mid": "0", "sdp_m_line_index": 0})

    def home_assistant_candidate_for_kind(
        self, kind: MediaKind, candidate: IceCandidate
    ) -> IceCandidate | None:
        """Map a Pro candidate back onto its section of the original offer."""
        section = self.section_for_kind(kind)
        if section is None:
            return None
        return candidate.model_copy(
            update={"sdp_mid": section.mid, "sdp_m_line_index": section.index}
        )


def split_pro_webrtc_offer(offer_sdp: str) -> ProWebRTCOffer:
    """Split a combined offer into one Pro offer per media kind."""
    session_lines, media_lines = _split_sdp_sections(offer_sdp)
    media_sections: list[ProMediaSection] = []
    offers: dict[MediaKind, str] = {}

    for index, lines in enumerate(media_lines):
        kind = _media_kind(lines)
        if kind is None or kind in offers:
            continue

        section = ProMediaSection(
            kind=kind,
            mid=_media_mid(lines, fallback=str(index)),
            index=index,
            lines=lines,
        )
        media_sections.append(section)
        offers[kind] = _join_sdp_sections(
            _single_media_session_lines(session_lines),
            (_replace_mid(section.lines, "0"),),
        )

    return ProWebRTCOffer(offers=offers, media_sections=tuple(media_sections))


def merge_pro_webrtc_answers(
    offer: ProWebRTCOffer, answers: dict[MediaKind, str]
) -> str:
    """Merge the per-kind Pro answers into one answer to the original offer."""
    if not answers:
        raise ValueError("No NanoKVM Pro WebRTC answers received")

    first_session_lines, _ = _split_sdp_sections(next(iter(answers.values())))
    merged_media_lines: list[tuple[str, ...]] = []

    for section in offer.media_sections:
        answer_sdp = answers.get(section.kind)
        if answer_sdp is None:
            raise ValueError(f"Missing {section.kind} WebRTC answer")

        answer_session_lines, answer_media_sections = _split_sdp_sections(answer_sdp)
        answer_media = next(
            (
                media
                for media in answer_media_sections
                if _media_kind(media) == section.kind
            ),
            None,
        )
        if answer_media is None:
            raise ValueError(f"NanoKVM Pro {section.kind} answer has no media section")

        merged_media_lines.append(
            _with_media_transport_lines(
                _replace_mid(answer_media, section.mid), answer_session_lines
            )
        )

    return _join_sdp_sections(
        _merged_session_lines(first_session_lines), tuple(merged_media_lines)
    )


def _split_sdp_sections(
    sdp: str,
) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """Split SDP into session-level lines and media sections."""
    session_lines: list[str] = []
    media_sections: list[list[str]] = []

    for line in sdp.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            continue
        if line.startswith("m="):
            media_sections.append([line])
        elif media_sections:
            media_sections[-1].append(line)
        else:
            session_lines.append(line)

    return tuple(session_lines), tuple(tuple(section) for section in media_sections)


def _join_sdp_sections(
    session_lines: tuple[str, ...], media_sections: tuple[tuple[str, ...], ...]
) -> str:
    """Join SDP lines using CRLF endings."""
    lines = list(session_lines)
    for media_section in media_sections:
        lines.extend(media_section)
    return "\r\n".join(lines) + "\r\n"


def _media_kind(lines: tuple[str, ...]) -> MediaKind | None:
    """Return the kind of an SDP media section, if supported."""
    if not lines or not lines[0].startswith("m="):
        return None
    kind = lines[0][2:].split(maxsplit=1)[0]
    return kind if kind in _SUPPORTED_KINDS else None  # type: ignore[return-value]


def _media_mid(lines: tuple[str, ...], *, fallback: str) -> str:
    """Return the SDP media section MID."""
    for line in lines:
        if line.startswith("a=mid:"):
            return line.removeprefix("a=mid:")
    return fallback


def _single_media_session_lines(session_lines: tuple[str, ...]) -> tuple[str, ...]:
    """Return session lines for one single-section Pro peer."""
    filtered: list[str] = []
    for line in session_lines:
        if not line.startswith("a=group:BUNDLE"):
            filtered.append(line)
        elif "a=group:BUNDLE 0" not in filtered:
            filtered.append("a=group:BUNDLE 0")
    return tuple(filtered)


def _merged_session_lines(session_lines: tuple[str, ...]) -> tuple[str, ...]:
    """Drop session lines that belong to one Pro peer's transport."""
    return tuple(
        line
        for line in session_lines
        if not line.startswith(("a=group:BUNDLE", *_TRANSPORT_SESSION_PREFIXES))
    )


def _replace_mid(lines: tuple[str, ...], mid: str) -> tuple[str, ...]:
    """Replace or add a media section MID."""
    if not any(line.startswith("a=mid:") for line in lines):
        return (*lines, f"a=mid:{mid}")
    return tuple(
        f"a=mid:{mid}" if line.startswith("a=mid:") else line for line in lines
    )


def _with_media_transport_lines(
    media_lines: tuple[str, ...], session_lines: tuple[str, ...]
) -> tuple[str, ...]:
    """Move one answer's session transport attributes into its media section."""
    seen = {
        prefix
        for prefix in _TRANSPORT_SESSION_PREFIXES
        if any(line.startswith(prefix) for line in media_lines)
    }
    result = list(media_lines)
    for line in session_lines:
        prefix = next(
            (p for p in _TRANSPORT_SESSION_PREFIXES if line.startswith(p)), None
        )
        if prefix is not None and prefix not in seen:
            result.append(line)
            seen.add(prefix)
    return tuple(result)
