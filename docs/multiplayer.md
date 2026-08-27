# Multiplayer

Two or more machines, one game, over a LAN or a port somebody opened for a friend.

```bash
# both machines, same pack
uv run hts gui data/base
```

The start screen does the rest: type a name, pick **Găzduiește** or **Intră în joc**, and the
host reads out the address the lobby shows.

---

## 1. The design, in one sentence

The engine's central theorem is

```
Game = f(content_hash, seed, max_turns, [decision₀, decision₁, …])
```

so network play is that theorem used in anger: give every machine the same content and the same
seed, and the **only** thing that has to travel is the stream of decisions.

That is the whole design. There is no view serialisation anywhere in `net/` — a client does not
*receive* a board, it *computes* one, out of its own engine, redacted for its own seat by the
same `build_view` the host uses. What crosses the wire is a few hundred bytes per turn.

```
host                                   client
────                                   ──────
Engine (authoritative *ordering*)      Engine (same content + seed)
  │                                      │
  │ asks seat p1 (mine)                  │ asks seat p1 (not mine → wait)
  ├─ the board answers                   │
  ├─ broadcast APPLY ────────────────────┤
  └─ apply                               └─ apply
```

**The host is the ordering authority, not the state authority.** Every decision — including the
host's own — is broadcast and then applied, so every engine consumes the same answers in exactly
one order. The host applying its own answer without broadcasting it would be the single way this
design can drift, so it does not: `GameHost.settle` is the only path in.

---

## 2. Trust model — read this before opening a port

Lockstep means **every machine holds the whole state**, including other players' hands. The UI
never shows them (each client renders `engine.view(my_seat)`, which is redacted in the core), but
a modified client could look.

This is a game for people who can see each other. It is not hardened against a cheating peer, and
saying otherwise would be dishonest. Hardening it would mean host-authoritative play with the
full redacted view on the wire — a different design, and a much larger one.

Practically: play with friends, on a network you trust. Do not expose the port to the internet.

---

## 3. Seat ownership — the one rule that must not break

Every seat is answered by **exactly one** machine.

| Machine | Answers for |
|---|---|
| host | its own seat, **and every AI seat** |
| each client | its own seat, and nothing else |

The AI clause is the subtle one: if two machines both ran an agent for seat `p4`, both would
publish an answer to the same question and every engine at the table would consume a decision
meant for somebody else. `NetSession.local_seats()` enforces it, and
`tests/test_menu_and_netplay.py` asserts the table is covered exactly once, as a partition.

---

## 4. The handshake

Content is **not** sent. Both ends load the same pack from disk and the handshake compares
`content_hash` — the same check `hts replay` runs before re-running a log, and it covers a pack's
`plugin.py` as well as its YAML.

```
client → host   hello    name, protocol version, content_hash
host   → client welcome  seat, players, seed, max_turns, content_hash, names
host   → all    lobby    who is here, how many are still missing
host   → all    start    deal now
client → host   decision kind + data          (one per question that seat is asked)
host   → all    apply    n, seat, kind, data  (the settled order)
```

A mismatch is refused **at the door**, with both hashes in the message:

```
you are running different content: host 5ec0e368cfd6, you 3dcd7aebf14b.
Same pack, same edits, including plugin.py.
```

That is deliberate. "Your Dragon says 11 and mine says 10" otherwise surfaces forty minutes later
as an inexplicable desync, and nobody would ever guess why.

---

## 5. When something goes wrong

| What you see | What it means |
|---|---|
| `could not reach 192.168.1.5:57311` | wrong address, host not started, or a firewall |
| `you are running different content` | different pack, different edits, or a changed `plugin.py` |
| `this game speaks protocol 1, the joining client speaks 0` | one side is an older build |
| `the table is full` | every seat was taken before you dialled |
| `Bob left the game` | a player disconnected mid-hand |
| `the games are out of step` | a decision arrived out of sequence — see below |

**A disconnect mid-game ends the game.** Lockstep has no way to answer for a seat nobody owns, so
the honest move is to stop and name who left rather than hang on a question that will never be
answered.

**"Out of step" should never happen.** It means a decision arrived with the wrong sequence number
or for the wrong seat, which in a lockstep design is either a lost message or an engine that
diverged. Both are unrecoverable, and both are far easier to diagnose at the moment they happen
than three turns later as a quietly wrong board — so the relay fails loudly instead of drifting.
If you can reproduce one, it is a bug worth reporting with both machines' packs.

---

## 6. Where the code lives

| Module | Job |
|---|---|
| `net/protocol.py` | newline-delimited JSON over TCP, and the message kinds |
| `net/session.py` | the decision relay, and the `DecisionSource` every networked engine runs on |
| `net/host.py` | opens a port, seats arrivals in order, settles every decision |
| `net/client.py` | dials in, takes a seat, deals the same game |
| `ui/pygame/menu.py` | the start screen and the lobby |
| `ui/pygame/netplay.py` | the only module that knows both a socket and a pygame surface |

Layering: `net/` may see `core/` and nothing above it — no `ui`, no `pygame`, no `ai`. An import
of `ui` there would be the first step away from "send decisions, not state", so
`tests/test_layering.py` asserts it cannot happen.

---

## 7. Tests

```bash
uv run pytest tests/test_net.py tests/test_menu_and_netplay.py
```

The acceptance test is not "a message arrived". It is **two real windows, a real socket, and
every answer submitted through the same call a mouse click makes**, asserting that both engines
finish on the same turn with the same winner and every card in every zone in the same place.
