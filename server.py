# ─────────────────────────────────────────────────────────
# server.py  –  Brawl Stars relay server
#
# Deploy this on Render.com (free tier):
#   1. Push this file to a GitHub repo
#   2. Create a new "Web Service" on render.com
#   3. Set:  Build Command: pip install -r requirements.txt
#            Start Command: python server.py
#   4. Set environment port to 10000 (Render's default)
#
# The server just relays messages between host and client.
# It never simulates the game – all logic runs on the host.
# ─────────────────────────────────────────────────────────

import socket, threading, struct, os, time

PORT     = int(os.environ.get("PORT", 10000))
BUF      = 4096
TIMEOUT  = 60   # drop room after 60s of inactivity

# rooms: room_code -> {"host": conn, "client": conn, "lock": Lock}
rooms    = {}
rooms_lock = threading.Lock()

def recvall(conn, n):
    """Read exactly n bytes from conn, or raise if disconnected."""
    data = b""
    while len(data) < n:
        chunk = conn.recv(n - len(data))
        if not chunk:
            raise ConnectionError("disconnected")
        data += chunk
    return data

def send_msg(conn, data: bytes):
    """Send length-prefixed message."""
    conn.sendall(struct.pack(">I", len(data)) + data)

def recv_msg(conn) -> bytes:
    """Receive length-prefixed message."""
    raw = recvall(conn, 4)
    length = struct.unpack(">I", raw)[0]
    if length > 1_000_000:
        raise ValueError("message too large")
    return recvall(conn, length)

def relay_loop(src, dst, label):
    """Forward every message from src to dst."""
    try:
        while True:
            msg = recv_msg(src)
            send_msg(dst, msg)
    except Exception:
        pass

def handle_client(conn, addr):
    conn.settimeout(30)
    try:
        # First message: JSON handshake
        # {"role":"host","room":"ABCD"} or {"role":"client","room":"ABCD"}
        import json
        raw = recv_msg(conn)
        info = json.loads(raw)
        role = info["role"]
        room_code = info["room"].upper().strip()

        with rooms_lock:
            if room_code not in rooms:
                rooms[room_code] = {"host": None, "client": None,
                                    "lock": threading.Lock()}
            room = rooms[room_code]

        with room["lock"]:
            if role == "host":
                room["host"] = conn
                send_msg(conn, b"WAITING")   # tell host to wait
                print(f"[{room_code}] host connected from {addr}")
            elif role == "client":
                if room["host"] is None:
                    send_msg(conn, b"NO_HOST")
                    return
                room["client"] = conn
                # tell both sides they're connected
                send_msg(room["host"],   b"CLIENT_JOINED")
                send_msg(room["client"], b"HOST_FOUND")
                print(f"[{room_code}] client connected from {addr} – starting relay")

        if role == "client":
            host_conn = room["host"]
            # relay in both directions simultaneously
            t1 = threading.Thread(target=relay_loop,
                                  args=(host_conn, conn, "host→client"), daemon=True)
            t2 = threading.Thread(target=relay_loop,
                                  args=(conn, host_conn, "client→host"), daemon=True)
            t1.start(); t2.start()
            t1.join(); t2.join()
            with rooms_lock:
                rooms.pop(room_code, None)
            print(f"[{room_code}] session ended")
        else:
            # host: just sit and wait until client joins (relay_loop handles it)
            # block until socket dies
            try:
                while room["client"] is None:
                    time.sleep(0.2)
                # relay threads are started by the client handler, so just wait
                while True:
                    time.sleep(1)
                    try: conn.getpeername()
                    except Exception: break
            except Exception:
                pass
            with rooms_lock:
                rooms.pop(room_code, None)

    except Exception as e:
        print(f"handle_client error from {addr}: {e}")
    finally:
        try: conn.close()
        except Exception: pass

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", PORT))
    srv.listen(32)
    print(f"Relay server listening on port {PORT}")
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()

if __name__ == "__main__":
    main()
