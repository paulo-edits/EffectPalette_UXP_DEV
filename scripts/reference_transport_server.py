"""Reference/test server for transport.js (TECHNICAL_PLAN.md stage 5).

This is throwaway test tooling, not part of the shipped plugin or the stable
EffectPalette product. It exists only so a human can confirm the UXP plugin's
WebSocket client actually connects, completes the token handshake, and can
round-trip an action through executionAdapter.execute() on the host.

A real product-side companion server is separate work, belongs in whichever
repo the user chooses to build it in, and must not be inferred from this
script beyond the wire protocol it demonstrates.

Usage:
    python scripts/reference_transport_server.py

Then in Premiere: reload the FX.palette UXP Diagnostics plugin (or click
"Reconnect now" in its Companion transport panel section) and watch this
terminal for the handshake and status lines. Once connected, this script
automatically sends one `diagnostics.read` probe (read-only), then one
`timeline.applyVideoEffect` action (a real mutation: adds Gamma Correction
to the currently selected video clip, undoable with Ctrl+Z in Premiere) to
prove the transport works for state-changing actions too, not just reads.
"""

import asyncio
import itertools
import json

try:
    import websockets
except ImportError as error:
    raise SystemExit(
        "The 'websockets' package is required. Install it with:\n"
        "    python -m pip install websockets"
    ) from error

HOST = "localhost"
PORT = 58756
TOKEN = "fxpalette-uxp-transport-v1-2eaf1cf6a94b4a5b8f0e3b7c9a5d6e21"

request_ids = itertools.count(1)


async def send_action(websocket, action_type, payload):
    message = {
        "schemaVersion": 1,
        "type": action_type,
        "requestId": f"ref-{next(request_ids)}",
        "payload": payload,
    }
    print(f"-> {json.dumps(message)}")
    await websocket.send(json.dumps(message))
    return message["requestId"]


async def handle_connection(websocket):
    peer = websocket.remote_address
    print(f"\n[connected] {peer}")

    raw_hello = await websocket.recv()
    print(f"<- {raw_hello}")
    try:
        hello = json.loads(raw_hello)
    except json.JSONDecodeError:
        print("[reject] handshake message was not valid JSON, closing")
        await websocket.close()
        return

    if hello.get("type") != "hello" or hello.get("token") != TOKEN:
        print("[reject] handshake type/token mismatch, closing")
        await websocket.close()
        return

    ack = {"type": "hello-ack", "ok": True}
    print(f"-> {json.dumps(ack)}")
    await websocket.send(json.dumps(ack))
    print("[handshake complete] plugin is connected and authenticated")

    probe_request_id = await send_action(websocket, "diagnostics.read", {})
    mutation_request_id = None
    try:
        async for raw in websocket:
            print(f"<- {raw}")
            try:
                response = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if response.get("requestId") == probe_request_id and mutation_request_id is None:
                mutation_request_id = await send_action(
                    websocket,
                    "timeline.applyVideoEffect",
                    {"matchName": "PR.ADBE Gamma Correction"},
                )
            elif response.get("requestId") == mutation_request_id:
                if response.get("ok"):
                    print("[mutation confirmed] Gamma Correction applied through the transport.")
                else:
                    print(f"[mutation failed] {response.get('error')}")
    finally:
        print(f"[disconnected] {peer}")


async def main():
    print(f"Reference transport server listening on ws://{HOST}:{PORT}")
    print("Waiting for the UXP plugin to connect...")
    async with websockets.serve(handle_connection, HOST, PORT):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
