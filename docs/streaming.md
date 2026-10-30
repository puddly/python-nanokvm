# Video streaming

The NanoKVM serves the HDMI input as an MJPEG stream and as H.264 over
WebRTC. The client reads MJPEG itself; for WebRTC it handles the signaling and
leaves the media to a WebRTC peer, such as a browser or `aiortc`.

| Need | Method |
|---|---|
| One still image | `get_snapshot()` (non-Pro) |
| Raw JPEG frames | `mjpeg_stream_bytes()` |
| Decoded `PIL.Image` frames | `mjpeg_stream()` |
| Live H.264 video, and audio on the Pro | `open_webrtc_signaling()` |

## The Pro's single encoder

The Pro encodes one stream mode at a time (`StreamMode`, set with
`set_stream_mode()`). Opening the MJPEG stream switches it to MJPEG, and any
WebRTC session in progress stops: its iteration raises
`NanoKVMVideoModeError`. For that reason `get_snapshot()` raises
`NanoKVMNotSupportedError` on the Pro without opening the stream
([NanoKVM-Pro#157](https://github.com/sipeed/NanoKVM-Pro/issues/157)).
`mjpeg_stream()` and `mjpeg_stream_bytes()` still work on the Pro, at that
cost.

## MJPEG

```python
from contextlib import aclosing
from pathlib import Path

Path("screen.jpg").write_bytes(await client.get_snapshot())

async with aclosing(client.mjpeg_stream_bytes()) as frames:
    async for frame in frames:
        ...  # one JPEG per iteration
```

`get_snapshot(timeout=20.0)` returns the first frame, raises `TimeoutError`
when none arrives in time, and `NanoKVMInvalidResponseError` when the stream
ends without one.
The streams run until the caller stops iterating; `aclosing()` closes the HTTP
response as soon as the loop is left.

## WebRTC signaling

`open_webrtc_signaling()` picks the endpoint for the detected hardware and
returns a `nanokvm.webrtc.WebRTCSignaling` on its own WebSocket:

```python
from nanokvm.webrtc import IceCandidate, WebRTCAnswer, WebRTCCandidate

async with await client.open_webrtc_signaling() as signaling:
    await signaling.send_offer(offer_sdp)
    async for event in signaling:
        if isinstance(event, WebRTCAnswer):
            ...  # hand event.sdp to the peer as the remote description
        elif isinstance(event, WebRTCCandidate):
            ...  # hand event.candidate to the peer
```

1. Send the peer's offer with `send_offer(sdp)`. Ask for video, and audio too
   on the Pro; the non-Pro has video only.
2. Send each of the peer's candidates with
   `send_candidate(IceCandidate(...))`, after the offer. `IceCandidate` takes
   the browser's `RTCIceCandidateInit` field names (`sdpMid`, `sdpMLineIndex`,
   `usernameFragment`) or the snake_case ones.
3. Iterate for exactly one `WebRTCAnswer` and any number of
   `WebRTCCandidate` events. Iterating and sending can run concurrently, for
   example the iteration in its own task.
4. Iteration ends when the device closes the WebSocket. Close the session, or
   leave its `async with`, before the client exits: the session owns the
   WebSocket, not the client.

A `ValueError` from `send_offer()` means a Pro offer without audio or video.
The Pro ignores WebSocket pings, so the session sends its application
heartbeat every 60 seconds; a failed heartbeat closes the session, which ends
the iteration.

### What happens on the Pro

The non-Pro negotiates one video peer connection on `stream/h264`, so the
session passes the offer, answer and candidates through. The Pro negotiates
video and audio as two peer connections on `stream/h264/webrtc`; the session
hides that, so the caller sees one offer and one answer on any hardware:

- The offer is split into one single-section offer per media kind.
- The answers are merged into one answer to the original offer, once all
  kinds have answered. Candidates that arrive before that are held, then
  yielded after the answer.
- Candidates are mapped both ways between the sections of the original offer
  and the single-section peers.

The split and merge are public in `nanokvm.webrtc` for callers that run their
own signaling: `split_pro_webrtc_offer()` returns a `ProWebRTCOffer`, whose
`upstream_candidate_for_kind()` and `client_candidate_for_kind()` map
candidates, and `merge_pro_webrtc_answers()` builds the answer.

## Blocking client

`NanoKVMSyncClient` offers `get_snapshot()` and the MJPEG streams as blocking
calls. WebRTC signaling is async-only, because its WebSocket belongs to the
client's event loop: reach it through `run()`, as in
[Blocking client](sync.md).
