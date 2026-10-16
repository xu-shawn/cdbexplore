import argparse, asyncio, contextlib, json, random, time
import chess, chess.engine, chess.pgn
import cdbsearch
from io import StringIO
from datetime import datetime, timedelta
from multiprocessing import freeze_support


def path_epds(board):
    """the positions on the way from the root of board's move stack to board itself"""
    b = board.root()
    epds = [b.epd()]
    for m in board.move_stack:
        b.push(m)
        epds.append(b.epd())
    return epds


def tt_score(kind, n):
    """a score of the engine's TT reports, in cdb's convention for mates"""
    if kind == "cp":
        return n
    return cdbsearch.CDB_MATE - n if n > 0 else -cdbsearch.CDB_MATE - n


def parse_tt(line):
    """the fields of a ttprobe or ttmove report as a dict, values and evals as ints (or None)"""
    t = line.split()
    d = {}
    if t[0] == "ttmove":
        d["ttmove"], t = t[1], t[2:]
    else:
        t = t[1:]
    i = 0
    while i < len(t):
        if t[i] in ["value", "eval"]:
            if t[i + 1] == "none":
                d[t[i]], i = None, i + 2
            else:
                d[t[i]], i = tt_score(t[i + 1], int(t[i + 2])), i + 3
        else:
            d[t[i]], i = t[i + 1], i + 2
    return d


# a score at which a position is decided, so that cdb's exact score of it does not matter
DECIDED = 400


class LiveEngine:
    """a UCI engine with a search running in the background, whose TT we probe meanwhile: this
    needs the ttprobe and ttmoves commands of the cdb-tt branch of Stockfish"""

    async def start(self, path, options):
        self.info, self.auxInfo, self.name, self.nodesSeen = "", "", path, 0
        self.replies = asyncio.Queue()
        self.proc = await asyncio.create_subprocess_exec(
            path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            limit=2**20,
        )
        self.reader = asyncio.create_task(self.read())
        await self.request("uci", lambda l: l == "uciok")
        for name, value in options.items():
            self.send(f"setoption name {name} value {value}")
        await self.request("isready", lambda l: l == "readyok")

    async def read(self):
        while line := await self.proc.stdout.readline():
            line = line.decode().strip()
            if line.startswith("aux info"):
                # remember the latest complete iteration of the auxiliary search
                if " pv " in line and "bound" not in line:
                    self.auxInfo = line
            elif line.startswith("info"):
                # iterations of a long search take long: count nodes from every line with them
                t = line.split()
                if "nodes" in t:
                    self.nodesSeen = int(t[t.index("nodes") + 1])
                # remember the latest complete iteration of the background search
                if (
                    line.startswith("info depth")
                    and " pv " in line
                    and "bound" not in line
                ):
                    self.info = line
            elif line.startswith("id name"):
                self.name = line[8:]
            elif not line.startswith(("id ", "option ")):
                self.replies.put_nowait(line)

    def send(self, cmd):
        self.proc.stdin.write((cmd + "\n").encode())

    async def request(self, cmd, last):
        """send a command, and return the lines of its reply, up to the one for which last() holds"""
        self.send(cmd)
        lines = []
        while True:
            try:
                lines.append(await asyncio.wait_for(self.replies.get(), timeout=60))
            except asyncio.TimeoutError:
                if self.reader.done():
                    raise RuntimeError(f"the engine stopped while we waited for: {cmd}")
                continue
            if last(lines[-1]):
                return lines

    def background(self):
        """the state of the background search, for the statistics"""
        t = self.info.split()
        if "nodes" not in t:
            return {}
        i = t.index("score")
        return {
            "depth": int(t[t.index("depth") + 1]),
            "nodes": self.nodesSeen,
            "score": tt_score(t[i + 1], int(t[i + 2])),
            "pv": t[t.index("pv") + 1 :][:12],
        }

    def status(self):
        """depth, nodes, score and the start of the PV of the background search"""
        t = self.info.split()
        if "nodes" not in t:
            return "just started"
        i = t.index("score")
        pv = " ".join(t[t.index("pv") + 1 :][:6])
        return f"depth {t[t.index('depth') + 1]}, {self.nodesSeen / 1e9:.2f}B nodes, {t[i + 1]} {t[i + 2]}, pv {pv} ..."

    def nodes(self):
        return self.nodesSeen

    def position(self, board):
        """set the engine's position to the one on board, with the moves leading to it"""
        moves = " ".join(m.uci() for m in board.move_stack)
        self.send(
            f"position fen {board.root().fen()}" + bool(moves) * f" moves {moves}"
        )

    async def aux_search(self, board, nodes, move=None):
        """a search of the auxiliary thread, which shares the TT with the background search:
        returns its best move and score for the side to move, possibly restricted to a move
        """
        moves = " ".join(m.uci() for m in board.move_stack)
        self.auxInfo = ""
        self.send(
            f"auxposition fen {board.root().fen()}" + bool(moves) * f" moves {moves}"
        )
        restrict = f" searchmoves {board.uci(move)}" if move else ""
        best = (
            await self.request(
                f"auxgo nodes {nodes}{restrict}", lambda l: l.startswith("aux bestmove")
            )
        )[-1].split()[2]
        t = self.auxInfo.split()
        if "score" not in t:  # too few nodes for a single complete iteration
            return board.parse_uci(best), None
        i = t.index("score")
        return board.parse_uci(best), tt_score(t[i + 1], int(t[i + 2]))

    def to_moves(self, board, ucis):
        """the legal beginning of a list of uci moves from board"""
        b, moves = board.copy(), []
        for u in ucis:
            try:
                m = b.parse_uci(u)
            except ValueError:
                break
            moves.append(m)
            b.push(m)
        return moves

    async def aux_line(self, board, nodes, move=None):
        """a search of the auxiliary threads of the position on board without the moves that led to
        it, as cdb sees it: its PV, possibly restricted to a move, and its score for the side to move
        """
        self.auxInfo = ""
        self.send(f"auxposition fen {board.fen()}")
        restrict = f" searchmoves {board.uci(move)}" if move else ""
        await self.request(
            f"auxgo nodes {nodes}{restrict}", lambda l: l.startswith("aux bestmove")
        )
        t = self.auxInfo.split()
        if "score" not in t or "pv" not in t:
            return [], None
        i = t.index("score")
        pv = self.to_moves(board, t[t.index("pv") + 1 :])
        return pv, tt_score(t[i + 1], int(t[i + 2]))

    async def ttpv(self, board, plies):
        """the PV formed by the TT moves from board"""
        self.position(board)
        line = (await self.request(f"ttpv {plies}", lambda l: l.startswith("ttpv")))[-1]
        return self.to_moves(board, line.split()[1:])

    async def probe(self, board):
        """the TT entry of the position on board"""
        self.position(board)
        return parse_tt(
            (await self.request("ttprobe", lambda l: l.startswith("ttprobe")))[-1]
        )

    async def tt(self, board):
        """the TT entry of the position on board, and those of the positions after its legal moves"""
        self.position(board)
        node = parse_tt(
            (await self.request("ttprobe", lambda l: l.startswith("ttprobe")))[-1]
        )
        children = await self.request("ttmoves", lambda l: l == "ttmoves end")
        return node, {c["ttmove"]: c for c in map(parse_tt, children[:-1])}


