"""Drive a QueuePi server with made-up orders, through the HTTP API.

    python scripts/simulate.py fill --waiting 25 --ready 4   # a static rush
    python scripts/simulate.py run --rate 2 --speed 10       # a live queue
    python scripts/simulate.py reset

`run` simulates a shop: orders arrive at random (`--rate` per minute), each
takes a random time to make (lognormal, median `--prep` minutes, so orders
finish out of order), and customers pick up a minute or so after their order
is ready. `--speed 10` runs ten times faster than real time.

Receipt numbers count up from a random start and the display number is the
last two digits, so numbers wrap around. If a number is still on the board
(a slow order from 100 receipts ago), that receipt is skipped.
"""

from __future__ import annotations

import argparse
import heapq
import http.cookiejar
import json
import random
import time
import urllib.error
import urllib.request


class Client:
    def __init__(self, server: str, pin: str):
        self.server = server.rstrip("/")
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.post("/api/login", {"pin": pin})

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.server + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with self.opener.open(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise SystemExit("wrong PIN (use --pin)") from e
            return e.code, json.load(e)

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        return self.request("POST", path, body)

    def add(self, number: str) -> dict | None:
        status, order = self.post("/api/orders", {"number": number})
        return order if status == 201 else None

    def move(self, order: dict, to: str) -> dict | None:
        status, new = self.post(
            f"/api/orders/{order['id']}/transition", {"to": to, "expect": order["status"]}
        )
        return new if status == 200 else None


def fill(client: Client, waiting: int, ready: int, rng: random.Random) -> None:
    numbers = rng.sample(range(100), waiting + ready)
    for n in numbers[:ready]:
        order = client.add(f"{n:02d}")
        if order:
            client.move(order, "ready")
    for n in numbers[ready:]:
        client.add(f"{n:02d}")
    print(f"added {waiting} waiting and {ready} ready")


def run(client: Client, rate: float, prep: float, pickup: float, speed: float, rng: random.Random) -> None:
    receipt = rng.randrange(10000, 99000)
    t0 = time.monotonic()
    now = lambda: (time.monotonic() - t0) * speed / 60  # simulated minutes
    events: list[tuple[float, int, str, dict]] = []  # (when, tiebreak, action, order)
    tiebreak = 0
    next_arrival = rng.expovariate(rate)
    print(f"simulating: {rate}/min, median prep {prep} min, {speed}x speed; Ctrl-C to stop")
    while True:
        t = now()
        if t >= next_arrival:
            receipt += 1
            order = client.add(str(receipt)[-2:])
            if order:
                done = t + rng.lognormvariate(0, 0.5) * prep
                heapq.heappush(events, (done, tiebreak := tiebreak + 1, "ready", order))
                print(f"{t:6.1f}  receipt {receipt}: order {order['number']} added")
            else:
                print(f"{t:6.1f}  receipt {receipt}: {str(receipt)[-2:]} still on the board, skipped")
            next_arrival = t + rng.expovariate(rate)
        while events and events[0][0] <= t:
            _, _, action, order = heapq.heappop(events)
            new = client.move(order, "ready" if action == "ready" else "picked_up")
            if new and action == "ready":
                later = t + rng.expovariate(1 / pickup)
                heapq.heappush(events, (later, tiebreak := tiebreak + 1, "pickup", new))
            print(f"{t:6.1f}  order {order['number']} {action}{'' if new else ' (failed)'}")
        time.sleep(0.05)


def ready_demo(client: Client, every: float, batch: int, rng: random.Random) -> None:
    """Show off the display's ready animation: every few seconds, mark the
    oldest waiting orders ready (sometimes several at once with --batch),
    keeping some orders waiting and only a few ready."""
    print(f"marking orders ready every {every:g} s; Ctrl-C to stop")
    while True:
        status, board = client.request("GET", "/api/board")
        waiting, ready = board["waiting"], board["ready"]
        for order in ready[: max(0, len(ready) - 3)]:  # keep three or so on the board
            client.move(order, "picked_up")
        for _ in range(max(0, 8 - len(waiting))):  # keep some waiting
            client.add(f"{rng.randrange(100):02d}")
        status, board = client.request("GET", "/api/board")
        n = rng.randint(1, batch)
        chosen = board["waiting"][:n]
        for order in chosen:
            client.move(order, "ready")
        print("ready:", " ".join(o["number"] for o in chosen))
        time.sleep(every)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--pin", default="1234")
    ap.add_argument("--seed", type=int)
    sub = ap.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fill", help="put a fixed set of orders on the board")
    f.add_argument("--waiting", type=int, default=25)
    f.add_argument("--ready", type=int, default=4)
    r = sub.add_parser("run", help="simulate a live queue")
    r.add_argument("--rate", type=float, default=2.0, help="orders per minute")
    r.add_argument("--prep", type=float, default=6.0, help="median minutes to make an order")
    r.add_argument("--pickup", type=float, default=1.5, help="mean minutes from ready to pickup")
    r.add_argument("--speed", type=float, default=1.0, help="simulated minutes per real minute")
    d = sub.add_parser("ready", help="mark orders ready every few seconds, to watch the display's animation")
    d.add_argument("--every", type=float, default=5.0, help="seconds between orders becoming ready")
    d.add_argument("--batch", type=int, default=1, help="up to this many at once")
    sub.add_parser("reset", help="clear the board (end of day)")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    client = Client(args.server, args.pin)
    if args.command == "fill":
        fill(client, args.waiting, args.ready, rng)
    elif args.command == "run":
        try:
            run(client, args.rate, args.prep, args.pickup, args.speed, rng)
        except KeyboardInterrupt:
            pass
    elif args.command == "ready":
        try:
            ready_demo(client, args.every, args.batch, rng)
        except KeyboardInterrupt:
            pass
    else:
        print(client.post("/api/reset", {"confirm": True})[1])


if __name__ == "__main__":
    main()
