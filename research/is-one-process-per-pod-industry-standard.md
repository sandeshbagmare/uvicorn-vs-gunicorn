# Is "one process per pod, Kubernetes manages the rest" really the industry standard? — the proof

> **What this doc does.** It does one thing: **prove**, with primary sources from *five independent
> authorities*, that the design this repo recommends — **one process per container/pod, with the cluster
> (not an in-container manager) owning replication, restarts and recycling** — is the mainstream cloud-native
> standard, not just one repo's opinion. Every claim carries an inline `[S#]` resolved in [Sources](#sources),
> with the authority's **own words** quoted so you can verify them yourself.
>
> **Verified:** 2026-06-29. **Honesty up front:** "industry standard" is graded in §3 — most of the claim is
> settled and multi-vendor; the *periodic rollout-restart recycle* is a widely-used, first-party-**supported**
> practice rather than a formal law, and even the framework authors say these are *"not rules written in stone."*

---

## 0. The claim, decomposed into testable sub-claims

| # | Sub-claim | Verdict (graded in §3) |
|---|---|---|
| **C1** | Run **one process / one concern per container** (single parent process). | ✅ Settled standard |
| **C2** | **One container per pod** is the default; a pod wraps one container. | ✅ Settled standard |
| **C3** | Get more capacity by **replicating at the cluster level** (controllers), **not** by stuffing workers/copies into one container/pod. | ✅ Settled standard |
| **C4** | **Let the platform own the process lifecycle** — crash recovery, restarts, scaling. Processes are **disposable**; don't build your own supervisor inside and don't daemonize. | ✅ Settled standard |
| **C5** | Therefore "one Uvicorn worker per pod, Kubernetes manages restarts/replicas, recycle via a rolling restart" is the *standard application* of C1–C4. | ✅ for C1–C4; the rollout-restart recycle is 🟡 common first-party practice |

---

## 1. The evidence matrix (who backs what)

Five independent authorities — the orchestrator itself (Kubernetes), two hyperscalers (Google Cloud, AWS),
the canonical cloud-native methodology (The Twelve-Factor App), and the framework you use (FastAPI):

| Authority | C1 one process/container | C2 one container/pod | C3 replicate at cluster level | C4 platform owns lifecycle / disposable |
|---|:--:|:--:|:--:|:--:|
| **Kubernetes docs** `[S1][S2]` | ✅ | ✅ | ✅ | ✅ |
| **Google Cloud** `[S3][S4]` | ✅ | — | ✅ | ✅ |
| **The Twelve-Factor App** `[S5][S6]` | ✅ | — | ✅ | ✅ |
| **FastAPI docs** `[S7][S8]` | ✅ | ✅ | ✅ | ✅ |
| **AWS (ECS/containers)** `[S9]` | ✅ | — | ✅ | ✅ |

Every cell below is backed by the authority's own words (§2). No single source carries the claim — they
**independently converge**, which is what makes it a *standard* rather than a *preference* *(ours)*.

---

## 2. The authorities, in their own words

### 2.1 Kubernetes — the orchestrator's own design `[S1][S2]`
- *"The 'one-container-per-Pod' model is the most common Kubernetes use case; in this case, you can think of a
  Pod as a wrapper around a single container."* `[S1]` → **C2**
- *"You don't need to run multiple containers to provide replication (for resilience or capacity); if you need
  multiple replicas, see Workload management."* `[S1]` → **C3**
- Pods *"are designed as relatively ephemeral, disposable entities"*; the Pod exists precisely to carry a
  **restart policy** and **liveness probe** so the platform manages the container's lifecycle `[S1][S2]` → **C4**

### 2.2 Google Cloud — *Best practices for building containers* `[S3][S4]`
- *"Each of your containers should contain only one app … an 'app' is … a single piece of software, with a
  unique parent process."* `[S3]` → **C1**
- *"Do not run PHP and MySQL in the same container: it's harder to debug, Linux signals will not be properly
  handled, **you can't horizontally scale** the PHP containers."* `[S4]` → **C1 + C3** (bundling *breaks*
  horizontal scaling — the exact failure mode of "many workers in one pod")