class SFGuide:
    """use Stockfish to find where cdb's main lines are likely wrong, and let cdbsearch explore there"""

    def __init__(self, engine, args):
        self.engine = engine
        self.args = args
        self.explored = {}  # the depth to which each target has been explored
        # for the statistics: the round, the positions where cdb disagreed with the engine in the
        # first walk, and the positions whose engine move has been explored
        self.stats = open(args.stats, "a") if args.stats else None
        self.round, self.baseline, self.exploredAt = 0, None, set()
        self.measureDB = None  # for fresh queries of cdb
        # positions of the first walk that are never explored, as a control, and those of its
        # disagreements without other ones below them
        self.heldOut, self.leaves = set(), set()
        # with --refute: how often each defence has been repaired, and the positions
        # where cdb agreed with the engine, or could not be brought closer, with cdb's score then
        self.repaired, self.agreed, self.stalled = {}, {}, {}

    def log(self, event, **record):
        """append a record to the statistics file, see cdbsfguide_stats.py"""
        if self.stats:
            record = {
                "time": datetime.now().isoformat(),
                "round": self.round,
                "event": event,
                **record,
            }
            self.stats.write(json.dumps(record) + "\n")
            self.stats.flush()

    async def cdb_now(self, board):
        """cdb's current scores at board, which queues it if cdb does not know it yet"""
        if self.measureDB is None:
            self.measureDB = self.chessdb(board)
        scored = await self.measureDB.queryall(board.epd(), board.chess960, skipTT=True)
        return {m: s for m, s in scored.items() if m != "depth"}

    async def propagate(self, board):
        """query the positions from board back up to the root, each one after the one below it"""
        b = board.copy()
        while True:
            await self.cdb_now(b)
            if not b.move_stack:
                break
            b.pop()

    async def short(self, board, move=None):
        """a short search of the auxiliary threads of the position as cdb sees it: the engine's
        line, possibly restricted to a move, and its score for the side to move"""
        return await self.live.aux_line(board, self.args.leafNodes, move)

    def final(self, board):
        """whether there is nothing to fix at board: the game is over, or cdb knows it from tablebases"""
        return (
            board.is_game_over() or chess.popcount(board.occupied) <= cdbsearch.CDB_EGTB
        )

    def agree(self, cv, sv):
        """whether cdb's score of a position agrees with the engine's, both for the side to move:
        within --tol, or both decided for the same side, where the exact score does not matter
        """
        return (
            abs(cv - sv) <= self.args.tol
            or min(cv, sv) >= DECIDED
            or max(cv, sv) <= -DECIDED
        )

    async def cdb_line(self, start):
        """the positions of cdb's PV from start, to the leaf of its tree"""
        if self.measureDB is None:
            self.measureDB = self.chessdb(start)
        line, b = [start.copy()], start.copy()
        for m in self.live.to_moves(b, await self.measureDB.querypv(b.epd()) or []):
            b.push(m)
            line.append(b.copy())
        return line

    async def add_line(self, board, pv):
        """add the positions of a line from board to cdb, and query them from the end of the line up
        to the root, so that cdb updates its scores along it: returns the last position of the line
        """
        line, b = [], board.copy()
        for m in pv:
            if self.final(b):
                break
            b.push(m)
            line.append(b.copy())
        if not line:
            return board
        await asyncio.gather(*(self.cdb_now(p) for p in line))
        await self.propagate(line[-1])
        return line[-1]

    async def fix(self, p, budget, indent, known=None):
        """Bring cdb's value of p closer to the engine's, by adding the engine's lines from p to cdb:
        its own line, and where cdb is too optimistic for the side to move and plays another move, its
        refutation of that move. Then extend the lines from their ends with further short searches, as
        long as that brings cdb's value closer. Returns whether cdb agrees in the end. When adding
        lines twice in a row does not bring cdb closer, cdb's own evals of the positions on the lines
        differ from the engine's, and more positions would not change that: then p is not tried again
        until cdb's score of it changes."""
        moves = await self.cdb_now(p)
        if not moves:
            return False
        if known is None:
            if budget[0] <= 0:
                return False
            budget[0] -= 1
            known = await self.short(p)
        pv, sv = known
        if sv is None or not pv:
            return False
        best = max(moves, key=moves.get)
        gap = moves[best] - sv
        if self.agree(moves[best], sv):
            self.agreed[p.epd()] = moves[best]
            return True
        w = 1 if p.turn == chess.WHITE else -1
        bestMove = chess.Move.from_uci(best)
        lines = [(p, pv)]
        if gap > 0 and pv[0] != bestMove and budget[0] > 0:
            budget[0] -= 1
            refutation, _ = await self.short(p, bestMove)
            if refutation:
                lines.append((p, refutation))
        print(
            f"{indent}{p.fullmove_number}{'.' if p.turn else '...'} cdb {p.san(bestMove)} {w * moves[best]:+}, engine {p.san(pv[0])} {w * sv:+}: "
            + (
                "add the engine's line"
                if len(lines) == 1
                else f"add the engine's line, and its refutation of {p.san(bestMove)}"
            ),
            flush=True,
        )
        stalls = 0
        while True:
            ends = [await self.add_line(b, l) for b, l in lines]
            moves = await self.cdb_now(p)
            best = max(moves, key=moves.get)
            newGap = moves[best] - sv
            print(
                f"{indent}  {sum(len(l) for _, l in lines)} plies added, cdb now {p.san(chess.Move.from_uci(best))} {w * moves[best]:+}",
                flush=True,
            )
            if self.agree(moves[best], sv):
                self.agreed[p.epd()] = moves[best]
                return True
            stalls = stalls + 1 if abs(newGap) > abs(gap) - self.args.tol / 2 else 0
            gap = newGap
            if stalls >= 2:
                self.stalled[p.epd()] = moves[best]
                return False
            # extend the lines from their ends
            lines = []
            for e in ends:
                if self.final(e) or budget[0] <= 0:
                    continue
                budget[0] -= 1
                more, _ = await self.short(e)
                if more:
                    lines.append((e, more))
            if not lines:
                return False

    async def repair(self, start, budget, indent):
        """Repair cdb's line from start from the bottom up: follow it to the leaf of cdb's tree, check
        its positions upward with short searches, which are reliable near the leaves, and fix the
        deepest disagreement; then look at the line again, as cdb may now play another one. Positions
        that agreed, or that could not be brought closer, are skipped until cdb's score of them changes,
        so that each look reaches further up the line."""
        fixes = 0
        for attempt in range(self.args.repairs):
            line = await self.cdb_line(start)
            target, checked = None, 0
            for p in reversed(line):
                if checked >= self.args.scanPlies or budget[0] <= 0:
                    break
                if self.final(p):
                    continue
                moves = await self.cdb_now(p)
                if not moves:
                    continue
                v = max(moves.values())
                if v in (self.agreed.get(p.epd()), self.stalled.get(p.epd())):
                    continue
                checked += 1
                budget[0] -= 1
                found = await self.short(p)
                if found[1] is None:
                    continue
                if not self.agree(v, found[1]):
                    target = p
                    break
                self.agreed[p.epd()] = v
            if target is None:
                break
            fixes += 1
            await self.fix(target, budget, indent, found)
        return fixes

    async def refute(self, root):
        """Prove the engine's line to cdb, for the side to move at the root. Walk the PV of the
        background search as far as its TT entries are deep, and add its positions to cdb. At the
        opponent's positions on it, every move that cdb scores better for the opponent than the
        engine's move is a defence against the line, which caps cdb's score of it: repair cdb's
        lines after those, from the bottom up, and the end of the engine's line too. Where cdb
        scores another move of our side above the engine's, nothing needs doing: our line only has
        to be proven good, and a move cdb wrongly prefers to it does not lower cdb's score of it.
        """
        pv = await self.live.ttpv(root, self.args.pvPlies)
        board, line = root.copy(), []
        for move in pv:
            if self.final(board):
                break
            # deep in the PV, the moves come from shallow searches
            node = await self.live.probe(board)
            if node.get("hit") != "1" or int(node["depth"]) < self.args.minDepth:
                break
            line.append((board.copy(), move))
            board.push(move)
        # add the line to cdb, and let cdb update its scores along it
        await asyncio.gather(*(self.cdb_now(b) for b, _ in line[1:]))
        if not self.final(board):
            await self.cdb_now(board)
        await self.propagate(board)

        found = []  # the defences: position, the engine's move, the defence, its gap
        for b, m in line:
            if b.turn == root.turn:
                continue
            moves = await self.cdb_now(b)
            ours = moves.get(b.uci(m))
            if ours is None:
                continue
            for a, score in moves.items():
                if a != b.uci(m) and score > ours + self.args.margin:
                    found.append((b, m, chess.Move.from_uci(a), score - ours))
        print(
            f"  the engine's line, {len(line)} plies with TT entries of depth {self.args.minDepth} or more: {root.variation_san(pv[: min(len(line), 16)])} ...;"
            f" cdb sees {len(found)} better defences against it",
            flush=True,
        )
        self.log("pvwalk", plies=len(line), disagreements=len(found))

        # the end of the engine's line
        budget = [self.args.repairBudget]
        fixes = await self.repair(board, budget, "    ")
        print(
            f"  the end of the line: {fixes} fixes, {self.args.repairBudget - budget[0]} engine searches",
            flush=True,
        )
        self.log("lineEnd", fixes=fixes, searches=self.args.repairBudget - budget[0])

        # among the defences repaired the fewest times so far, those closest to the root first, as
        # the shallowest defence on cdb's path caps its score of the line, then the largest gaps;
        # each round repairs them deepest first
        key = lambda t: (t[0].epd(), t[2].uci())
        chosen = sorted(
            found,
            key=lambda t: (self.repaired.get(key(t), 0), len(t[0].move_stack), -t[3]),
        )
        for b, m, a, gap in sorted(
            chosen[: self.args.maxMarks], key=lambda t: -len(t[0].move_stack)
        ):
            at = (
                self.root.variation_san(b.move_stack[len(self.root.move_stack) :])
                or "(root)"
            )
            w = 1 if b.turn == chess.WHITE else -1
            before = await self.cdb_now(b)
            print(
                f"  {at}: cdb {b.san(a)} {w * before[a.uci()]:+} vs the engine's {b.san(m)} {w * before[m.uci()]:+}",
                flush=True,
            )
            self.repaired[key((b, m, a))] = self.repaired.get(key((b, m, a)), 0) + 1
            child, budget = b.copy(), [self.args.repairBudget]
            child.push(a)
            fixes = await self.repair(child, budget, "    ")
            await self.propagate(b)
            after = await self.cdb_now(b)
            print(
                (
                    f"    after {fixes} fixes and {self.args.repairBudget - budget[0]} engine searches: cdb {b.san(a)} {w * after[a.uci()]:+}, {b.san(m)} {w * after[m.uci()]:+}"
                    if a.uci() in after and m.uci() in after
                    else f"    after {fixes} fixes and {self.args.repairBudget - budget[0]} engine searches"
                ),
                flush=True,
            )
            self.log(
                "defence",
                line=at,
                engineMove=m.uci(),
                defence=a.uci(),
                before={
                    "defence": before.get(a.uci()),
                    "engineMove": before.get(m.uci()),
                },
                after={"defence": after.get(a.uci()), "engineMove": after.get(m.uci())},
                fixes=fixes,
                searches=self.args.repairBudget - budget[0],
            )

    async def cdb_view(self, board, move):
        """cdb's current best move at board, its score, and cdb's score for the given move"""
        moves = await self.cdb_now(board)
        if not moves:
            return {"best": None, "bestScore": None, "score": None}
        best = max(moves, key=lambda m: moves[m])
        return {"best": best, "bestScore": moves[best], "score": moves.get(move)}

    def chessdb(self, board):
        """a fresh ChessDB, so that we see cdb's current scores rather than cached ones"""
        return cdbsearch.ChessDB(
            concurrency=self.args.concurrency,
            evalDecay=self.args.evalDecay,
            cursedWins=False,
            TBsearch=False,
            rootBoard=board.copy(),
            user=self.args.user,
            showErrors=not self.args.suppressErrors,
        )

    async def cdb_moves(self, chessdb, board):
        """cdb's scored moves for the position on board, as a dict from uci to score"""
        scored = await chessdb.queryall(board.epd(), board.chess960)
        return {m: s for m, s in scored.items() if m != "depth"}

    async def sf_eval(self, board, move=None):
        """Stockfish's best move and its score for the side to move, possibly restricted to a given move"""
        info = await self.engine.analyse(
            board,
            chess.engine.Limit(nodes=self.args.nodes),
            root_moves=None if move is None else [move],
        )
        score = info["score"].pov(board.turn).score(mate_score=cdbsearch.CDB_MATE)
        return info["pv"][0], score

    async def sf_search(self, board):
        """a single long Stockfish search, showing its progress: returns the PV and its score for the side to move"""
        best = None
        limit = chess.engine.Limit(nodes=self.args.nodes)
        with await self.engine.analysis(board, limit) as analysis:
            async for info in analysis:
                if "pv" not in info or "score" not in info:
                    continue
                if "lowerbound" in info or "upperbound" in info:
                    continue  # fail high/low: the score is a bound, the PV possibly truncated
                newDepth = best is None or info.get("depth") != best.get("depth")
                best = info
                if newDepth:
                    score = (
                        info["score"]
                        .pov(board.turn)
                        .score(mate_score=cdbsearch.CDB_MATE)
                    )
                    print(
                        f"    depth {info.get('depth'):2} {score:+5}  {board.variation_san(info['pv'][:10])} ..."
                        f"  ({info.get('nodes', 0) / 1e9:.2f}B nodes, {timedelta(seconds=int(info.get('time', 0)))})",
                        flush=True,
                    )
        score = best["score"].pov(board.turn).score(mate_score=cdbsearch.CDB_MATE)
        return best["pv"], score

    def target(self, board, move, why):
        """a target: the position after move, in the extended EPD syntax"""
        after = board.copy()
        after.push(move)
        return {
            "why": why,
            "ply": len(board.move_stack),
            "line": board.root().variation_san(board.move_stack) or "(root)",
            "parent": board.epd(),
            # the positions from the root to the target itself
            "path": path_epds(board) + [after.epd()],
            "move": board.san(move),
            "epd": board.root().fen()
            + " moves "
            + " ".join(m.uci() for m in board.move_stack + [move]),
        }

    def summarize(self, targets):
        """list the targets, and write them to the targets file"""
        for t in targets:
            print(f"  {t['why']}  {t['line']} -> {t['move']}")
            print(f"           {t['epd']}")
        if self.args.targets:
            with open(self.args.targets, "w") as f:
                for t in targets:
                    f.write(t["epd"] + "\n")
            print(f"Wrote the targets to {self.args.targets}.")
        print(flush=True)

    async def walk(self, root):
        """visit cdb's main line from root, and Stockfish's alternatives where the two disagree"""
        chessdb = self.chessdb(root)
        queue, seen, targets = [root.copy()], set(), []
        # the largest amount by which Stockfish preferred cdb's move to its own
        noise = 0
        print(f"Walk from {root.epd()}, {datetime.now().isoformat()}", flush=True)
        while queue and len(seen) < self.args.maxNodes:
            # breadth first: positions closer to the root matter more
            board = queue.pop(0)
            ply = len(board.move_stack) - len(root.move_stack)
            if board.epd() in seen or board.is_game_over(claim_draw=True):
                continue
            if chess.popcount(board.occupied) <= cdbsearch.CDB_EGTB:
                continue  # cdb knows these from its EGTB
            seen.add(board.epd())
            moves = await self.cdb_moves(chessdb, board)
            if not moves:
                continue
            cdbBest = max(moves, key=lambda m: moves[m])
            sfMove, sfScore = await self.sf_eval(board)
            sfBest = board.uci(sfMove)
            # how much better Stockfish thinks its move is than cdb's: both measured by Stockfish, as cdb's scale differs,
            # and both with a search restricted to that one move, as such a search goes deeper than the unrestricted one
            gap, sfScoreCdb = 0, sfScore
            if sfBest != cdbBest:
                _, sfScore = await self.sf_eval(board, sfMove)
                _, sfScoreCdb = await self.sf_eval(board, board.parse_uci(cdbBest))
                gap = sfScore - sfScoreCdb
                noise = max(noise, -gap)
            side = "white" if board.turn == chess.WHITE else "black"
            target = gap >= self.args.threshold and self.args.side in ["both", side]

            line = root.variation_san(board.move_stack[len(root.move_stack) :])
            cdbSan, sfSan = board.san(board.parse_uci(cdbBest)), board.san(sfMove)
            if sfBest == cdbBest:
                verdict = f"agree       SF {sfScore:+5}"
            else:
                sfInCdb = f"cdb {moves[sfBest]:+5}" if sfBest in moves else "unscored "
                verdict = f"SF {sfSan:>6} ({sfInCdb}) {sfScore:+5} vs {sfScoreCdb:+5}, gap {gap}"
            print(
                f"  ply {ply:2} {side[0].upper()}  cdb {cdbSan:>6} {moves[cdbBest]:+5} | {verdict:<52}{' TARGET' if target else '':7} | {line or '(root)'}",
                flush=True,
            )

            if target:
                targets.append(self.target(board, sfMove, f"gap {gap:3}"))
                targets[-1]["gap"] = gap
            if ply + 1 < self.args.maxPly:
                for m in [cdbBest] + ([sfBest] if target else []):
                    child = board.copy()
                    child.push(board.parse_uci(m))
                    queue.append(child)

        targets.sort(key=lambda t: (-t["gap"], t["ply"]))
        print(f"Visited {len(seen)} positions, found {len(targets)} targets.")
        if noise:
            print(
                f"Searches restricted to cdb's move came out up to {noise} cp better than those for Stockfish's own move:"
                " targets with a gap of that size or less may well be noise."
            )
        self.summarize(targets)
        return targets

    async def compare_line(self, chessdb, root, start, pv):
        """follow Stockfish's PV from start, and return the positions where cdb prefers another move"""
        disagreements = []
        board = start.copy()
        for move in pv:
            ply = len(board.move_stack) - len(root.move_stack)
            if ply >= self.args.maxPly or board.is_game_over(claim_draw=True):
                break
            if chess.popcount(board.occupied) <= cdbsearch.CDB_EGTB:
                break  # cdb knows these from its EGTB
            moves = await self.cdb_moves(chessdb, board)
            if not moves:
                break
            cdbBest = max(moves, key=lambda m: moves[m])
            sfBest, sfSan = board.uci(move), board.san(move)
            side = "white" if board.turn == chess.WHITE else "black"
            if sfBest == cdbBest:
                verdict = f"agree   {sfSan:>6} {moves[sfBest]:+5}"
            else:
                sfInCdb = f"{moves[sfBest]:+5}" if sfBest in moves else "unscored"
                verdict = f"cdb     {board.san(board.parse_uci(cdbBest)):>6} {moves[cdbBest]:+5}, SF {sfSan:>6} {sfInCdb}"
                if self.args.side in ["both", side]:
                    disagreements.append((board.copy(), move, cdbBest))
            line = root.variation_san(board.move_stack[len(root.move_stack) :])
            print(
                f"  ply {ply:2} {side[0].upper()}  {verdict:<42} | {line or '(root)'}",
                flush=True,
            )
            board.push(move)
        return disagreements

    def pv_targets(self, root, disagreements):
        """the positions after Stockfish's moves where cdb disagrees, closest to the root first"""
        targets, seen = [], set()
        for board, move, _ in sorted(disagreements, key=lambda d: len(d[0].move_stack)):
            t = self.target(
                board, move, f"ply {len(board.move_stack) - len(root.move_stack):2}"
            )
            if t["epd"] not in seen:
                seen.add(t["epd"])
                targets.append(t)
        print(
            f"Found {len(targets)} positions where cdb disagrees with Stockfish's lines."
        )
        self.summarize(targets)
        return targets

    async def pv_walk(self, root):
        """follow the PVs of long Stockfish searches: the first from root, the others refuting cdb's choices where it disagrees"""
        chessdb = self.chessdb(root)
        queue, searched, disagreements = [root.copy()], set(), []
        self.lines = []
        while queue and len(searched) < self.args.maxSearches:
            # closest to the root first
            start = queue.pop(0)
            if start.epd() in searched:
                continue
            searched.add(start.epd())
            line = root.variation_san(start.move_stack[len(root.move_stack) :])
            print(
                f"Stockfish search from {line or '(root)'}, {datetime.now().isoformat()}",
                flush=True,
            )
            pv, score = await self.sf_search(start)
            print(f"  Stockfish: {score:+} {start.variation_san(pv)}", flush=True)
            self.lines.append((start, pv))
            new = await self.compare_line(chessdb, root, start, pv)
            disagreements += new
            # where cdb prefers another move, ask Stockfish how it would answer that move
            for board, _, cdbBest in new:
                refute = board.copy()
                refute.push(refute.parse_uci(cdbBest))
                if len(refute.move_stack) - len(root.move_stack) < self.args.maxPly:
                    queue.append(refute)
            queue.sort(key=lambda b: len(b.move_stack))
            print(flush=True)
        return self.pv_targets(root, disagreements)

    async def pv_check(self, root):
        """compare cdb with the PVs of the earlier Stockfish searches once more"""
        chessdb = self.chessdb(root)
        disagreements = []
        for start, pv in self.lines:
            line = root.variation_san(start.move_stack[len(root.move_stack) :])
            print(f"Stockfish's line from {line or '(root)'}:", flush=True)
            disagreements += await self.compare_line(chessdb, root, start, pv)
        return self.pv_targets(root, disagreements)

    async def live_walk(self, root):
        """walk cdb's main line from root, comparing cdb's choices with the TT of the background search"""
        chessdb = self.chessdb(root)
        queue, seen, targets = [root.copy()], set(), []
        agree, noData, disagreements = 0, 0, []
        # the largest amount by which the auxiliary searches preferred cdb's move to the engine's
        noise = 0
        print(f"Walk from {root.epd()}, {datetime.now().isoformat()}", flush=True)
        print(f"  background search: {self.live.status()}", flush=True)

        def bounded(c):
            if not c or c.get("hit") != "1" or c.get("value") is None:
                return "     -"
            sign = {"exact": "=", "lower": ">=", "upper": "<="}.get(c["bound"], "?")
            return f"{sign}{c['value']:+} d{c['depth']}"

        while queue and len(seen) < self.args.maxNodes:
            # breadth first: positions closer to the root matter more
            board = queue.pop(0)
            ply = len(board.move_stack) - len(root.move_stack)
            if board.epd() in seen or board.is_game_over(claim_draw=True):
                continue
            if chess.popcount(board.occupied) <= cdbsearch.CDB_EGTB:
                continue  # cdb knows these from its EGTB
            seen.add(board.epd())
            moves = await self.cdb_moves(chessdb, board)
            if not moves:
                continue
            cdbBest = max(moves, key=lambda m: moves[m])
            node, children = await self.live.tt(board)
            side = "white" if board.turn == chess.WHITE else "black"
            line = root.variation_san(board.move_stack[len(root.move_stack) :])
            cdbSan = board.san(board.parse_uci(cdbBest))

            # the engine's choice is its TT move, if that entry is deep enough to trust, or else the
            # choice of a search on the auxiliary thread, which also deepens the shared TT there
            sfMove, source = None, "TT"
            if node.get("hit") == "1" and int(node["depth"]) >= self.args.minDepth:
                try:
                    sfMove = board.parse_uci(node["move"])
                except ValueError:
                    pass
            if sfMove is None and self.args.auxNodes:
                sfMove, _ = await self.live.aux_search(board, self.args.auxNodes)
                source = "aux"

            if sfMove is None:
                verdict = f"no deep TT entry (depth {node.get('depth', '-')})"
                target, follow = False, []
                noData += 1
            elif board.uci(sfMove) == cdbBest:
                verdict = f"agree   {source} {bounded(children.get(cdbBest)) if source == 'TT' else ''}"
                target, follow = False, [cdbBest]
                agree += 1
            else:
                # Any disagreement is worth exploring: cdb's search settles the evals. Only with
                # --scoreGap we first measure it, with equal budgets for both moves on the
                # auxiliary thread, profiting from the shared TT.
                sfBest = board.uci(sfMove)
                s, c = children.get(sfBest, {}), children.get(cdbBest, {})
                note = f"TT {bounded(s)} vs {bounded(c)}"
                target = self.args.side in ["both", side]
                if target:
                    disagreements.append((board.copy(), sfBest, line or "(root)", ply))
                if target and self.args.scoreGap:
                    _, sScore = await self.live.aux_search(
                        board, self.args.auxNodes, sfMove
                    )
                    _, cScore = await self.live.aux_search(
                        board, self.args.auxNodes, board.parse_uci(cdbBest)
                    )
                    gap = sScore - cScore if None not in (sScore, cScore) else 0
                    noise = max(noise, -gap)
                    note += f", aux {sScore:+} vs {cScore:+}, gap {gap}"
                    target = gap >= self.args.threshold
                verdict = f"{source} {board.san(sfMove):>6} ({note})"
                follow = [cdbBest] + ([sfBest] if target else [])
            print(
                f"  ply {ply:2} {side[0].upper()}  cdb {cdbSan:>6} {moves[cdbBest]:+5} | {verdict:<60}{' TARGET' if target else '':7} | {line or '(root)'}",
                flush=True,
            )
            if target:
                targets.append(self.target(board, sfMove, f"ply {ply:2}"))
            if ply + 1 < self.args.maxPly:
                for m in follow:
                    child = board.copy()
                    child.push(board.parse_uci(m))
                    queue.append(child)

        print(f"Visited {len(seen)} positions, found {len(targets)} targets.")
        if noise:
            print(
                f"Auxiliary searches came out up to {noise} cp better for cdb's move than for the engine's:"
                " targets with a gap of that size or less may well be noise."
            )

        # The disagreements of the first walk are a fixed set to measure progress on: does cdb now
        # play the engine's move there? Those never explored show cdb's drift without us.
        if self.baseline is None:
            self.baseline = disagreements
            # A random part of them is never explored, to tell our effect from cdb's drift, nor
            # are the lines below them, which share in their scores. So that this costs little,
            # only those without other disagreements below them (leaves) are held out.
            above = set()
            for b, _, _, _ in disagreements:
                above.update(path_epds(b)[:-1])
            self.leaves = {d[0].epd() for d in disagreements if d[0].epd() not in above}
            eligible = [
                d
                for d in disagreements
                if d[3] >= self.args.holdoutPly and d[0].epd() in self.leaves
            ]
            k = round(self.args.holdout * len(eligible))
            heldOut = random.Random(self.args.seed).sample(eligible, k)
            self.heldOut = {b.epd() for b, _, _, _ in heldOut}
            if k:
                print(
                    f"Holding out {k} of the {len(eligible)} disagreements of the first walk that are at least {self.args.holdoutPly} plies deep, with no other ones below them, from exploration, as a control."
                )
        kept = [t for t in targets if not self.heldOut.intersection(t["path"])]
        if len(kept) < len(targets):
            print(f"Leaving out {len(targets) - len(kept)} targets in held out lines.")
        targets = kept
        baseline = []
        for b, m, l, _ in self.baseline:
            v = await self.cdb_view(b, m)
            gap = (
                v["bestScore"] - v["score"]
                if None not in (v["bestScore"], v["score"])
                else None
            )
            baseline.append(
                {
                    "line": l,
                    "move": m,
                    "explored": b.epd() in self.exploredAt,
                    "heldOut": b.epd() in self.heldOut,
                    "leaf": b.epd() in self.leaves,
                    "resolved": v["best"] == m,
                    "cdbGap": gap,
                }
            )
        for label, group in [
            ("explored", [b for b in baseline if b["explored"]]),
            ("held out", [b for b in baseline if b["heldOut"]]),
            (
                "not explored yet",
                [b for b in baseline if not b["explored"] and not b["heldOut"]],
            ),
        ]:
            if group:
                print(
                    f"Baseline: {sum(b['resolved'] for b in group)} of {len(group)} disagreements of the first walk"
                    f" ({label}) now resolved."
                )
        self.log(
            "walk",
            background=self.live.background(),
            root=await self.cdb_moves(self.chessdb(root), root),
            positions=len(seen),
            agree=agree,
            disagree=len(disagreements),
            noData=noData,
            targets=len(targets),
            baseline=baseline,
        )
        self.summarize(targets)
        return targets

    async def explore(self, target):
        """let cdbsearch explore the position after Stockfish's move, then reprobe the line back to its root"""
        # a target that is found again after exploring it is explored one depth deeper
        depth = self.explored[target["epd"]] = (
            self.explored.get(target["epd"], self.args.exploreDepth - 1) + 1
        )
        epd, _, moves = target["epd"].partition(" moves ")
        leaf = chess.Board(epd, chess960=self.args.chess960)
        for m in moves.split():
            leaf.push(leaf.parse_uci(m))
        parent = leaf.copy()
        move = parent.uci(parent.pop())
        before = await self.cdb_view(parent, move)
        start = time.time()

        out = StringIO()
        with contextlib.redirect_stdout(out):
            await cdbsearch.cdbsearch(
                epd=target["epd"],
                chess960=self.args.chess960,
                depthLimit=depth,
                timeLimit=None,
                concurrency=self.args.concurrency,
                evalDecay=self.args.evalDecay,
                user=self.args.user,
                suppressErrors=self.args.suppressErrors,
            )
        fields = {}
        for l in out.getvalue().splitlines():
            key, _, value = l.partition(":")
            fields[key.strip()] = value.strip()
        print(
            f"  explored to depth {depth} {target['line']} -> {target['move']}: score {fields.get('score')}, PV {' '.join(fields.get('PV', '').split()[:6])} ..., {fields.get('total time')}",
            flush=True,
        )
        # cdb updates the scores of a position when it is queried: walk the line back to its root, leaf first
        board = leaf.copy()
        chessdb = self.chessdb(board)
        while True:
            await chessdb.queryall(board.epd(), board.chess960, skipTT=True)
            if not board.move_stack:
                break
            board.pop()

        after = await self.cdb_view(parent, move)
        self.exploredAt.add(parent.epd())
        print(
            f"    cdb before: best {before['best']} {before['bestScore']}, {move} {before['score']};"
            f" after: best {after['best']} {after['bestScore']}, {move} {after['score']}",
            flush=True,
        )
        self.log(
            "explore",
            line=target["line"],
            move=move,
            depth=depth,
            before=before,
            after=after,
            seconds=round(time.time() - start),
            enqueued=int(fields.get("enqueued") or 0),
            positions=int((fields.get("chessdbq") or "0").split()[0]),
        )

    async def report(self, root):
        """show cdb's current view of root, and of the move leading to it"""
        chessdb = self.chessdb(root)
        moves = await self.cdb_moves(chessdb, root)
        top = sorted(moves.items(), key=lambda t: -t[1])[:5]
        print(
            "cdb at the root:",
            ", ".join(f"{root.san(root.parse_uci(m))} {s:+}" for m, s in top),
        )
        if root.move_stack:
            parent = root.copy()
            last = parent.pop()
            moves = await self.cdb_moves(chessdb, parent)
            score = moves.get(parent.uci(last))
            print(f"cdb for {parent.san(last)} at its parent: {score:+}")
        print(flush=True)


