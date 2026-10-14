import argparse, asyncio, contextlib
import chess, chess.engine, chess.pgn
import cdbsearch
from io import StringIO
from datetime import datetime, timedelta
from multiprocessing import freeze_support


class SFGuide:
    """use Stockfish to find where cdb's main lines are likely wrong, and let cdbsearch explore there"""

    def __init__(self, engine, args):
        self.engine = engine
        self.args = args

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
        return {
            "why": why,
            "ply": len(board.move_stack),
            "line": board.root().variation_san(board.move_stack) or "(root)",
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

    async def explore(self, target):
        """let cdbsearch explore the position after Stockfish's move, then reprobe the line back to its root"""
        out = StringIO()
        with contextlib.redirect_stdout(out):
            await cdbsearch.cdbsearch(
                epd=target["epd"],
                chess960=self.args.chess960,
                depthLimit=self.args.exploreDepth,
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
            f"  explored {target['line']} -> {target['move']}: score {fields.get('score')}, PV {' '.join(fields.get('PV', '').split()[:6])} ..., {fields.get('total time')}",
            flush=True,
        )
        # cdb updates the scores of a position when it is queried: walk the line back to its root, leaf first
        epd, _, moves = target["epd"].partition(" moves ")
        board = chess.Board(epd, chess960=self.args.chess960)
        for m in moves.split():
            board.push(board.parse_uci(m))
        chessdb = self.chessdb(board)
        while True:
            await chessdb.queryall(board.epd(), board.chess960, skipTT=True)
            if not board.move_stack:
                break
            board.pop()

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
    _, engine = await chess.engine.popen_uci(args.engine)
    options = {"Threads": args.threads, "Hash": args.hash}
    if args.chess960:
        options["UCI_Chess960"] = True
    await engine.configure(options)
    print(
        f"Engine       :  {engine.id.get('name')}, {args.threads} threads, {args.hash} MB hash,"
        f" {args.nodes} nodes per {'search' if args.pv else 'position'}"
    )
    print(f"Root position:  {root.epd()}")
    print(f"Start date   :  {datetime.now().isoformat()}\n", flush=True)

    guide = SFGuide(engine, args)
    await guide.report(root)
    targets = await (guide.pv_walk(root) if args.pv else guide.walk(root))
    for _ in range(args.rounds):
        if not args.exploreDepth or not targets:
            break
        print(f"Exploring {min(len(targets), args.maxTargets)} targets.", flush=True)
        for target in targets[: args.maxTargets]:
            await guide.explore(target)
        print(flush=True)
        await guide.report(root)
        targets = await (guide.pv_check(root) if args.pv else guide.walk(root))

    # give cdbsearch's background requests (reprobes and requeues) a chance to finish
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.wait(pending, timeout=120)
    await engine.quit()


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
        "--threshold",
        help="Minimal advantage in cp, according to the engine, of its move over cdb's best move for a position to become a target. Not used with --pv.",
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
        help="Explore at most this many targets per round, the ones with the largest gap (with --pv: closest to the root) first.",
        type=int,
        default=8,
    )
    argParser.add_argument(
        "--rounds",
        help="Number of rounds of exploring the targets, each followed by a new walk (with --pv: by comparing cdb with the earlier engine lines again).",
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
