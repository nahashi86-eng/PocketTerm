"""
Q-learning maze you can watch in the terminal. Pure Python, no installs.

  python rl_maze_slow.py                  # default: slow and watchable
  python rl_maze_slow.py --delay 0.3      # even slower
  python rl_maze_slow.py --batch 0        # watch EVERY episode
  python rl_maze_slow.py --batch 25       # train 25 episodes silently between demos
  python rl_maze_slow.py --no-slip        # easier, no random slips

Legend:  @ agent   X pit   G goal   dark = wall   arrows = best known move
Press Ctrl+C to stop.
"""
import argparse
import os
import random
import sys
import time
from collections import deque

ap = argparse.ArgumentParser()
ap.add_argument("--delay", type=float, default=0.12, help="seconds per step when watching")
ap.add_argument("--batch", type=int, default=10, help="watch every Nth episode (0 = every one)")
ap.add_argument("--episodes", type=int, default=1500)
ap.add_argument("--no-slip", action="store_true")
args = ap.parse_args()

MAP = [
    "S...#.......",
    ".##.#.XXXX#.",
    ".#..#.#...#.",
    ".#.XX.#.#.#.",
    ".#.##...#...",
    ".#....###.#.",
    ".####.X...#.",
    "......X.###.",
    "#####.#.....",
    "......#.###.",
    ".######.....",
    "........#..G",
]
N = len(MAP)
WALLS = {(r, c) for r in range(N) for c in range(N) if MAP[r][c] == "#"}
PITS = {(r, c) for r in range(N) for c in range(N) if MAP[r][c] == "X"}
START = (0, 0)
GOAL = (N - 1, N - 1)
MOVES = [(-1, 0), (1, 0), (0, -1), (0, 1)]
ARROWS = ["↑", "↓", "←", "→"]
NAMES = ["up", "down", "left", "right"]

ALPHA, GAMMA = 0.2, 0.97
SLIP = 0.0 if args.no_slip else 0.1
MAX_STEPS = 300
eps = 0.3

Q = {(r, c): [0.0] * 4 for r in range(N) for c in range(N)}


def env_step(state, action):
    if random.random() < SLIP:
        action = random.randrange(4)
    r, c = state[0] + MOVES[action][0], state[1] + MOVES[action][1]
    if not (0 <= r < N and 0 <= c < N) or (r, c) in WALLS:
        return state, -1, False
    nxt = (r, c)
    if nxt == GOAL:
        return nxt, 100, True
    if nxt in PITS:
        return nxt, -50, True
    return nxt, -1, False


def best(state):
    v = Q[state]
    return v.index(max(v))


def pick(state):
    v = Q[state]
    m = max(v)
    return random.choice([a for a in range(4) if abs(v[a] - m) < 1e-9])


def draw(state, lines):
    out = ["\033[H"]
    for r in range(N):
        row = ""
        for c in range(N):
            p = (r, c)
            if p == state:
                row += "\033[1;30;43m @ \033[0m"
            elif p in WALLS:
                row += "\033[100m   \033[0m"
            elif p in PITS:
                row += "\033[97;41m X \033[0m"
            elif p == GOAL:
                row += "\033[97;42m G \033[0m"
            elif any(Q[p]):
                row += f"\033[36m {ARROWS[best(p)]} \033[0m"
            else:
                row += "\033[2m · \033[0m"
        out.append(row + "\033[K\n")
    for line in lines:
        out.append(line + "\033[K\n")
    sys.stdout.write("".join(out))
    sys.stdout.flush()


recent = deque(maxlen=50)      # 1 if the episode reached the goal
recent_r = deque(maxlen=50)    # total reward per episode


def stats_lines(ep, t, total, extra):
    rate = 100 * sum(recent) / len(recent) if recent else 0
    avg = sum(recent_r) / len(recent_r) if recent_r else 0
    return [
        "",
        f"Episode {ep}   step {t}   epsilon {eps:.2f}   reward this episode {total}",
        f"Last 50 episodes: {rate:.0f}% reached the goal, avg reward {avg:.1f}",
    ] + extra


def run_episode(ep, show):
    global eps
    s, total = START, 0
    won = False
    for t in range(1, MAX_STEPS + 1):
        explore = random.random() < eps
        a = random.randrange(4) if explore else pick(s)
        nxt, r, done = env_step(s, a)

        old = Q[s][a]
        target = r + (0 if done else GAMMA * max(Q[nxt]))
        Q[s][a] = old + ALPHA * (target - old)
        total += r

        if show:
            q = "  ".join(f"{NAMES[i]}={Q[s][i]:6.1f}" for i in range(4))
            extra = [
                f"Move: {NAMES[a]} ({'random explore' if explore else 'best known'}) -> reward {r}",
                f"Q-values of the cell it just left ({old:.1f} -> {Q[s][a]:.1f} for that move):",
                "  " + q,
            ]
            draw(nxt, stats_lines(ep, t, total, extra))
            time.sleep(args.delay)

        s = nxt
        if done:
            won = r == 100
            break

    recent.append(1 if won else 0)
    recent_r.append(total)
    if show:
        msg = "Reached the goal!" if won else ("Fell in a pit." if s in PITS else "Ran out of steps.")
        draw(s, stats_lines(ep, t, total, ["", msg]))
        time.sleep(max(args.delay * 8, 1.0))
    eps = max(0.02, eps * 0.996)


os.system("")  # enables ANSI colors in Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.stdout.write("\033[2J\033[?25l")
try:
    for ep in range(1, args.episodes + 1):
        show = args.batch == 0 or ep <= 2 or ep % args.batch == 0
        run_episode(ep, show)
except KeyboardInterrupt:
    pass
finally:
    sys.stdout.write("\033[?25h\n")