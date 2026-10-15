"""
Summarise the statistics of a cdbsfguide.py run (--stats), to measure its effect on cdb:
cdb's scores at the root, its agreement with the engine along the walks, and what became of
the disagreements of the first walk, for those that were explored and those that were not.
For a run with --refute: cdb's scores at the root at every round, and what became of the
positions whose lines were repaired.
"""

import argparse, json
from datetime import datetime


def load_runs(path):
    """the records of the file, split into runs at their start records"""
    runs = []
    for line in open(path):
        if line.strip():
            r = json.loads(line)
            if r["event"] == "start" or not runs:
                runs.append([])
            runs[-1].append(r)
    return runs


def fraction(n, d):
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"


GROUPS = {
    "explored": lambda b: b["explored"],
    # the held out disagreements are leaves: compare them with the explored leaves
    "explored leaves": lambda b: b["explored"] and b.get("leaf", False),
    "held out": lambda b: b.get("heldOut", False),
    "not explored yet": lambda b: not b["explored"] and not b.get("heldOut", False),
}


def baseline_counts(walk, group):
    """resolved and total disagreements of the first walk in the group"""
    members = [b for b in walk["baseline"] if GROUPS[group](b)]
    return sum(b["resolved"] for b in members), len(members)


def main():
    parser = argparse.ArgumentParser(
        description="Summarise the statistics of a cdbsfguide.py run (--stats).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "stats", help="The statistics file written by cdbsfguide.py --stats."
    )
    parser.add_argument(
        "--run",
        type=int,
        default=-1,
        help="Which run of the file to summarise, by index.",
    )
    parser.add_argument(
        "--moves",
        help="Comma separated root moves to show, e.g. e2e4,d2d4. By default the top three of the first walk.",
    )
    parser.add_argument(
        "--plot", help="Also plot the run to this file (needs matplotlib)."
    )
    args = parser.parse_args()

    runs = load_runs(args.stats)
    run = runs[args.run]
    start = run[0]
    walks = [r for r in run if r["event"] == "walk"]
    explores = [r for r in run if r["event"] == "explore"]
    errors = [r for r in run if r["event"] == "error"]
    print(f"{len(runs)} run(s) in {args.stats}; this one started {start['time']}")
    if start["event"] == "start":
        print(f"  engine {start['engine']}, root {start['root']}")
    if (
        any(r["event"] == "candidate" for r in run)
        or not walks
        and any(r["event"] == "round" for r in run)
    ):
        refute_summary(run, args)
        return
    if not walks:
        print("  no walks yet")
        return
    moves = (
        args.moves.split(",")
        if args.moves
        else sorted(walks[0]["root"], key=walks[0]["root"].get, reverse=True)[:3]
    )

    print(
        f"\n{'time':<16} {'round':>5} {'background':>17} | "
        + " ".join(f"{m:>6}" for m in moves)
        + " | agree / walked | baseline resolved: explored, explored leaves, held out, not explored yet"
    )
    for w in walks:
        bg = w["background"]
        bgs = f"d{bg['depth']} {bg['nodes'] / 1e9:7.1f}B" if bg else "-"
        root = " ".join(f"{w['root'].get(m, 0):+6}" for m in moves)
        compared = w["agree"] + w["disagree"]
        print(
            f"{w['time'][:16]:<16} {w['round']:>5} {bgs:>17} | {root} | {fraction(w['agree'], compared):>14} |"
            f" {fraction(*baseline_counts(w, 'explored')):>12}, {fraction(*baseline_counts(w, 'explored leaves')):>12},"
            f" {fraction(*baseline_counts(w, 'held out')):>12},"
            f" {fraction(*baseline_counts(w, 'not explored yet'))}"
        )

    if explores:
        adopted = sum(e["after"]["best"] == e["move"] for e in explores)
        both = [
            e
            for e in explores
            if None not in (e["before"]["score"], e["after"]["score"])
        ]
        raised = sum(e["after"]["score"] > e["before"]["score"] for e in both)
        newly = sum(
            e["before"]["score"] is None and e["after"]["score"] is not None
            for e in explores
        )
        delta = sum(e["after"]["score"] - e["before"]["score"] for e in both) / max(
            len(both), 1
        )
        hours = sum(e["seconds"] for e in explores) / 3600
        print(
            f"\n{len(explores)} explorations of {len({(e['line'], e['move']) for e in explores})} targets, {hours:.1f} hours:"
            f" cdb then played the engine's move in {fraction(adopted, len(explores))};"
            f" its score for that move rose in {fraction(raised, len(both))} (by {delta:+.1f} cp on average),"
            f" and {newly} moves got a first score; {sum(e['enqueued'] for e in explores)} positions were added to cdb."
        )
    if errors:
        print(f"{len(errors)} errors, the last one: {errors[-1]['message']}")

    first, last = walks[0], walks[-1]
    print(
        f"\nFrom the first walk ({first['time'][:16]}) to the last ({last['time'][:16]}):"
    )
    for m in moves:
        a, b = first["root"].get(m), last["root"].get(m)
        if None not in (a, b):
            print(f"  cdb's score of {m} at the root: {a:+} -> {b:+}")
    for group in GROUPS:
        n, d = baseline_counts(last, group)
        if d:
            print(
                f"  disagreements of the first walk, {group}: {fraction(n, d)} resolved"
            )

    if args.plot:
        plot(walks, explores, moves, args.plot)


