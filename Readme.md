# Explore and extend the Chess Cloud Database 

Explores and searches [chessdb.cn](https://chessdb.cn/queryc_en/), the largest online database (db) of chess positions and openings.

## Purpose

Build a search tree from a particular position, using a mini-max like algorithm,
finding the best line, extending the db as needed.

Using some concurrency, fairly deep exploration is quickly possible.

## `cdbsearch`

This is a command line program to explore a single position.

```
usage: cdbsearch.py [-h] [--epd EPD | --san SAN] [--chess960] [--depthLimit DEPTHLIMIT] [--timeLimit TIMELIMIT] [--concurrency CONCURRENCY] [--evalDecay EVALDECAY] [--cursedWins] [--TBsearch] [--proveMates] [--user USER] [--suppressErrors]

Explore and extend the Chess Cloud Database (https://chessdb.cn/queryc_en/). Builds a search tree for a given position.

options:
  -h, --help            show this help message and exit
  --epd EPD             EPD/FEN to explore: acceptable are FENs w/ and w/o move counters, as well as the extended "['startpos'|FEN] moves m1 m2 m3" syntax. (default: startpos moves g2g4)
  --san SAN             Moves in SAN notation that lead to the position to be explored. E.g. "1. g4". (default: None)
  --chess960            Enable chess960. (default: False)
  --depthLimit DEPTHLIMIT
                        Finish the exploration at the specified depth. (default: None)
  --timeLimit TIMELIMIT
                        Do not start a search at higher depth if this limit (in seconds) is exceeded. (default: None)
  --concurrency CONCURRENCY
                        Concurrency of requests. This is the maximum number of requests made to chessdb at the same time. (default: 16)
  --evalDecay EVALDECAY
                        Depth decrease per cp eval-to-best. A small number will use a very narrow search, 0 will essentially just follow PV lines. A wide search will likely enqueue many positions. (default: 2)
  --cursedWins          Treat cursed wins as wins. (default: False)
  --TBsearch            Extend the searching and exploration of lines into cdb's EGTB. (default: False)
  --proveMates          Attempt to prove that mate PV lines have no better defence. Proven mates are indicated with "CHECKMATE" at the end of the PV, whereas unproven ones use "checkmate". (default: False)
  --user USER           Add this username to the http user-agent header. (default: None)
  --suppressErrors      Suppress any error messages resulting from the API calls. (default: False)
``` 

Sample output:

```
Search at depth  8
  position  :  rnbqkbnr/pppppppp/8/8/6P1/8/PPPPPP1P/RNBQKBNR b KQkq -
  cdb PV len:  39
  score     :  174
  PV        :  d7d5 c2c4 c8g4 d1b3 e7e6 h2h3 g4f5 c4d5 d8d5 b3d5 e6d5 b1c3 c7c6 g1f3 f8d6 f3d4 f5g6 h3h4 g8f6 f1h3
  PV len    :  20
  level     :  25
  max level :  39
  queryall  :  1409
  bf        :  2.48
  chessdbq  :  713 (50.60% of queryall)
  enqueued  :  8
  requeued  :  0
  unscored  :  0 (0.00% of enqueued)
  reprobed  :  113 (15.85% of chessdbq)
  inflightQ :  38.71
  inflightR :  11.15
  cdb time  :  22
  date      :  2026-01-05T21:05:02.452997
  total time:  0:00:16.24
  URL       :
https://chessdb.cn/queryc_en/?rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR_w_KQkq_-_moves_g2g4_d7d5_c2c4_c8g4_d1b3_e7e6_h2h3_g4f5_c4d5_d8d5_b3d5_e6d5_b1c3_c7c6_g1f3_f8d6_f3d4_f5g6_h3h4_g8f6_f1h3
```

Meaning of the fields:

```
cdb PV len : Current length of the PV for this position in cdb in plies.
score      : The standard minimax score found, not using the decay that cdb implements.
PV         : Best line found.
PV len     : Length of this PV line in plies.
level      : Deepest level in search tree reached in the last completed search.
max level  : Deepest level in search tree reached in all the searches so far.
queryall   : Number of positions visited in the search tree, with results provided by cdb or the local cache.
bf         : Branching factor q^(1/d) computed from queryall q and depth d.
chessdbq   : Number of positions requested to cdb.
enqueued   : Number of positions that did not exist within cdb but have been added as part of the search.
requeued   : Number of positions that were re-queued to prompt cdb to provide at least 5 scored moves.
unscored   : Number of existing unscored moves within cdb that were assigned a score as part of the search.
reprobed   : Number of positions in local PV lines re-requested to cdb.
inflightQ  : Number of concurrent queries, guaranteed to return scored moves, made to cdb on average.
inflightR  : Number of concurrent http requests made to chessdb.cn on average.
cdb time   : Average time (in milliseconds) needed to get a cdb list of moves for a position (including those that required enqueuing).
date       : ... you guessed it
total time : Time spent since the start of the search.
URL        : Link displaying the found PV on chessdb.cn.
```

Sample output with `--proveMates`:

```
> python cdbsearch.py --epd "3r4/3N2kr/1p6/pBpn1p2/Q2PR1p1/P7/1P4P1/2q3K1 w - -" --depthLimit 1 --evalDecay 0 --proveMates
Root position:  3r4/3N2kr/1p6/pBpn1p2/Q2PR1p1/P7/1P4P1/2q3K1 w - -
evalDecay    :  0
Concurrency  :  16
Prove Mates  :  True
Starting date:  2026-01-05T21:05:30.827169
Search at depth  1
  position  :  3r4/3N2kr/1p6/pBpn1p2/Q2PR1p1/P7/1P4P1/2q3K1 w - -
  cdb PV len:  10
  score     :  -29990
  PV        :  g1f2 f5e4 a4b3 h7h1 b3c3 d5c3 b5f1 c1f4 f2e1 f4e3 CHECKMATE (#-5)
  PV len    :  10
  level     :  10
  max level :  10
  queryall  :  3332
  bf        :  3332.00
  chessdbq  :  1965 (58.97% of queryall)
  enqueued  :  0
  requeued  :  0
  unscored  :  0 (0.00% of enqueued)
  reprobed  :  10 (0.51% of chessdbq)
  inflightQ :  15.87
  inflightR :  5.91
  cdb time  :  61
  date      :  2026-01-05T21:07:30.809123
  total time:  0:01:59.98
  URL       :  https://chessdb.cn/queryc_en/?3r4/3N2kr/1p6/pBpn1p2/Q2PR1p1/P7/1P4P1/2q3K1_w_-_-_moves_g1f2_f5e4_a4b3_h7h1_b3c3_d5c3_b5f1_c1f4_f2e1_f4e3
```

## `cdbbulksearch`

This is a command line program to sequentially explore several positions.

```
usage: cdbbulksearch.py [-h] [--excludeFile EXCLUDEFILE] [--chess960] [--plyBegin PLYBEGIN] [--plyEnd PLYEND] [--shuffle] [--depthLimit DEPTHLIMIT] [--timeLimit TIMELIMIT] [--concurrency CONCURRENCY] [--evalDecay EVALDECAY] [--cursedWins] [--TBsearch] [--proveMates] [--user USER] [--suppressErrors] [--bulkConcurrency BULKCONCURRENCY] [--forever] [--maxDepthLimit MAXDEPTHLIMIT] [--reload] filename

Invoke cdbsearch for positions loaded from a file.

positional arguments:
  filename              PGN file if suffix is .pgn(.gz), o/w a file with FENs/EPDs. The latter may use the extended "['startpos'|FEN] moves m1 m2 m3" syntax.

options:
  -h, --help            show this help message and exit
  --excludeFile EXCLUDEFILE
                        A file with FENs/EPDs that should not be loaded from filename. (default: None)
  --chess960            Enable chess960. (default: False)
  --plyBegin PLYBEGIN   Ply in each line of filename from which positions will be searched by cdbsearch. A value of 0 corresponds to the starting FEN without any moves played. Negative values count from the back, as per the Python standard. (default: -1)
  --plyEnd PLYEND       Ply in each line of filename until which positions will be searched by cdbsearch. A value of None means including the final move of the line. (default: None)
  --shuffle             Shuffle the positions to be searched randomly. (default: False)
  --depthLimit DEPTHLIMIT
                        Argument passed to cdbsearch. (default: 5)
  --timeLimit TIMELIMIT
                        Argument passed to cdbsearch. (default: None)
  --concurrency CONCURRENCY
                        Argument passed to cdbsearch. (default: 16)
  --evalDecay EVALDECAY
                        Argument passed to cdbsearch. (default: 2)
  --cursedWins          Argument passed to cdbsearch. (default: False)
  --TBsearch            Argument passed to cdbsearch. (default: False)
  --proveMates          Argument passed to cdbsearch. (default: False)
  --user USER           Argument passed to cdbsearch. (default: None)
  --suppressErrors      Argument passed to cdbsearch. (default: False)
  --bulkConcurrency BULKCONCURRENCY
                        Number of concurrent processes running cdbsearch. (default: 4)
  --forever             Pass positions from filename to cdbsearch in an infinite loop, increasing depthLimit by one after each completed cycle. (default: False)
  --maxDepthLimit MAXDEPTHLIMIT
                        Upper bound for dynamically increasing depthLimit. (default: None)
  --reload              Reload positions from filename when tasks for new cycle are needed. (default: False)
```

Example:
```shell
echo -e '[Event "*"]\n\n1. g4 *\n\n1. g4 d5 *\n' > book.pgn
git clone https://github.com/vondele/cdbexplore && pip install -r cdbexplore/requirements.txt
python cdbexplore/cdbbulksearch.py book.pgn --forever >& cdbsearch_book.log &
```

## `cdbsfguide`

This is a command line program that uses Stockfish (or any UCI engine) to find the positions where cdb's main lines are likely wrong, and lets `cdbsearch` explore those positions.
After each exploration, the line is queried once more from its leaf back to its root, so that cdb's scores along it get updated.

It has four modes:

* By default, it walks cdb's main line from the root, and asks the engine at every position whether it would play another move.
  Where the engine prefers its own move by at least `--threshold` cp, the position after the engine's move becomes a target, and the walk follows the engine's line as well.
  Both moves are scored by the engine, each with a search restricted to that move, as cdb's scale differs from the engine's.
  This suits short searches: the program reports by how much the engine sometimes preferred cdb's move to its own, which shows what size of gap is just noise.
* With `--pv`, it compares cdb's choices with the main line of a long engine search, position by position.
  Where they differ, the position after the engine's move becomes a target, and a further search from the position after cdb's move shows how the engine would refute it (up to `--maxSearches` searches, closest to the root first).
  This suits searches with billions of nodes.
* With `--live`, one engine search keeps running from the root for as long as the program runs, and keeps deepening its TT.
  The walk along cdb's main line reads the engine's move at each position from that TT, while the search runs; where the TT has no deep entry, a search on an auxiliary thread, which shares only the TT with the background search, finds the engine's move (`--auxNodes`).
  Every position where the engine's move differs from cdb's becomes a target: cdb's own search settles the evals.
  With `--scoreGap`, both moves are first scored by auxiliary searches, and only gaps of at least `--threshold` cp are explored.
  With `--rounds 0`, walking and exploring go on until stopped, and a target that is found again is explored one depth deeper.
  This needs the `ttprobe`, `ttmoves` and `aux*` commands of the [`cdb-tt`](https://github.com/xu-shawn/Stockfish/tree/cdb-tt) branch of Stockfish.
* With `--live --refute`, it fixes cdb's evals from the bottom up itself, with short searches near the leaves, instead of letting `cdbsearch` explore.
  It proves the line of the background search to cdb, for the side to move at the root: it walks the PV of the background search (`--pvPlies`), as far as its TT entries have a depth of at least `--minDepth`, and adds its positions to cdb.
  At the opponent's positions on the line, every move that cdb scores more than `--margin` cp better for the opponent than the engine's move is a defence against the line, which caps cdb's score of it.
  Each round, it repairs the end of the line, and cdb's lines after up to `--maxMarks` defences: those repaired the fewest times so far, the ones closest to the root first, as the shallowest defence on cdb's path caps its score of the line, and among those the largest gaps.
  Where cdb scores another move of the side to move at the root above the engine's, nothing needs doing: the line only has to be proven good, and a move cdb wrongly prefers to it does not lower cdb's score of it.
  To repair a line, it follows cdb's PV to the leaf of cdb's tree, and checks its positions upward with short searches (`--leafNodes`) on the auxiliary threads (`--auxThreads`), of the positions as cdb sees them, without the moves that led to them.
  At the deepest position where cdb's value differs from the engine's by more than `--tol` cp, unless both see the same side 400 cp or more ahead, it adds the engine's line to cdb, and if cdb is too optimistic for the side to move and plays another move, the engine's refutation of that move; it queries the lines from their ends back up to the root, and extends them from their ends with further short searches, as long as that brings cdb's value closer to the engine's.
  When two additions in a row do not, cdb's own evals of the positions on these lines differ from the engine's, and more positions would not change that.
  Positions that agree, or that could not be brought closer, are skipped until cdb's score of them changes, so that every new look at the line (`--repairs`) reaches further up, while its share of the `--repairBudget` searches lasts.
  The engine's values far from the leaves never decide: they miss what only long lines show, and underestimate advantages that only show deep in the endgame.
  Adding positions to cdb does not make its minimax worse, so where a short search is wrong, cdb's own scores of the added positions settle it.

In each round, the targets that are explored (`--maxTargets`) are explored deepest first, and every exploration ends by querying its line from the leaf back to the root.
As cdb updates the scores of a position when it is queried, this fixes cdb's evals from the bottom up.

To measure the effect of a run, `--stats FILE` appends statistics to `FILE` (JSON lines): at every walk, cdb's scores of the root moves and how often cdb agrees with the engine; for every exploration, cdb's best move and its score of the engine's move, before and after.
With `--refute`: at every round, cdb's scores of the root moves; for every repaired defence, cdb's scores of it and of the engine's move, before and after, and the number of short searches.
The disagreements of the first walk serve as a fixed set: every walk records whether cdb now plays the engine's move there.
With `--holdout`, a random part of them (at least `--holdoutPly` plies from the root) is never explored, nor are the lines below them, as a control group: comparing the explored disagreements with the held out ones tells the effect of the run from cdb's own drift.
`cdbsfguide_stats.py FILE --plot FILE.png` summarises such a file.

To change the program during a long run without losing the background search and its TT, run the engine under `uciserver.py`, which serves it on a Unix socket to one client at a time and keeps it running when the client ends, and give `cdbsfguide.py` the socket with `--attach` instead of `--engine`.
A later run from the same root, maybe with new code, attaches to the running search; the engine stops with the server.

Example of a run without end that repairs lines itself, for the start position in which only White may castle:
```shell
python uciserver.py --engine Stockfish/src/stockfish --socket sf.sock >& uciserver.log &
python cdbsfguide.py --live --refute --attach sf.sock --epd "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQ - 0 1" --threads 5 --hash 32768 --auxThreads 3 --rounds 0 --stats kq.jsonl >& kq.log &
```

Example of a run without end that explores with `cdbsearch`, with a control group:
```shell
python cdbsfguide.py --live --epd "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQ - 0 1" --engine Stockfish/src/stockfish --threads 5 --hash 32768 --minDepth 30 --exploreDepth 6 --rounds 0 --holdout 0.3 --stats kq.jsonl >& kq.log &
python cdbsfguide_stats.py kq.jsonl --plot kq.png
```

```
usage: cdbsfguide.py [-h] [--epd EPD | --san SAN] [--chess960] [--engine ENGINE] [--threads THREADS] [--hash HASH] [--nodes NODES] [--pv] [--maxSearches MAXSEARCHES] [--live] [--refute] [--pvPlies PVPLIES] [--leafNodes LEAFNODES] [--scanPlies SCANPLIES] [--repairs REPAIRS] [--repairBudget REPAIRBUDGET] [--tol TOL] [--margin MARGIN] [--maxMarks MAXMARKS] [--auxThreads AUXTHREADS] [--attach ATTACH] [--prefillNodes PREFILLNODES] [--minDepth MINDEPTH] [--auxNodes AUXNODES] [--scoreGap]
                     [--threshold THRESHOLD] [--side {both,white,black}] [--maxPly MAXPLY] [--maxNodes MAXNODES] [--targets TARGETS] [--exploreDepth EXPLOREDEPTH] [--maxTargets MAXTARGETS] [--rounds ROUNDS] [--stats STATS] [--holdout HOLDOUT] [--holdoutPly HOLDOUTPLY] [--seed SEED] [--concurrency CONCURRENCY] [--evalDecay EVALDECAY] [--user USER] [--suppressErrors]

Use Stockfish to find the positions where cdb's main lines are likely wrong, and explore those with cdbsearch.

options:
  -h, --help            show this help message and exit
  --epd EPD             EPD/FEN to start from: acceptable are FENs w/ and w/o move counters, as well as the extended "['startpos'|FEN] moves m1 m2 m3" syntax. (default: startpos)
  --san SAN             Moves in SAN notation that lead to the position to start from. E.g. "1. e4". (default: None)
  --chess960            Enable chess960. (default: False)
  --engine ENGINE       Path to the Stockfish (or any UCI engine) binary. (default: stockfish)
  --threads THREADS     Number of threads for the engine. (default: 4)
  --hash HASH           Hash size in MB for the engine. (default: 1024)
  --nodes NODES         Number of nodes the engine searches per position (three times where it disagrees with cdb), or per search with --pv. (default: 10000000)
  --pv                  Follow the main lines of long engine searches, instead of searching every position on cdb's main line. Suited to searches with many nodes. (default: False)
  --maxSearches MAXSEARCHES
                        With --pv: the number of engine searches. The first one is from the root, the others refute cdb's choices wherever they differ from the engine's main lines, closest to the root first. (default: 3)
  --live                Keep one engine search running from the root, and compare cdb's choices with the TT of that search, probed while it runs. Needs the ttprobe and ttmoves commands of the cdb-tt branch of Stockfish. (default: False)
  --refute              With --live: prove the background search's line to cdb for the side to move at the root: repair, from the bottom up with short searches on the auxiliary threads near the leaves, the end of the line and cdb's lines after the opponent's moves that cdb scores better than the engine's, instead of exploring targets with cdbsearch. (default: False)
  --pvPlies PVPLIES     With --refute: the length of the background search's PV to walk. (default: 100)
  --leafNodes LEAFNODES
                        With --refute: the nodes of the short searches near the leaves, which decide. (default: 50000000)
  --scanPlies SCANPLIES
                        With --refute: check at most this many positions of a line from its leaf upward, at each look at it, besides those that agreed before. (default: 20)
  --repairs REPAIRS     With --refute: look at a line this many times, each time fixing its deepest disagreement. (default: 3)
  --repairBudget REPAIRBUDGET
                        With --refute: the short searches available for repairing a line. (default: 60)
  --tol TOL             With --refute: cdb's score of a position agrees with a short search if they are at most this many cp apart. (default: 15)
  --margin MARGIN       With --refute: a move of the opponent is a defence against the engine's line if cdb scores it more than this many cp better for the opponent than the engine's move. (default: 5)
  --maxMarks MAXMARKS   With --refute: repair at most this many defences against the engine's line per round. (default: 6)
  --auxThreads AUXTHREADS
                        With --refute: the number of threads of the auxiliary searches. (default: 3)
  --attach ATTACH       With --live: use the engine of a uciserver.py listening on this socket, instead of starting one. Its search goes on when this program ends, so that a later run, maybe with new code, can attach to it again and keep its TT. --engine is then not used, and --threads and --hash only if the engine is not searching yet. (default: None)
  --prefillNodes PREFILLNODES
                        With --live: the number of nodes the background search makes before the first walk. (default: 1000000000)
  --minDepth MINDEPTH   With --live: only trust TT entries of at least this depth. (default: 25)
  --auxNodes AUXNODES   With --live: where the TT has no deep entry for a position, find the engine's move with a search of this many nodes on an auxiliary thread, which shares the TT with the background search. With 0, such positions are skipped. (default: 50000000)
  --scoreGap            With --live: where the engine's move differs from cdb's, score both with --auxNodes searches on the auxiliary thread, and only explore if the gap is at least --threshold. (default: False)
  --threshold THRESHOLD
                        Minimal advantage in cp, according to the engine, of its move over cdb's best move for a position to become a target. Not used with --pv, and with --live only with --scoreGap. (default: 25)
  --side {both,white,black}
                        Only look for better moves of this side. (default: both)
  --maxPly MAXPLY       Walk at most this many plies away from the root. (default: 16)
  --maxNodes MAXNODES   Analyse at most this many positions per walk. Not used with --pv. (default: 60)
  --targets TARGETS     Write the targets of the last walk to this file, in the extended EPD syntax understood by cdbbulksearch. (default: None)
  --exploreDepth EXPLOREDEPTH
                        Explore every target with cdbsearch to this depth. With 0 we only walk and report. (default: 0)
  --maxTargets MAXTARGETS
                        Explore at most this many targets per round: the ones with the largest gap (with --pv and --live: closest to the root). They are explored deepest first, so that cdb's evals get fixed from the bottom up. (default: 8)
  --rounds ROUNDS       Number of rounds of exploring the targets, each followed by a new walk (with --pv: by comparing cdb with the earlier engine lines again). With 0, keep going until stopped. A target found again is explored one depth deeper. (default: 1)
  --stats STATS         Append statistics of the walks and explorations to this file (JSON lines), see cdbsfguide_stats.py. (default: None)
  --holdout HOLDOUT     With --live: the fraction of the first walk's disagreements without other ones below them (leaves) that is never explored, as a control group for --stats. (default: 0.0)
  --holdoutPly HOLDOUTPLY
                        With --holdout: only disagreements at least this many plies from the root are held out. Positions below them are not explored either. (default: 4)
  --seed SEED           Seed for choosing the held out disagreements. (default: 1)
  --concurrency CONCURRENCY
                        Argument passed to cdbsearch. (default: 16)
  --evalDecay EVALDECAY
                        Argument passed to cdbsearch. (default: 2)
  --user USER           Argument passed to cdbsearch. (default: None)
  --suppressErrors      Argument passed to cdbsearch. (default: False)
```

Sample output (excerpt), for a walk after 1. e4 with 20M nodes per position and `--threshold 15`:

```
> python cdbsfguide.py --epd "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQ - 0 1 moves e2e4" --engine stockfish --threads 8 --hash 2048 --nodes 20000000 --threshold 15 --exploreDepth 6
cdb at the root: c6 -120, c5 -121, e5 -129, Nc6 -129, d6 -139
cdb for e4 at its parent: +120

  ply  0 B  cdb     c6  -120 | SF     c5 (cdb  -121)   -85 vs  -102, gap 17         TARGET | (root)
  ply  1 W  cdb     c4  +120 | SF     d4 (cdb  +100)   +87 vs  +100, gap -13               | 1...c6
  ply  1 W  cdb    Nf3  +121 | agree       SF   +96                                        | 1...c5
  ...
  ply  7 W  cdb   Bb5+  +120 | SF    Nc3 (cdb  +108)  +143 vs  +120, gap 23         TARGET | 1...c6 2. c4 d5 3. cxd5 cxd5 4. exd5 Nf6
  ...
Exploring 3 targets.
  explored 1...c6 2. c4 d5 3. cxd5 cxd5 4. exd5 Nf6 -> Nc3: score -103, PV f6d5 f1c4 d5b4 g1f3 b4d3 e1e2 ..., 0:03:07.48
  explored 1...c5 2. Nf3 Nc6 3. Bb5 d6 4. O-O Bd7 5. c3 Nf6 6. d4 Nxe4 7. Re1 Nf6 8. d5 Ne5 -> Bf1: score -107, PV e5f3 d1f3 e7e6 d5e6 f7e6 f3b7 ..., 0:02:43.27
  explored (root) -> c5: score 114, PV g1f3 b8c6 f1b5 d7d6 e1g1 c8d7 ..., 0:03:35.13

cdb at the root: c5 -111, c6 -120, e5 -129, Nc6 -129, d6 -139
cdb for e4 at its parent: +111
```

Scores are from the point of view of the side to move. For each position, the line shows cdb's best move and score, and either that the engine agrees, or the engine's move (with cdb's score for it), the engine's scores for its move and cdb's move, and the gap between the two.

[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
