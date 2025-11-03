import socket
import threading
import json
import time

HOST = "127.0.0.1"   # localhost
PORT = 5000          # port number

clients = set()
clients_lock = threading.Lock()

def send_json(sock, obj):
    data = (json.dumps(obj) + "\n").encode("utf-8")
    sock.sendall(data)

def broadcast(msg_obj, exclude=None):
    with clients_lock:
        dead = []
        for c in clients:
            if c is exclude:
                continue
            try:
                send_json(c, msg_obj)
            except Exception:
                dead.append(c)
        for d in dead:
            clients.discard(d)

def handle_client(conn, addr):
    with clients_lock:
        clients.add(conn)
    try:
        f = conn.makefile("r", encoding="utf-8", newline="\n")
        broadcast({"type":"info","text":f"{addr} joined"}, exclude=None)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type")
            if mtype == "chat":
                server_ts = time.time()
                out = {
                    "type":"chat",
                    "from": msg.get("from","?"),
                    "text": msg.get("text",""),
                    "client_ts": msg.get("client_ts"),
                    "server_ts": server_ts
                }
                broadcast(out)
            elif mtype == "sync_request":
                send_json(conn, {
                    "type":"sync_reply",
                    "server_time": time.time()
                })
    except Exception:
        pass
    finally:
        with clients_lock:
            if conn in clients:
                clients.remove(conn)
        try:
            conn.close()
        except Exception:
            pass
        broadcast({"type":"info","text":f"{addr} left"}, exclude=None)

def main():
    print(f"Server starting on {HOST}:{PORT}")
    # Windows-safe version (no reuse_port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((HOST, PORT))
        s.listen()
        print("Waiting for clients...")
        while True:
            conn, addr = s.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()

if __name__ == "__main__":
    main()