async def main(args, root):
    options = {"Threads": args.threads, "Hash": args.hash}
    if args.chess960:
        options["UCI_Chess960"] = True
    if args.live:
        engine, live = None, LiveEngine()
        await live.start(args.engine, options)
        name, budget = (
            live.name,
            f"a background search of at least {args.prefillNodes} nodes",
        )
    else:
        _, engine = await chess.engine.popen_uci(args.engine)
        await engine.configure(options)
        name = engine.id.get("name")
        budget = f"{args.nodes} nodes per {'search' if args.pv else 'position'}"
    print(
        f"Engine       :  {name}, {args.threads} threads, {args.hash} MB hash, {budget}"
    )
    print(f"Root position:  {root.epd()}")
    print(f"Start date   :  {datetime.now().isoformat()}\n", flush=True)

    guide = SFGuide(engine, args)
    guide.log("start", root=root.epd(), engine=name, args=vars(args))
    try:
        await run(args, root, guide, engine, live if args.live else None)
    finally:
        guide.log("stop")
        if args.live and live.proc.returncode is None:
            live.send("stop")
            live.send("quit")


async def run(args, root, guide, engine, live):
    """the rounds of walking and exploring"""

    async def safely(f, *a):
        # in long runs, an error in one exploration or walk should not end the run
        try:
            return await f(*a)
        except Exception as ex:
            if args.live and live.reader.done():
                raise
            print(f"  error: {ex!r}", flush=True)
            guide.log("error", message=repr(ex))

    if args.live:
        # one search from the root keeps running, and keeps filling the TT that we probe
        guide.live = live
        live.position(root)
        live.send("go infinite")
        last = 0
        while live.nodes() < args.prefillNodes:
            await asyncio.sleep(5)
            if datetime.now().timestamp() - last >= 60:
                print(f"  background search: {live.status()}", flush=True)
                last = datetime.now().timestamp()
        print(flush=True)
    if args.refute:
        await refute_rounds(args, root, guide, live, safely)
    else:
        await explore_rounds(args, root, guide, live, safely)

    # give cdbsearch's background requests (reprobes and requeues) a chance to finish
    pending = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and not (args.live and t is live.reader)
    ]
    if pending:
        await asyncio.wait(pending, timeout=120)
    if args.live:
        await live.request("stop", lambda l: l.startswith("bestmove"))
        live.send("quit")
        await live.proc.wait()
    else:
        await engine.quit()