def gap(view):
    """by how much cdb prefers its best move to the engine's move, None if it has no score for that"""
    return (
        None if view["engineMove"] is None else view["bestScore"] - view["engineMove"]
    )


def refute_summary(run, args):
    rounds = [r for r in run if r["event"] == "round"]
    walks = {r["round"]: r for r in run if r["event"] == "pvwalk"}
    candidates = [r for r in run if r["event"] == "candidate"]
    errors = [r for r in run if r["event"] == "error"]
    if not rounds:
        print("  no rounds yet")
        return
    moves = (
        args.moves.split(",")
        if args.moves
        else sorted(rounds[0]["root"], key=rounds[0]["root"].get, reverse=True)[:3]
    )
    print(
        f"\n{'time':<16} {'round':>5} {'background':>17} | "
        + " ".join(f"{m:>6}" for m in moves)
        + " | PV disagreements | repaired: positions, fixes, searches"
    )
    for r in rounds:
        bg = r["background"]
        bgs = f"d{bg['depth']} {bg['nodes'] / 1e9:7.1f}B" if bg else "-"
        root = " ".join(f"{r['root'].get(m, 0):+6}" for m in moves)
        w = walks.get(r["round"])
        mine = [c for c in candidates if c["round"] == r["round"]]
        print(
            f"{r['time'][:16]:<16} {r['round']:>5} {bgs:>17} | {root} | {w['disagreements'] if w else '-':>17} |"
            f" {len(mine)}, {sum(c['fixes'] for c in mine)}, {sum(c['searches'] for c in mine)}"
        )
    if candidates:
        n = len(candidates)
        adopted = sum(c["after"]["best"] == c["engineMove"] for c in candidates)
        changed = sum(c["after"]["best"] != c["before"]["best"] for c in candidates)
        both = [
            c for c in candidates if None not in (gap(c["before"]), gap(c["after"]))
        ]
        narrowed = sum(gap(c["after"]) < gap(c["before"]) for c in both)
        dgap = sum(gap(c["after"]) - gap(c["before"]) for c in both) / max(len(both), 1)
        dbest = (
            sum(c["after"]["bestScore"] - c["before"]["bestScore"] for c in candidates)
            / n
        )
        print(
            f"\n{n} repairs of {len({c['line'] for c in candidates})} positions, {sum(c['searches'] for c in candidates)} short searches:"
            f" cdb's best move changed in {fraction(changed, n)}, to the engine's move in {fraction(adopted, n)};"
            f" cdb's preference of its best move over the engine's narrowed in {fraction(narrowed, len(both))} (by {dgap:+.1f} cp on average),"
            f" and its score of its best move changed by {dbest:+.1f} cp on average."
        )
    if errors:
        print(f"{len(errors)} errors, the last one: {errors[-1]['message']}")
    # the scores at the root after the last round, or at its start if it has not ended
    ends = [r for r in run if r["event"] == "roundEnd"]
    first = rounds[0]
    last = ends[-1] if ends and ends[-1]["time"] > rounds[-1]["time"] else rounds[-1]
    print(f"\nFrom the first round ({first['time'][:16]}) to {last['time'][:16]}:")
    for m in moves:
        a, b = first["root"].get(m), last["root"].get(m)
        if None not in (a, b):
            print(f"  cdb's score of {m} at the root: {a:+} -> {b:+}")
    if args.plot:
        plot_refute(rounds, candidates, moves, args.plot)