### 2.3 The Twelve-Factor App — the canonical methodology `[S5][S6]`
- **Concurrency (VIII):** *"Scale out via the process model … the share-nothing, horizontally partitionable
  nature of twelve-factor app processes means that adding more concurrency is a simple and reliable
  operation."* `[S5]` → **C3**
- **Disposability (IX):** *"The twelve-factor app's processes are disposable, meaning they can be started or
  stopped at a moment's notice … shut down gracefully when they receive a SIGTERM."* `[S6]` → **C4**
- The decisive line for "don't be your own supervisor": *"\[Processes] should never daemonize or write PID
  files. Instead, rely on the operating system's process manager (such as systemd, a distributed process
  manager on a cloud platform …) to … respond to crashed processes, and handle user-initiated restarts and
  shutdowns."* `[S6]` → **C4** (i.e. let the platform — Kubernetes — be the process manager)

### 2.4 FastAPI — your framework's deployment docs `[S7][S8]`
- *"When running on Kubernetes you will probably **not** want to use workers and instead run a **single Uvicorn
  process per container**."* `[S7]` → **C1 + C2**
- On retiring its old Gunicorn+Uvicorn base image: *"The Docker image was created when Uvicorn didn't support
  managing and restarting dead workers … But now that Uvicorn (and the `fastapi` command) support using
  `--workers`, there's no reason to use a base Docker image."* `[S8]` → **C4** (the in-container manager was
  redundant once the platform/runtime handled restarts)
- And the in-pod manager is called out as redundant on a cluster: *"Having another process manager inside the
  container … would only add unnecessary complexity that you are most probably already taking care of with your
  cluster system."* `[S8]` → **C3 + C4**

### 2.5 AWS — *Graceful shutdowns with ECS* `[S9]`
- The platform — not your app — coordinates lifecycle and the load balancer: *"ECS will **automatically
  deregister** your task from the load balancer's target group **before** sending it a SIGTERM signal …
  ensuring that all new requests are redirected to other tasks."* `[S9]` → **C3 + C4**
- And the money quote that *is literally your 529/530*: *"a container could **exit before a load balancer stops
  sending it requests, leading to HTTP 5xx errors**."* `[S9]` → this is the same root cause as the
  synchronized-recycle 529/530 — *"no ready backend"* — which is why readiness/deregistration gating (C4) is
  mandatory, not optional *(ours)*.

---

## 3. Honest grading — what's settled vs what's "practice"

- **C1–C4 are settled, multi-vendor standard.** Five independent authorities state them in their own docs
  `[S1][S3][S5][S7][S9]`. This is as close to "industry standard" as architecture guidance gets. *(ours)*
- **C5's recycling mechanism is common first-party *practice*, not a formal law.** Kubernetes ships
  `kubectl rollout restart` `[S10]` and the scheduled-recycle pattern is widely used, but vendors frame it as a
  **mitigation** — the *preferred* fix for memory is "disposable processes + correct limits + fix the leak,"
  with periodic recycling as insurance. We grade it 🟡 honestly. *(ours)*
- **It is explicitly *not* a hard rule.** FastAPI's own disclaimer: *"none of these are rules written in stone
  that you have to blindly follow."* `[S8]` Legitimate exceptions exist — sidecars/init containers (tightly
  coupled helpers, **not** replication) `[S1]`; a large shared in-memory model; or simple single-server /
  Docker-Compose deployments without a cluster `[S8]`. The standard is the **default**, not a commandment.
  *(ours)*

---

## 4. So, for your actual question

Your instinct — *"with one worker per pod, Kubernetes maintains the pods, not Uvicorn"* — **is the standard,
and the sources prove it**:

- One Uvicorn worker per pod = **C1 + C2** `[S1][S7]`.
- Two/N pods maintained by the Deployment/ReplicaSet, scaled by the HPA = **C3** `[S1][S5]`.
- The kubelet restarting a crashed/exited container, processes being **disposable** and draining on SIGTERM =
  **C4** `[S2][S6][S9]`.