async def refute_rounds(args, root, guide, live, safely):
    """walk the background search's PV, refuting what cdb overvalues, round after round"""
    guide.root = root
    live.send(f"auxthreads {args.auxThreads}")
    rounds = 0
    while args.rounds <= 0 or rounds < args.rounds:
        rounds += 1
        guide.round = rounds
        print(
            f"Round {rounds}, {datetime.now().isoformat()}, background search: {live.status()}",
            flush=True,
        )
        await safely(guide.report, root)
        guide.log("round", background=live.background(), root=await guide.cdb_now(root))
        await safely(guide.refute, root)
        guide.log("roundEnd", root=await guide.cdb_now(root))
        print(flush=True)


async def explore_rounds(args, root, guide, live, safely):
    """walk and explore the targets, round after round"""
    walk = guide.live_walk if args.live else guide.pv_walk if args.pv else guide.walk
    await guide.report(root)
    targets = await walk(root)
    rounds = 0
    while args.rounds <= 0 or rounds < args.rounds:
        rounds += 1
        guide.round = rounds
        if not args.exploreDepth:
            break
        if not targets:
            if args.rounds > 0 or not args.live:
                break
            # a run without end: the background search goes on, and so may cdb's view change
            print("No targets: walking again in 10 minutes.", flush=True)
            await asyncio.sleep(600)
            targets = await safely(walk, root) or []
            continue
        # The most important targets are chosen, and then explored deepest first: cdb updates the
        # scores of a position when it is queried, so fixing a line from the bottom up lets the
        # corrections of the deeper positions reach the ones above them.
        chosen = sorted(targets[: args.maxTargets], key=lambda t: -t["ply"])
        print(f"Exploring {len(chosen)} targets, deepest first.", flush=True)
        for target in chosen:
            await safely(guide.explore, target)
        print(flush=True)
        await safely(guide.report, root)
        targets = await safely(guide.pv_check if args.pv else walk, root) or []


