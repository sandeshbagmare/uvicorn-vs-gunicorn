# Uvicorn vs Gunicorn — Explained So Anyone Can Understand It
### From "What is the internet?" to "Which server should I pick?" — No coding experience needed.

> **Who is this for?**
> Absolute beginners, students, managers, curious people — anyone who wants to understand
> what Uvicorn and Gunicorn are, why they exist, how they differ, and which one to use.
> No prior programming knowledge is assumed.
>
> **Already technical?** Jump straight to [Part 6 — Real Benchmark Results](#part-6--real-benchmark-results)
> or open the expert document [`FINAL_CONFLUENCE_PAGE.md`](FINAL_CONFLUENCE_PAGE.md).
>
> **Reading time:** About 20 minutes end to end.

---

## Table of Contents

- [Part 1 — How Websites Work (The Basics)](#part-1--how-websites-work-the-basics)
- [Part 2 — What is a Web Server?](#part-2--what-is-a-web-server)
- [Part 3 — WSGI vs ASGI: The Old Way vs The New Way](#part-3--wsgi-vs-asgi-the-old-way-vs-the-new-way)
- [Part 4 — Meet Uvicorn](#part-4--meet-uvicorn)
- [Part 5 — Meet Gunicorn](#part-5--meet-gunicorn)
- [Part 6 — Real Benchmark Results](#part-6--real-benchmark-results)
- [Part 7 — Speed: Are They Actually Different?](#part-7--speed-are-they-actually-different)
- [Part 8 — How to Decide Which One to Use](#part-8--how-to-decide-which-one-to-use)
- [Part 9 — Production Checklist in Plain English](#part-9--production-checklist-in-plain-english)
- [Part 10 — FAQ](#part-10--faq)
- [Glossary — Every Term, Explained Simply](#glossary--every-term-explained-simply)

---

## Part 1 — How Websites Work (The Basics)

### 1.1 What happens when you open a website?

Let's say you open `instagram.com` on your phone. Here is exactly what happens, step by step:

```
Your phone
  ↓  You tap the app icon
Your phone sends a tiny message through the internet:
  "Hey Instagram, please give me your home page."
  ↓  That message travels through cables and wifi
Instagram's computers (in a data centre somewhere)
  ↓  They read the message, figure out what to send back
  "Here is your home page — here are photos, stories, notifications..."
  ↓  That reply travels back to your phone
Your phone draws what you see on screen
```

The computers at Instagram that receive your message and send back the answer are called **servers**.

The messages going back and forth follow a shared rulebook called **HTTP**
(HyperText Transfer Protocol — essentially the "language" the internet uses to communicate).

### 1.2 A server is just a computer running a program

A server is not magic. It is literally just a computer — often in a data centre — running a
**program** that listens for incoming messages and sends back replies.

That program is called a **web server**.

The web server's job, in three steps:
1. **Listen** — wait for messages from browsers, phones, and apps
2. **Process** — work out what the message is asking for, run some code, produce an answer
3. **Reply** — send the answer back

**Uvicorn and Gunicorn are both web server programs.** They run on a computer, listen for
requests, and send back responses. The difference is *how* they do it — and that difference
matters enormously depending on your situation.

---

### 1.3 The Restaurant — Your Mental Model for This Whole Guide

The best way to understand web servers is to picture a restaurant.
This analogy will appear throughout the entire guide.

| The Restaurant | The Web Server World |
|---|---|
| 🏠 The restaurant building | Your web application |
| 👨‍🍳 Chef in the kitchen | Your Python code (FastAPI, Django, Flask, etc.) |
| 🧑‍🍽️ The waiter | **Uvicorn** — takes requests, passes to kitchen, sends reply |
| 🕴️ The floor manager | **Gunicorn** — supervises the waiters, manages the team |
| 📋 A customer's order | An HTTP request from a browser or app |
| 🍕 Food delivered to the table | The HTTP response sent back |
| 🪑 One table being served | One user's connection |
| 👥 Many tables at once | Many concurrent users |
| 🏗️ Four restaurant branches | Running four worker processes |

---

## Part 2 — What is a Web Server?

### 2.1 The waiter in detail

A web server (like Uvicorn) is the **waiter** of your restaurant.

When you visit a website:
- Your browser is the **customer**
- The Python code (FastAPI) is the **chef** who prepares the answer
- The web server is the **waiter** — it takes your request to the kitchen and brings back the result

The chef never talks to customers directly. The waiter is the go-between.

### 2.2 What is Python? What is FastAPI?

**Python** is a programming language — a way for developers to write instructions for computers.
It reads almost like English, which is why it is very popular.

**FastAPI** is a toolkit (called a "framework") built in Python that makes it easy to create
websites and APIs. Think of it as the chef's recipe book — all the tools to prepare great food.

But the chef (FastAPI code) and the waiter (Uvicorn or Gunicorn) need to agree on *how* to
communicate. That shared agreement is called a **standard** — and this is where WSGI and ASGI
enter the picture.

---

## Part 3 — WSGI vs ASGI: The Old Way vs The New Way

This is the most important concept in the whole guide. Everything else flows from here.

### 3.1 Why does a standard need to exist?

The web server and your Python app are two completely separate programs, possibly made by
different people. They need a **shared language** — a set of rules both sides agree to follow
so they can work together.

Python has had two such standards over the years:
- **WSGI** — the old standard (2003)
- **ASGI** — the new standard (2019)

### 3.2 WSGI — The Old Way (Synchronous)

**WSGI** stands for **Web Server Gateway Interface**. Pronounced "wizzy" or "whiskey".

The most important thing about WSGI is that it is **synchronous**, which means:

> Handle ONE request. Wait until it is completely finished. Then handle the next one.

**The waiter analogy for WSGI:**

Imagine a restaurant where the waiter works like this:
```
1. Take table 1's order.
2. Walk to the kitchen. Hand in the order.
3. STAND in the kitchen and WAIT until the food is ready. ⏳ (could be 30 seconds)
4. Walk back and deliver the food to table 1.
5. NOW go to table 2.
```

This waiter can serve perhaps 10–15 tables per hour. If the kitchen is slow (slow database,
slow external API), the waiter stands there doing absolutely nothing while everyone else waits.

This was perfectly fine in 2003 — websites were simpler. But as apps became more complex —
live chat, streaming, real-time notifications — this became a major bottleneck.

### 3.3 ASGI — The New Way (Asynchronous)

**ASGI** stands for **Asynchronous Server Gateway Interface**.

"Asynchronous" means:

> Start a request. While waiting for something to finish (like a database query), go handle other
> requests. Come back when the first one is ready. Keep switching between many tasks constantly.

**The waiter analogy for ASGI:**

Now imagine a smarter waiter:
```
1. Take table 1's order. Walk to kitchen. Hand it in.
2. INSTEAD of waiting: immediately go to table 2. Take their order. Hand it in.
3. Go to table 3. Take their order. Hand it in.
4. Go to table 4...
5. "DING!" — Table 1's food is ready. Go deliver it.
6. "DING!" — Table 2's food is ready. Go deliver it.
```

One waiter can now serve **50+ tables simultaneously**, because they never stand still.

**In Python, this behaviour uses the keywords `async` and `await`:**

```python
# WSGI style — waiter stands in the kitchen waiting:
def get_users():
    users = database.query("SELECT * FROM users")  # stands still for 50ms
    return users

# ASGI style — waiter walks away while kitchen works:
async def get_users():
    users = await database.query("SELECT * FROM users")  # goes to serve other tables for 50ms
    return users
```

The `await` keyword is the waiter saying:
*"I'm going to pause and wait for this thing. While I wait, go do something else. Come back when it is done."*

### 3.4 WSGI vs ASGI — Side-by-Side

| | WSGI (Old) | ASGI (New) |
|---|---|---|
| **Year created** | 2003 | 2019 |
| **Style** | Synchronous (one at a time) | Asynchronous (many at once) |
| **Waiter analogy** | Stands in kitchen waiting | Never stops moving |
| **Live chat / WebSockets** | ❌ Not possible natively | ✅ Built in |
| **Streaming responses** | ❌ Limited | ✅ Full support |
| **Real-time notifications** | ❌ Very hard | ✅ Easy |
| **Python frameworks** | Flask, classic Django | FastAPI, Starlette, Django Channels |
| **Server programs** | Gunicorn, uWSGI | Uvicorn, Hypercorn, Granian |
| **Best for** | Classic web apps, forms, CRUD | High-traffic APIs, real-time, streaming |

> **What is a WebSocket?**
> A permanent two-way connection between your browser and the server — like a phone call
> rather than sending letters. Used for: live chat, multiplayer games, stock tickers,
> AI streaming responses (like the text streaming you see in ChatGPT), live dashboards.
> Needs ASGI. Cannot be done properly with WSGI.

### 3.5 Why this matters for our comparison

- **Uvicorn** is an **ASGI server** — the modern, async way
- **Gunicorn** is primarily a **WSGI server** — the traditional, sync way
- **FastAPI requires ASGI** — you must use an ASGI server like Uvicorn to run it correctly

Gunicorn CAN work with FastAPI, but only via a special adapter: you tell Gunicorn to use
Uvicorn as each of its workers. Then Gunicorn acts purely as the manager, and Uvicorn handles
all the actual HTTP work inside each worker.

---

## Part 4 — Meet Uvicorn

### 4.1 What is Uvicorn?

Uvicorn is a **fast, lightweight ASGI web server** written in Python.

It is the modern way to run async Python web apps. The efficient waiter who never stands still.

The name: **UV** (from `uvloop`, a fast event loop) + **icorn** (from "unicorn", like Gunicorn).

### 4.2 What makes Uvicorn fast?

When you install `uvicorn[standard]` (the recommended version), you get two speed boosters:

#### Speed Booster 1: uvloop

Python normally uses something called `asyncio` to manage the "go do other things while I wait"
behaviour. Think of this as a standard car engine.

`uvloop` is a replacement engine built on `libuv` — the same engine that powers Node.js,
one of the fastest web platforms in the world. It runs **2–4× faster** than standard asyncio.

> ⚠️ **Important:** `uvloop` only works on **Linux** and **Mac**. On **Windows**, Uvicorn
> automatically falls back to the standard `asyncio` engine (still works, just slower).
> This is why the Windows benchmark numbers below look less impressive —
> production Linux numbers would be significantly better.

#### Speed Booster 2: httptools

When a request arrives, something needs to read and decode the raw HTTP message.
`httptools` is a super-fast C-language reader, much faster than the default Python one (`h11`).

**How to install with both speed boosters:**
```
pip install "uvicorn[standard]"
```

### 4.3 How do you run Uvicorn?

```bash
# One worker (default — good for containers, dev, async-heavy apps):
uvicorn myapp.main:app

# Four workers (four independent processes — for CPU work or bare Linux servers):
uvicorn myapp.main:app --workers 4

# Full example with host and port specified:
uvicorn myapp.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### 4.4 What exactly is a "worker"?

A **worker** is one independent copy of your program running in its own private memory space.

**1 worker = 1 waiter for the whole restaurant.**

One well-trained async waiter can handle many tables at once as long as they never get stuck
standing in the kitchen. But there is a physical limit to how fast one person can move.

**4 workers = 4 independent waiters, each working in parallel.**

Now four people are simultaneously taking orders and delivering food. The restaurant handles
roughly 4× the traffic in cases where the work is CPU-heavy.

```
Internet traffic (thousands of users)
           ↓
      [Port 8000]    ← single door everyone comes through
     ↙   ↓   ↘   ↘
   W1   W2   W3   W4   ← 4 independent worker processes
     ↘   ↓   ↙   ↙
      FastAPI app    ← the kitchen they all serve from
```

**When do you need more than 1 worker?**
- You want to use more than one CPU core (each process uses its own core)
- You want backup if one worker crashes
- You have CPU-intensive requests (image processing, encryption, AI calculations)

**When is 1 worker enough?**
- Most requests are waiting for databases or APIs — async handles this brilliantly without extra workers
- You are running in containers/Kubernetes — the platform handles scaling for you

### 4.5 Windows support

Uvicorn works perfectly on Windows. The `--workers` flag works on Windows too.
This becomes important when we look at Gunicorn next.

---

## Part 5 — Meet Gunicorn

### 5.1 What is Gunicorn?

**Gunicorn** stands for **Green Unicorn**. It has been around since 2010 and is extremely
battle-tested — trusted in production by thousands of companies around the world.

Here is the point that confuses most people:

> **Gunicorn is NOT primarily a web server. It is a process manager.**
> Its main job is supervising and managing worker processes — not handling HTTP requests.

Gunicorn is the **floor manager** of the restaurant.
The floor manager does not take orders or deliver food. They manage the people who do.

### 5.2 What does Gunicorn (the floor manager) actually do?

| Gunicorn's job | Restaurant equivalent |
|---|---|
| Starts N worker processes when the app launches | Hires N waiters when the restaurant opens |
| Watches every worker constantly | Manager keeps an eye on all staff |
| Replaces a worker that crashes | If a waiter faints, hire a replacement immediately |
| Kills a worker that is frozen and not responding | Fire the waiter who has been stuck in the kitchen for 60 seconds |
| Replaces workers one-by-one when you deploy new code | Smoothly rotate shifts: new waiters start before old ones leave (zero downtime) |
| Recycles workers after X requests to keep memory clean | Replace each waiter after 1000 tables to prevent fatigue/memory leaks |
| Adds or removes workers without restarting | Call in extra staff during the lunch rush |

### 5.3 The powerful combination: Gunicorn + Uvicorn workers

Because Gunicorn itself cannot run ASGI apps like FastAPI, the community invented a clever bridge:

**Use Gunicorn as the manager, but make each of its workers be a Uvicorn ASGI server.**

```bash
gunicorn myapp.main:app -k uvicorn.workers.UvicornWorker -w 4
```

This says: "Gunicorn, manage 4 workers — but each worker should be a Uvicorn ASGI server."

```
            GUNICORN (Floor Manager — process supervision)
           /         |         \         \
       Uvicorn    Uvicorn    Uvicorn    Uvicorn   ← 4 Uvicorn ASGI workers
       Worker 1   Worker 2   Worker 3   Worker 4
           \         |         /         /
                 FastAPI App
              (the kitchen they all serve)
```

You get the best of both worlds:
- ✅ Gunicorn's proven, robust process supervision
- ✅ Uvicorn's async speed inside each worker

### 5.4 The hard limitation: Gunicorn does NOT run on Windows

This is a fundamental technical constraint that will never change.

Gunicorn relies on two Unix-specific operating system features:
- `os.fork()` — makes an exact copy of the running process (Unix/Linux only)
- `fcntl` — coordinates between processes using file locks (Unix/Linux only)

**Windows does not have these.** Gunicorn simply crashes at startup on Windows.

If you are on Windows — whether for development or production — **Uvicorn is your only option**.
And that is perfectly fine: `uvicorn --workers` works well on Windows.

---

## Part 6 — Real Benchmark Results

Theory is one thing. Let's look at what actually happened when we ran real tests.

### 6.1 What was tested and how

**The machine:** Windows 11, 8 CPU cores, Python 3.13
*(No uvloop on Windows — see the note below)*

**The app:** A FastAPI application with 4 types of endpoints, each testing a different scenario

**The load:** Hundreds of requests fired simultaneously (like hundreds of people all pressing F5
at exactly the same instant)

**Compared:** 1 Uvicorn worker vs 4 Uvicorn workers
*(Gunicorn was skipped — Windows limitation)*

> ⚠️ **Important Windows caveat:** These benchmarks ran without `uvloop` (the fast engine that
> only works on Linux/Mac). On a Linux production server with uvloop, the async I/O numbers would
> be dramatically better — a single worker would handle thousands of concurrent async requests.
> Despite this, the *patterns* and *comparisons* teach the right lessons.

---

### 6.2 The Four Test Scenarios

#### Scenario 1: The Trivial Request (`/`)
```python
@app.get("/")
async def root():
    return {"message": "ok", "pid": os.getpid()}
```
No real work — just return a tiny JSON response immediately.
**Restaurant analogy:** Customer asks "What time is it?" — waiter just answers.

---

#### Scenario 2: Good Async Work (`/async-io`) ✅
```python
@app.get("/async-io")
async def async_io():
    await asyncio.sleep(0.05)  # Wait 50ms, but RELEASE the event loop while waiting
    return {"kind": "async-io", "pid": os.getpid()}
```
Simulates a database query or API call. Waits 50ms, but while waiting the event loop is free
to serve other requests. This is async done correctly.
**Restaurant analogy:** Order placed, waiter walks away to serve other tables while kitchen cooks.

---

#### Scenario 3: Blocking Work (`/sync-io`) ⚠️ THE DANGER ZONE
```python
@app.get("/sync-io")
async def sync_io():
    time.sleep(0.05)  # Wait 50ms, BLOCKING the entire event loop — WRONG!
    return {"kind": "sync-io", "pid": os.getpid()}
```
This is the classic mistake: calling a blocking function inside an async handler.
The event loop freezes completely for 50ms per request. Nobody else can be served.
**Restaurant analogy:** Waiter stands personally in the kitchen stirring the pot. All tables wait.

---

#### Scenario 4: CPU-Heavy Work (`/cpu`)
```python
@app.get("/cpu")
async def cpu():
    total = sum(i * i % 7 for i in range(50_000))  # Python number crunching
    return {"result": total, "pid": os.getpid()}
```
Simulates heavy computation — like compressing data, encrypting, or running calculations.
The CPU is genuinely busy doing Python work.
**Restaurant analogy:** Waiter has to personally bake bread from scratch for each order.
More waiters (workers) = more ovens = faster.

---

### 6.3 The Results

**Every number here is from real tests run on this machine and saved in `results/raw/*.json`.**

#### Trivial Request `/`
*2000 requests, 200 at once*

| Metric | 1 Worker | 4 Workers |
|---|---:|---:|
| Speed | **338 req/s** | 218 req/s |
| Succeeded | ✅ 2000/2000 | ✅ 2000/2000 |
| Median latency | 374 ms | 541 ms |
| 95th percentile (p95) | 1,781 ms | 2,844 ms |
| 99th percentile (p99) | 2,912 ms | 4,586 ms |
| Processes used | 1 PID | 4 PIDs |

**Surprise: 1 worker is faster.**
With no real work, distributing requests across 4 processes just adds coordination overhead.
Simpler = faster here.

---

#### Good Async Work `/async-io`
*2000 requests, 200 at once*

| Metric | 1 Worker | 4 Workers |
|---|---:|---:|
| Speed | **111 req/s** | 90 req/s |
| Succeeded | ✅ 2000/2000 | ✅ 2000/2000 |
| Median latency | 1,137 ms | 1,443 ms |
| p95 | 5,372 ms | 5,949 ms |
| p99 | 7,647 ms | 8,751 ms |

**1 worker wins again — on Windows.**
Without uvloop, asyncio overhead dominates. Extra workers can't help.
**On Linux with uvloop: 1 worker at 200 concurrency × 50ms delay → ~4,000 req/s theoretical.**
This is where the async model truly shines. The Windows numbers do not tell the full story.

---

#### Blocking Work `/sync-io` — The Most Important Result
*1000 requests, 200 at once*

| Metric | 1 Worker | 4 Workers |
|---|---:|---:|
| Speed | 22 req/s | **52 req/s** |
| Succeeded | ❌ **728 / 1000** | ✅ **1000 / 1000** |
| Failed (timeouts + errors) | ❌ **272 failures (27%!)** | ✅ 0 failures |
| Median latency | 2,485 ms | 2,769 ms |
| p95 latency | **30,270 ms (30 sec!)** | 8,884 ms |
| p99 latency | 30,325 ms | 12,264 ms |

**This is the most dramatic result in the whole benchmark.**

With 1 worker: **272 requests failed entirely.** Real users got error messages.
The blocking `time.sleep()` inside async froze the whole event loop. 200 simultaneous users
stacked up, waited 30 seconds, and gave up with timeout errors.

With 4 workers: **zero failures.** Every request succeeded. Because 4 workers can each be
blocked independently, throughput multiplied and no requests hit the timeout.

**The lesson has two parts:**
1. **Fix the root cause** — do not call blocking code inside async. Use `await`.
2. **More workers help when you can't fix the blocking** — but they are a band-aid, not a cure.

---

#### CPU-Heavy Work `/cpu`
*600 requests, 100 at once*

| Metric | 1 Worker | 4 Workers |
|---|---:|---:|
| Speed | 107 req/s | **131 req/s** |
| Succeeded | ✅ 600/600 | ✅ 600/600 |
| Median latency | 280 ms | 521 ms |
| p95 latency | 2,755 ms | 2,065 ms |
| p99 latency | 3,018 ms | 2,862 ms |

**4 workers are faster for CPU work — 22% faster overall.**
The improvement is modest here because each request's CPU work (~5ms) is light.
For heavy CPU tasks (image resizing, AI inference, encryption), gains would be much closer to 4×.

---

### 6.4 Proof: workers really did share the load

The test app includes the serving **Process ID (PID)** in every response.
Counting how many distinct PIDs appear across 2000 responses proves load-spreading actually worked.

**1 worker — `/async-io` — 2000 requests:**
```
PID 2788: 2000 requests (100% — the only worker)
```

**4 workers — `/async-io` — 2000 requests:**
```
PID 3344:  290 requests  (14.5%)
PID 18860: 469 requests  (23.5%)
PID 19064: 897 requests  (44.9%)  ← got the most (Windows OS distribution quirk)
PID 16740: 344 requests  (17.2%)
```

All 4 workers shared the work. The uneven distribution is normal on Windows — Linux would
balance more evenly.

---

### 6.5 Lessons Summary

| Workload | 1 Worker | 4 Workers | The Real Lesson |
|---|:---:|:---:|---|
| Trivial work | 🏆 Faster | Slower | Extra processes add overhead with nothing to parallelize |
| Async I/O on Linux | 🏆 Much faster | Similar | uvloop + 1 event loop is incredible for waiting tasks |
| Async I/O on Windows | 🏆 Slightly faster | Slightly slower | Windows hides async benefits — Linux is where it shines |
| Blocking sync code | ❌ 27% failure | 🏆 0% failure | **Never block the event loop. Fix the code first.** |
| CPU-heavy work | Slower | 🏆 ~22% faster | More processes = more cores = real parallelism |

---

## Part 7 — Speed: Are They Actually Different?

### Gunicorn + Uvicorn workers vs Uvicorn `--workers N`

**They are identical in speed. Always.**

The reason is simple: **it is the same Uvicorn code doing the actual HTTP work in both cases.**

```
Gunicorn + Uvicorn workers:
  [Gunicorn master] → supervises → [Uvicorn worker 1]
                                    [Uvicorn worker 2]
                                    [Uvicorn worker 3]
                                    [Uvicorn worker 4]
                                          ↑
                                 HTTP requests handled here

Uvicorn --workers 4:
  [Uvicorn supervisor] → supervises → [Uvicorn worker 1]
                                       [Uvicorn worker 2]
                                       [Uvicorn worker 3]
                                       [Uvicorn worker 4]
                                             ↑
                                    HTTP requests handled here
```

The workers are identical. Only the supervisor layer differs.

**Where they differ is operational robustness — not speed:**

| Feature | Uvicorn `--workers` | Gunicorn + Uvicorn |
|---|:---:|:---:|
| Crash-restart workers | ✅ Basic | ✅✅ Battle-tested since 2010 |
| Kill frozen/hung workers (`--timeout`) | ⚠️ Limited | ✅ Fully implemented |
| Zero-downtime code deploy (`HUP` signal) | ⚠️ Basic | ✅ Proper graceful reload |
| Recycle workers after N requests | ❌ None | ✅ `--max-requests` |
| Add/remove workers without restart | ❌ None | ✅ `TTIN` / `TTOU` signals |
| Lifecycle hooks for custom code | ❌ None | ✅ Full hook system |
| Works on Windows | ✅ Yes | ❌ No |
| Simpler to set up | ✅ One command | Requires more config |

**Choose based on your deployment platform, not on speed.**

---

## Part 8 — How to Decide Which One to Use

### 3 Questions That Decide Everything

**Question 1 — Are you on Windows?**
Yes → **Uvicorn with `--workers N`**. Gunicorn cannot run on Windows. This is your only option — and it works well.

**Question 2 — Are you running in containers / Kubernetes / a cloud container platform?**
Yes → **Uvicorn, 1 worker per container**. Your platform (Kubernetes, AWS ECS, Google Cloud Run, Fly.io, Railway)
handles restarts, scaling, and load-balancing for you. Adding Gunicorn is redundant — the cloud is your floor manager.

**Question 3 — Are you on a plain Linux server with no container platform?**
- Need maximum robustness → **Gunicorn + Uvicorn workers**
- Want simplicity → **Uvicorn `--workers N` managed by systemd**

### The Full Decision Tree

```
What kind of Python app are you running?
│
├─ Old-style app: Flask, classic Django, no async keyword
│   └─ ✅ Use Gunicorn with default sync workers.
│      Uvicorn cannot serve WSGI apps.
│
└─ Modern async app: FastAPI, Starlette, async Django
    │
    ├─ On Windows?
    │   └─ ✅ Uvicorn (--workers N works fine on Windows).
    │      Gunicorn is not an option — it cannot run on Windows.
    │
    ├─ In containers / Kubernetes / cloud platform?
    │   └─ ✅ Uvicorn, 1 worker per container.
    │      The platform handles restarts and scaling.
    │      Gunicorn would just add redundant complexity.
    │
    └─ On a plain Linux VPS / dedicated server?
        │
        ├─ Need crash recovery, hung-process killing,
        │  zero-downtime deploys, memory-leak recycling?
        │   └─ ✅ Gunicorn + Uvicorn workers.
        │      Most robust option for bare-server deployments.
        │
        └─ Want simplicity and good-enough reliability?
            └─ ✅ uvicorn --workers N managed by systemd.
               Simple, reliable, and sufficient for most teams.
```

### Quick Reference Table

| Your situation | What to use |
|---|---|
| Windows (any reason) | `uvicorn app.main:app --workers 2` |
| Docker / Kubernetes / Cloud containers | `uvicorn app.main:app` (1 worker — scale via replicas) |
| Linux VPS, simple setup | `uvicorn app.main:app --workers 4` (+ systemd) |
| Linux VPS, maximum reliability | `gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4` |
| Old Flask or Django app | `gunicorn app.main:app -w 4` (no Uvicorn needed) |

### How many workers should you run?

| Your workload | Recommended worker count |
|---|---|
| Mostly waiting (DB queries, API calls) — async app | 1–2 workers is usually enough |
| Mixed: some async, some CPU | Half to all of your CPU cores |
| CPU-heavy (ML, image processing, crypto) | = number of CPU cores |
| Kubernetes or containers | Always 1 per container — scale with replica count |
| Development / testing | 1 worker (much easier to debug) |
| Not sure? | Start with 2, watch CPU usage, add more if saturated |

> **Memory calculation:** If your app uses 300 MB of RAM and you run 8 workers: `8 × 300 MB = 2.4 GB`.
> Make sure your server has enough RAM before adding workers.

---

## Part 9 — Production Checklist in Plain English

When you are ready for real users, here is what you must have:

### Must-Haves

- [ ] **Put Nginx or a cloud load balancer in front** of Uvicorn/Gunicorn. Never expose the
  Python server directly to the internet. Nginx handles HTTPS (the padlock), slow connections,
  static files (images, CSS, JS), and acts as a security layer.

- [ ] **Add a `/health` endpoint** that returns `{"status": "ok"}`. Your load balancer will
  ping this every few seconds. If it stops responding, traffic stops going to that server.

- [ ] **Set timeouts.** If a request takes 60 seconds, something is broken. Kill it and
  return an error so the next request can be served.

- [ ] **Turn on structured logging.** Make sure every error is logged with enough detail
  to understand what went wrong later.

### For Gunicorn specifically

- [ ] `--max-requests 1000` — automatically recycles each worker after 1000 requests to keep
  memory clean and prevent slow leaks from growing forever.
- [ ] `--timeout 30` — kills any worker that doesn't respond within 30 seconds (stuck requests).
- [ ] `--workers` — set to roughly your number of CPU cores (or `(2 × cores) + 1` as a starting point).

### For Kubernetes/containers

- [ ] 1 Uvicorn worker per container. Scale by adding more container replicas, not by
  packing more workers into one container.
- [ ] Set memory limits per container so one runaway app can't crash everything.
- [ ] Give containers a graceful shutdown period (30+ seconds) so they can finish
  current requests before stopping.

### The Golden Rule of Async Python

> **Never put blocking code inside an `async def` function.**

These are blocking (BAD inside async):
```python
time.sleep(1)             # freezes the event loop for 1 second
requests.get("...")       # the `requests` library is synchronous
open("file.txt").read()   # regular file reads are blocking
```

If you must use a blocking library, run it in a thread pool so it doesn't freeze everything:
```python
import asyncio

# Run blocking code safely without freezing the event loop:
result = await asyncio.get_event_loop().run_in_executor(None, blocking_function, args)
```

Or, better: use async alternatives (`httpx` instead of `requests`, `aiofiles` instead of `open`).

---

## Part 10 — FAQ

**Q: Is Uvicorn better than Gunicorn?**

They do different things — comparing them directly is like asking "Is the waiter better
than the floor manager?" They have different jobs. Uvicorn handles HTTP connections.
Gunicorn manages worker processes. For async Python apps on a Linux server, the best setup
often uses both together. On containers/Kubernetes or Windows, Uvicorn alone is the right choice.

---

**Q: FastAPI's website says to use Uvicorn. Should I just do that?**

Yes, for most modern setups — especially containers and cloud deployments. FastAPI's official
documentation has shifted toward recommending Uvicorn directly. For a bare Linux server where
you need crash-restart and zero-downtime deploys, consider adding Gunicorn as the supervisor.

---

**Q: I am building a small side project. What is the simplest setup?**

```bash
uvicorn myapp.main:app --workers 2 --host 0.0.0.0 --port 8000
```
Run this with a systemd service on Linux or just keep it running in a terminal for development.
Add Gunicorn later if you outgrow this setup.

---

**Q: What is `uvicorn[standard]` vs just `uvicorn`?**

The `[standard]` version automatically installs the speed boosters (uvloop + httptools).
Always use this, especially on Linux where uvloop activates and gives 2–4× faster performance.

```bash
pip install "uvicorn[standard]"   # ← always prefer this
pip install uvicorn               # ← missing the speed boosters
```

---

**Q: Gunicorn says it failed on my Windows machine. Is something broken?**

Nothing is broken. Gunicorn **fundamentally cannot run on Windows** — it uses Unix-only
operating system features (`os.fork`, `fcntl`) that Windows simply does not have.
Use Uvicorn instead. It works great on Windows and supports `--workers`.

---

**Q: Are Gunicorn + Uvicorn workers faster than just Uvicorn `--workers`?**

No. They are identical in speed. Both use the same Uvicorn code to handle HTTP requests.
The only difference is in the supervisor (manager) layer — Gunicorn's supervisor has more
operational features, but both produce the same request-handling throughput.

---

**Q: What is a WebSocket? Do I need ASGI for it?**

A WebSocket is a permanent, live, two-way connection between a browser and a server —
like a phone call rather than exchanging letters. Used for: live chat, multiplayer games,
stock price tickers, AI response streaming (like ChatGPT typing out responses word by word).
Yes, you must use ASGI (Uvicorn) for WebSockets. WSGI/Gunicorn sync workers cannot do this.

---

**Q: What is the `uvicorn-worker` package I keep seeing mentioned?**

In newer versions of Uvicorn, the built-in Gunicorn worker class (`uvicorn.workers.UvicornWorker`)
was moved to a separate package. Install it with `pip install uvicorn-worker`, then:

```bash
gunicorn myapp.main:app -k uvicorn_worker.UvicornWorker -w 4
```

Check the Uvicorn release notes for your installed version to know which import to use.

---

**Q: I have a Flask app. Can I use Uvicorn?**

No. Flask is a WSGI framework, and Uvicorn is an ASGI server. They speak different protocols.
Use Gunicorn with Flask. Uvicorn is only for ASGI frameworks (FastAPI, Starlette, etc.).

---

## Glossary — Every Term, Explained Simply

| Term | Plain English |
|---|---|
| **API** | A way for programs to request services from other programs. Like a waiter between two kitchens. |
| **ASGI** | The modern (2019) shared rulebook for async Python web servers. Lets one worker handle many requests. |
| **async / await** | Python keywords meaning "do other things while I wait for this to finish". |
| **asyncio** | Python's built-in system for managing many async tasks simultaneously. |
| **Concurrency** | Handling many things at the "same time" by switching between them rapidly — one waiter, many tables. |
| **Container** | A packaged version of your app that includes everything it needs. Runs identically anywhere (e.g. Docker). |
| **CPU** | The computer's calculation engine. More cores = more things can compute in parallel. |
| **CPU-bound** | Work that keeps the CPU busy doing calculations (as opposed to waiting for network/database). |
| **Django** | A popular Python web framework. Supports both old WSGI and new ASGI modes. |
| **Event loop** | The mechanism that lets one process juggle many tasks. The brain of the smart waiter. |
| **FastAPI** | A modern, fast Python framework for building web APIs. Requires ASGI (Uvicorn). |
| **Flask** | A simple Python web framework. Uses WSGI. Runs with Gunicorn. |
| **GIL** | Global Interpreter Lock. Python's rule: only one piece of Python code runs at a time per process. |
| **Graceful reload** | Deploying new code without dropping any users' active connections. New workers start before old ones stop. |
| **Gunicorn** | Green Unicorn. A WSGI process manager/server (Linux/Mac only, since 2010). |
| **HTTP** | HyperText Transfer Protocol. The language browsers and web servers use to communicate. |
| **httptools** | A fast C-based HTTP message reader. Installed automatically with `uvicorn[standard]`. |
| **Hung worker** | A worker process that is stuck and not responding to any new requests. |
| **I/O-bound** | Work that mostly waits (for databases, APIs, files) rather than calculating. Async handles this well. |
| **Kubernetes (K8s)** | A system for running many containers at scale. Acts as the platform floor manager. |
| **Latency** | How long a single request takes to get a response. Lower is better. |
| **Memory leak** | When a program gradually uses more and more RAM without releasing it, growing until it crashes. |
| **Nginx** | A popular reverse proxy — a middleman server placed in front of your app to handle HTTPS, caching, etc. |
| **p50 / p95 / p99** | Percentiles for latency. "p95 = 500ms" means 95% of requests were faster than 500ms. |
| **PID** | Process ID. A number the OS gives to each running program. Used to identify which worker served a request. |
| **Process** | An independent running program with its own memory. Multiple processes = multiple CPU cores used. |
| **req/s** | Requests per second — how many requests the server handles every second. Higher is better. |
| **Reverse proxy** | A middleman server (like Nginx) that sits in front of your app to handle HTTPS, routing, and security. |
| **Starlette** | A lightweight ASGI Python framework. FastAPI is built on top of it. |
| **Synchronous** | One thing at a time, in order. Finish step 1 completely before starting step 2. |
| **systemd** | Linux's built-in tool for managing long-running services (auto-restart on crash, start on boot). |
| **Throughput** | Total requests handled per second. A measure of overall server capacity. |
| **TLS / HTTPS** | Encrypted web connections. The padlock icon in your browser. Handled by Nginx, not Uvicorn/Gunicorn. |
| **Uvicorn** | A fast ASGI web server for Python. Works on Windows, Mac, and Linux. |
| **uvloop** | A turbo-charged event loop for Python. 2–4× faster than asyncio. Linux and Mac only. |
| **WebSocket** | A live, permanent two-way connection between browser and server. For chat, games, streaming. |
| **Worker** | One independent process running your app. 4 workers = 4 processes = can use 4 CPU cores. |
| **WSGI** | Web Server Gateway Interface. The older (2003) synchronous standard for Python web servers. |

---

*For the full technical reference with architecture diagrams, the 15-parameter decision matrix,
complete benchmark data tables, and production configuration examples, see
[`FINAL_CONFLUENCE_PAGE.md`](FINAL_CONFLUENCE_PAGE.md) in this same folder.*

*To reproduce all the benchmarks on your own machine, follow the setup instructions in
[`README.md`](README.md).*
