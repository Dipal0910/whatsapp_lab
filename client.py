import socket
import threading
import json
import time
import queue
import tkinter as tk
from tkinter import ttk, messagebox

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 5000

# optional drift per second (simulate local clock drift)
DRIFT_PER_SEC = 0.0

class Clock:
    """
    Maintains a local clock with optional drift and an adjustable offset
    (set via Cristian's algorithm).
    """
    def __init__(self):
        self.start_real = time.time()
        self.start_local = self.start_real
        self.offset = 0.0  # estimate of (server_time - local_time_now)
        self.lock = threading.Lock()

    def now_local(self):
        # Simulate drift: local time runs slightly faster/slower.
        real_elapsed = time.time() - self.start_real
        drifted = self.start_local + real_elapsed * (1.0 + DRIFT_PER_SEC)
        return drifted

    def now_synced(self):
        with self.lock:
            return self.now_local() + self.offset

    def apply_cristian(self, t_send, server_time, t_recv):
        # Cristian’s algorithm:
        # offset ≈ server_time + (RTT/2) - t_recv_local
        rtt = t_recv - t_send
        est_offset = server_time + (rtt / 2.0) - t_recv
        with self.lock:
            self.offset = est_offset

class ClientApp:
    def __init__(self, master, username):
        self.master = master
        self.master.title(f"Chat Client - {username}")
        self.username = username

        # UI
        self.chat = tk.Text(master, height=20, state="disabled", wrap="word")
        self.chat.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=8, pady=8)

        self.entry = tk.Entry(master)
        self.entry.grid(row=1, column=0, sticky="ew", padx=8, pady=(0,8))
        self.entry.bind("<Return>", self.send_msg)

        self.send_btn = ttk.Button(master, text="Send", command=self.send_msg)
        self.send_btn.grid(row=1, column=1, sticky="e", padx=(0,8), pady=(0,8))

        self.local_label = ttk.Label(master, text="Local: --:--:--")
        self.local_label.grid(row=2, column=0, sticky="w", padx=8, pady=(0,8))

        self.synced_label = ttk.Label(master, text="Synced: --:--:--")
        self.synced_label.grid(row=2, column=1, sticky="e", padx=8, pady=(0,8))

        master.rowconfigure(0, weight=1)
        master.columnconfigure(0, weight=1)

        # networking & threads
        self.sock = None
        self.receiver_thread = None
        self.ui_queue = queue.Queue()
        self.clock = Clock()
        self.running = True

        try:
            self.connect()
        except Exception as e:
            messagebox.showerror("Connection error", str(e))
            master.destroy()
            return

        # schedule periodic tasks
        self.master.after(50, self.process_ui_queue)
        self.master.after(200, self.update_clocks)
        self.master.after(5000, self.sync_with_server)  # every 5s

    def connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.connect((SERVER_HOST, SERVER_PORT))
        self.sock = s
        self.receiver_thread = threading.Thread(target=self.receiver_loop, daemon=True)
        self.receiver_thread.start()
        self.append_chat("[system] connected to server")

    def close(self):
        self.running = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

    def format_ts(self, ts):
        return time.strftime("%H:%M:%S", time.localtime(ts))

    def append_chat(self, line):
        self.chat.configure(state="normal")
        self.chat.insert("end", line + "\n")
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def send_json(self, obj):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        self.sock.sendall(data)

    def send_msg(self, event=None):
        text = self.entry.get().strip()
        if not text:
            return
        client_ts = self.clock.now_local()
        msg = {
            "type":"chat",
            "from": self.username,
            "text": text,
            "client_ts": client_ts
        }
        try:
            self.send_json(msg)
            # local echo
            self.append_chat(f"[{self.format_ts(client_ts)}] you: {text}")
        except Exception as e:
            self.append_chat(f"[error] {e}")
        self.entry.delete(0, "end")

    def receiver_loop(self):
        try:
            f = self.sock.makefile("r", encoding="utf-8", newline="\n")
            for line in f:
                if not self.running:
                    break
                try:
                    msg = json.loads(line.strip())
                except Exception:
                    continue
                self.ui_queue.put(msg)
        except Exception:
            pass
        finally:
            self.ui_queue.put({"type":"info","text":"[system] disconnected"})

    def process_ui_queue(self):
        while True:
            try:
                msg = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            mtype = msg.get("type")
            if mtype == "chat":
                from_user = msg.get("from","?")
                text = msg.get("text","")
                client_ts = msg.get("client_ts")
                server_ts = msg.get("server_ts")
                left = self.format_ts(client_ts) if client_ts else "--:--:--"
                right = self.format_ts(server_ts) if server_ts else "--:--:--"
                self.append_chat(f"[client {left} | server {right}] {from_user}: {text}")
            elif mtype == "info":
                self.append_chat(msg.get("text","[info]"))
        self.master.after(50, self.process_ui_queue)

    def update_clocks(self):
        local_t = self.clock.now_local()
        synced_t = self.clock.now_synced()
        self.local_label.config(text=f"Local:  {self.format_ts(local_t)}")
        self.synced_label.config(text=f"Synced: {self.format_ts(synced_t)}")
        self.master.after(200, self.update_clocks)

    def sync_with_server(self):
        if not self.running:
            return
        try:
            t_send = self.clock.now_local()
            self.send_json({"type":"sync_request"})
            deadline = time.time() + 1.0
            reply = None
            while time.time() < deadline:
                try:
                    msg = self.ui_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                if msg.get("type") == "sync_reply":
                    reply = msg
                    break
                else:
                    self.ui_queue.put(msg)
            if reply is not None:
                t_recv = self.clock.now_local()
                server_time = reply.get("server_time")
                if isinstance(server_time, (int, float)):
                    self.clock.apply_cristian(t_send, server_time, t_recv)
                    self.append_chat("[sync] clock adjusted")
                else:
                    self.append_chat("[sync] invalid server time")
            else:
                self.append_chat("[sync] no reply")
        except Exception as e:
            self.append_chat(f"[sync error] {e}")
        finally:
            self.master.after(5000, self.sync_with_server)

def main():
    import sys
    username = "user"
    if len(sys.argv) > 1:
        username = sys.argv[1]
    root = tk.Tk()
    app = ClientApp(root, username)
    def on_close():
        app.close()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()

if __name__ == "__main__":
    main()