def plot_refute(rounds, candidates, moves, path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    t = [datetime.fromisoformat(r["time"]) for r in rounds]
    _, axes = plt.subplots(2, 1, figsize=(8, 7), sharex=True)
    for m in moves:
        axes[0].plot(
            t, [r["root"].get(m) for r in rounds], marker="o", markersize=3, label=m
        )
    axes[0].set_ylabel("cdb score at the root")
    axes[0].legend(fontsize=8)
    tc = [datetime.fromisoformat(c["time"]) for c in candidates]
    adopted, cum = 0, []
    for c in candidates:
        adopted += c["after"]["best"] == c["engineMove"]
        cum.append(adopted)
    axes[1].plot(tc, range(1, len(tc) + 1), label="repaired positions")
    axes[1].plot(tc, cum, label="cdb then played the engine's move")
    axes[1].set_ylabel("cumulative")
    axes[1].legend(fontsize=8)
    for ax in axes:
        ax.grid(alpha=0.4, linewidth=0.5)
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    plt.setp(axes[1].get_xticklabels(), rotation=45, ha="right", fontsize=7)
    plt.savefig(path, dpi=150, bbox_inches="tight")
    print(f"\nPlotted to {path}.")


def plot(walks, explores, moves, path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    t = [datetime.fromisoformat(w["time"]) for w in walks]
    _, axes = plt.subplots(3, 1, figsize=(8, 10), sharex=True)
    for m in moves:
        axes[0].plot(
            t, [w["root"].get(m) for w in walks], marker="o", markersize=3, label=m
        )
    axes[0].set_ylabel("cdb score at the root")
    axes[0].legend(fontsize=8)
    axes[1].plot(
        t,
        [100 * w["agree"] / max(w["agree"] + w["disagree"], 1) for w in walks],
        marker="o",
        markersize=3,
        label="walk: cdb agrees with the engine",
    )
    for group in GROUPS:
        counts = [baseline_counts(w, group) for w in walks]
        if any(d for _, d in counts):
            axes[1].plot(
                t,
                [100 * n / d if d else None for n, d in counts],
                marker="o",
                markersize=3,
                label=f"baseline {group}: resolved",
            )
    axes[1].set_ylabel("%")
    axes[1].legend(fontsize=8)
    te = [datetime.fromisoformat(e["time"]) for e in explores]
    axes[2].plot(te, range(1, len(te) + 1), label="explorations")
    adopted = 0
    cum = []
    for e in explores:
        adopted += e["after"]["best"] == e["move"]
        cum.append(adopted)
    axes[2].plot(te, cum, label="cdb then played the engine's move")
    axes[2].set_ylabel("cumulative")
    axes[2].legend(fontsize=8)
    for ax in axes:
        ax.grid(alpha=0.4, linewidth=0.5)
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    plt.setp(axes[2].get_xticklabels(), rotation=45, ha="right", fontsize=7)
    plt.savefig(path, dpi=150, bbox_inches="tight")
    print(f"\nPlotted to {path}.")


if __name__ == "__main__":
    main()
