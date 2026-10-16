"""
Keep a UCI engine running, and serve it on a Unix socket to one client at a time, so that a
client can be stopped and started again, with new code, while the engine's search goes on and
keeps its TT. The engine stops only with this server.

The client's lines go to the engine, except for quit, and the engine's lines go to the client;
while no client is connected they are dropped. A client is greeted with a line
"uciserver {json}", with the engine's name, whether it is searching, and the position of that
search, followed by the latest info lines of the search, with nodes and with a PV.
When a client leaves, its auxiliary search (of the cdb-tt branch of Stockfish) is stopped, and the
engine's replies to its last commands are drained, before the next client is greeted.
"""

import argparse, asyncio, json, os, signal


class Server:
    def __init__(self, args):
        self.args = args
        self.client = None  # the writer to the connected client
        self.name = args.engine
        self.searching, self.position, self.searchPosition = False, None, None
        self.auxRunning = False
        self.lastNodes = self.lastPV = None  # the latest info lines of the search
        self.lines = (
            asyncio.Queue()
        )  # engine lines while the server itself waits for some
        self.waiting = None  # the condition the server waits for, if it does

    async def start(self):
        self.proc = await asyncio.create_subprocess_exec(
            self.args.engine,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            limit=2**20,
        )
        self.reader = asyncio.create_task(self.read())
        await self.wait_for("uci", lambda l: l == "uciok")

    def send(self, cmd):
        self.proc.stdin.write((cmd + "\n").encode())

    async def wait_for(self, cmd, last):
        """send a command of the server's own, and wait for the engine's line for which last() holds"""
        self.waiting = last
        self.send(cmd)
        while not last(await self.lines.get()):
            pass
        self.waiting = None

    async def read(self):
        while line := await self.proc.stdout.readline():
            text = line.decode().rstrip("\n")
            if text.startswith("id name "):
                self.name = text[8:]
            elif text.startswith("bestmove"):
                self.searching = False
            elif text.startswith("aux bestmove"):
                self.auxRunning = False
            elif text.startswith("info"):
                if " nodes " in text:
                    self.lastNodes = text
                if " pv " in text and "bound" not in text:
                    self.lastPV = text
            if self.waiting:
                self.lines.put_nowait(text)
            elif self.client:
                self.client.write(line)
        print("the engine stopped", flush=True)
        os.kill(os.getpid(), signal.SIGTERM)

    async def handle(self, reader, writer):
        if self.client or self.waiting:
            writer.write(b'uciserver {"busy": true}\n')
            await writer.drain()
            writer.close()
            return
        state = {
            "name": self.name,
            "searching": self.searching,
            "position": self.searchPosition if self.searching else None,
        }
        writer.write(f"uciserver {json.dumps(state)}\n".encode())
        for line in (self.lastNodes, self.lastPV):
            if self.searching and line:
                writer.write((line + "\n").encode())
        self.client = writer
        print("a client connected", flush=True)
        try:
            while line := await reader.readline():
                cmd = line.decode().strip()
                if cmd == "quit":
                    continue
                if cmd.startswith("position"):
                    self.position = cmd
                elif cmd.startswith("go"):
                    self.searching, self.searchPosition = True, self.position
                elif cmd.startswith("auxgo"):
                    self.auxRunning = True
                self.send(cmd)
        except ConnectionError:
            pass
        finally:
            self.client = None
            print("the client left", flush=True)
            writer.close()
            # the next client should not see replies to this one's commands
            if self.auxRunning:
                await self.wait_for("auxstop", lambda l: l.startswith("aux bestmove"))
            await self.wait_for("isready", lambda l: l == "readyok")


async def main():
    parser = argparse.ArgumentParser(
        description="Serve a UCI engine on a Unix socket, so that its search survives its clients.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--engine", default="stockfish", help="The engine binary.")
    parser.add_argument(
        "--socket", default="uciserver.sock", help="The path of the socket."
    )
    args = parser.parse_args()
    server = Server(args)
    await server.start()
    if os.path.exists(args.socket):
        os.remove(args.socket)
    unix = await asyncio.start_unix_server(server.handle, args.socket, limit=2**20)
    print(f"serving {server.name} on {args.socket}", flush=True)
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    await stop.wait()
    unix.close()
    if server.proc.returncode is None:
        server.send("stop")
        server.send("quit")
        await server.proc.wait()
    os.remove(args.socket)


if __name__ == "__main__":
    asyncio.run(main())