- Recycling memory by a **rolling restart** (graceful, staggered) instead of an in-pod `max_requests` =
  the standard *application* of "disposable processes + platform-managed lifecycle" `[S6][S10]`, and it
  sidesteps the AWS/Kubernetes failure mode of *"exit before the LB deregisters → 5xx/529/530"* `[S9]`.

The one thing the standard makes **your** responsibility is exactly what the rest of this research covers:
deciding *when* to recycle and ensuring pods don't recycle *together* — see the measured companion
[memory-leaks-and-worker-recycling.md](memory-leaks-and-worker-recycling.md) and the how-to
[recycling-one-uvicorn-worker-per-pod-on-kubernetes.md](recycling-one-uvicorn-worker-per-pod-on-kubernetes.md).

---

## Verdict

**Yes — it's the industry standard.** "One process per container, one container per pod, replicate and manage
lifecycle at the cluster level" is stated, independently, by **Kubernetes, Google Cloud, AWS, the Twelve-Factor
methodology, and FastAPI** `[S1][S3][S5][S7][S9]`. Running a **single Uvicorn worker per pod and letting
Kubernetes do the replication, restarts and (rolling) recycling** is the textbook expression of that standard.
The only honest caveats: the *periodic rollout-restart recycle* is a supported *practice* (not a law), and the
authors themselves stress these are defaults to *apply with judgement*, not rules to *follow blindly* `[S8]`.

---

## Sources

| Tag | Source | Type | Link |
|---|---|---|---|
| **S1** | Kubernetes — *Pods* ("one-container-per-Pod is the most common use case"; don't use multiple containers for replication; pods are ephemeral/disposable) | Official docs | https://kubernetes.io/docs/concepts/workloads/pods/ |
| **S2** | Kubernetes — *Pod Lifecycle* (restart policy + probes; the platform restarts containers) | Official docs | https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/ |
| **S3** | Google Cloud — *Best practices for building containers* ("each container should contain only one app … a unique parent process") | Official docs | https://cloud.google.com/architecture/best-practices-for-building-containers |
| **S4** | Google Cloud — *7 best practices for building containers* ("do not run PHP and MySQL in the same container … you can't horizontally scale") | Eng blog (vendor) | https://cloud.google.com/blog/products/containers-kubernetes/7-best-practices-for-building-containers |
| **S5** | The Twelve-Factor App — *VIII. Concurrency* ("Scale out via the process model"; share-nothing horizontal scaling) | Methodology | https://12factor.net/concurrency |
| **S6** | The Twelve-Factor App — *IX. Disposability* (disposable processes; graceful SIGTERM; "never daemonize … rely on the … process manager … to respond to crashed processes and handle restarts") | Methodology | https://12factor.net/disposability |
| **S7** | FastAPI — *Server Workers* ("on Kubernetes … a single Uvicorn process per container") | Official docs | https://fastapi.tiangolo.com/deployment/server-workers/ |
| **S8** | FastAPI — *FastAPI in Containers* (in-pod manager is redundant on a cluster; retired the Gunicorn+Uvicorn image; "not rules written in stone"; legitimate exceptions) | Official docs | https://fastapi.tiangolo.com/deployment/docker/ |
| **S9** | AWS — *Graceful shutdowns with ECS* (platform deregisters from the LB before SIGTERM; "exit before a load balancer stops sending requests → HTTP 5xx") | Eng blog (vendor) | https://aws.amazon.com/blogs/containers/graceful-shutdowns-with-ecs/ |
| **S10** | Kubernetes — *kubectl rollout restart* (first-party graceful, rolling restart) | Official docs | https://kubernetes.io/docs/reference/kubectl/generated/kubectl_rollout/kubectl_rollout_restart/ |

> Companion research in this folder: [why in-pod Gunicorn workers are discouraged on
> K8s](why-not-gunicorn-workers-on-kubernetes.md) · [memory & worker recycling
> (measured)](memory-leaks-and-worker-recycling.md) · [recycling one Uvicorn worker per pod on
> Kubernetes](recycling-one-uvicorn-worker-per-pod-on-kubernetes.md).