if __name__ == "__main__":
    freeze_support()
    argParser = argparse.ArgumentParser(
        description="Use Stockfish to find the positions where cdb's main lines are likely wrong, and explore those with cdbsearch.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    group = argParser.add_mutually_exclusive_group()
    group.add_argument(
        "--epd",
        help="""EPD/FEN to start from: acceptable are FENs w/ and w/o move counters, as well as the extended "['startpos'|FEN] moves m1 m2 m3" syntax.""",
        default="startpos",
    )
    group.add_argument(
        "--san",
        help='Moves in SAN notation that lead to the position to start from. E.g. "1. e4".',
    )
    argParser.add_argument(
        "--chess960",
        action="store_true",
        help="Enable chess960.",
    )
    argParser.add_argument(
        "--engine",
        help="Path to the Stockfish (or any UCI engine) binary.",
        default="stockfish",
    )
    argParser.add_argument(
        "--threads",
        help="Number of threads for the engine.",
        type=int,
        default=4,
    )
    argParser.add_argument(
        "--hash",
        help="Hash size in MB for the engine.",
        type=int,
        default=1024,
    )
    argParser.add_argument(
        "--nodes",
        help="Number of nodes the engine searches per position (three times where it disagrees with cdb), or per search with --pv.",
        type=int,
        default=10000000,
    )
    argParser.add_argument(
        "--pv",
        action="store_true",
        help="Follow the main lines of long engine searches, instead of searching every position on cdb's main line. Suited to searches with many nodes.",
    )
    argParser.add_argument(
        "--maxSearches",
        help="With --pv: the number of engine searches. The first one is from the root, the others refute cdb's choices wherever they differ from the engine's main lines, closest to the root first.",
        type=int,
        default=3,
    )
    argParser.add_argument(
        "--live",
        action="store_true",
        help="Keep one engine search running from the root, and compare cdb's choices with the TT of that search, probed while it runs. Needs the ttprobe and ttmoves commands of the cdb-tt branch of Stockfish.",
    )
    argParser.add_argument(
        "--refute",
        action="store_true",
        help="With --live: prove the background search's line to cdb for the side to move at the root: repair, from the bottom up with short searches on the auxiliary threads near the leaves, the end of the line and cdb's lines after the opponent's moves that cdb scores better than the engine's, instead of exploring targets with cdbsearch.",
    )
    argParser.add_argument(
        "--pvPlies",
        help="With --refute: the length of the background search's PV to walk.",
        type=int,
        default=100,
    )
    argParser.add_argument(
        "--leafNodes",
        help="With --refute: the nodes of the short searches near the leaves, which decide.",
        type=int,
        default=50000000,
    )
    argParser.add_argument(
        "--scanPlies",
        help="With --refute: check at most this many positions of a line from its leaf upward, at each look at it, besides those that agreed before.",
        type=int,
        default=20,
    )
    argParser.add_argument(
        "--repairs",
        help="With --refute: look at a line this many times, each time fixing its deepest disagreement.",
        type=int,
        default=3,
    )
    argParser.add_argument(
        "--repairBudget",
        help="With --refute: the short searches available for repairing a line.",
        type=int,
        default=60,
    )
    argParser.add_argument(
        "--tol",
        help="With --refute: cdb's score of a position agrees with a short search if they are at most this many cp apart.",
        type=int,
        default=15,
    )
    argParser.add_argument(
        "--margin",
        help="With --refute: a move of the opponent is a defence against the engine's line if cdb scores it more than this many cp better for the opponent than the engine's move.",
        type=int,
        default=5,
    )
    argParser.add_argument(
        "--maxMarks",
        help="With --refute: repair at most this many defences against the engine's line per round.",
        type=int,
        default=6,
    )
    argParser.add_argument(
        "--auxThreads",
        help="With --refute: the number of threads of the auxiliary searches.",
        type=int,
        default=3,
    )
    argParser.add_argument(
        "--prefillNodes",
        help="With --live: the number of nodes the background search makes before the first walk.",
        type=int,
        default=1000000000,
    )
    argParser.add_argument(
        "--minDepth",
        help="With --live: only trust TT entries of at least this depth.",
        type=int,
        default=25,
    )
    argParser.add_argument(
        "--auxNodes",
        help="With --live: where the TT has no deep entry for a position, find the engine's move with a search of this many nodes on an auxiliary thread, which shares the TT with the background search. With 0, such positions are skipped.",
        type=int,
        default=50000000,
    )
    argParser.add_argument(
        "--scoreGap",
        action="store_true",
        help="With --live: where the engine's move differs from cdb's, score both with --auxNodes searches on the auxiliary thread, and only explore if the gap is at least --threshold.",
    )
    argParser.add_argument(
        "--threshold",
        help="Minimal advantage in cp, according to the engine, of its move over cdb's best move for a position to become a target. Not used with --pv, and with --live only with --scoreGap.",
        type=int,
        default=25,
    )
    argParser.add_argument(
        "--side",
        help="Only look for better moves of this side.",
        choices=["both", "white", "black"],
        default="both",
    )
    argParser.add_argument(
        "--maxPly",
        help="Walk at most this many plies away from the root.",
        type=int,
        default=16,
    )
    argParser.add_argument(
        "--maxNodes",
        help="Analyse at most this many positions per walk. Not used with --pv.",
        type=int,
        default=60,
    )
    argParser.add_argument(
        "--targets",
        help="Write the targets of the last walk to this file, in the extended EPD syntax understood by cdbbulksearch.",
    )
    argParser.add_argument(
        "--exploreDepth",
        help="Explore every target with cdbsearch to this depth. With 0 we only walk and report.",
        type=int,
        default=0,
    )
    argParser.add_argument(
        "--maxTargets",
        help="Explore at most this many targets per round: the ones with the largest gap (with --pv and --live: closest to the root). They are explored deepest first, so that cdb's evals get fixed from the bottom up.",
        type=int,
        default=8,
    )
    argParser.add_argument(
        "--rounds",
        help="Number of rounds of exploring the targets, each followed by a new walk (with --pv: by comparing cdb with the earlier engine lines again). With 0, keep going until stopped. A target found again is explored one depth deeper.",
        type=int,
        default=1,
    )
    argParser.add_argument(
        "--stats",
        help="Append statistics of the walks and explorations to this file (JSON lines), see cdbsfguide_stats.py.",
    )
    argParser.add_argument(
        "--holdout",
        help="With --live: the fraction of the first walk's disagreements without other ones below them (leaves) that is never explored, as a control group for --stats.",
        type=float,
        default=0.0,
    )
    argParser.add_argument(
        "--holdoutPly",
        help="With --holdout: only disagreements at least this many plies from the root are held out. Positions below them are not explored either.",
        type=int,
        default=4,
    )
    argParser.add_argument(
        "--seed",
        help="Seed for choosing the held out disagreements.",
        type=int,
        default=1,
    )
    argParser.add_argument(
        "--concurrency",
        help="Argument passed to cdbsearch.",
        type=int,
        default=16,
    )
    argParser.add_argument(
        "--evalDecay",
        help="Argument passed to cdbsearch.",
        type=int,
        default=2,
    )
    argParser.add_argument(
        "--user",
        help="Argument passed to cdbsearch.",
    )
    argParser.add_argument(
        "--suppressErrors",
        action="store_true",
        help="Argument passed to cdbsearch.",
    )
    args = argParser.parse_args()
    if args.refute and not args.live:
        argParser.error("--refute needs --live")

    if args.san is not None:
        game = chess.pgn.read_game(StringIO(args.san))
        root = game.board()
        for move in game.mainline_moves():
            root.push(move)
    else:
        epd, _, moves = args.epd.replace("startpos", chess.STARTING_FEN).partition(
            "moves"
        )
        root = chess.Board(epd.strip(), chess960=args.chess960)
        for m in moves.split():
            root.push(root.parse_uci(m))

    asyncio.run(main(args, root))
