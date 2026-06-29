# Recycling one Uvicorn worker per pod — the Kubernetes way (no Gunicorn)

> **Format note (matches this repo):** factual lines carry an inline `[S#]` source, resolved in
> [Sources](#sources). Lines marked *(ours)* are our synthesis; *(measured)* are results from this repo's
> scripts (see the companion [memory-leaks-and-worker-recycling.md](memory-leaks-and-worker-recycling.md)).
>
> **The exact question this answers.** *"The official (and your) recommendation is **one worker per pod**. In
> that case Uvicorn isn't maintaining multiple pods — **Kubernetes** is, at the cluster level, right? So when
> I'm **not using Gunicorn**, just a **single Uvicorn worker per pod**, how do I get `max_requests`-style
> memory recycling using **Kubernetes**?"*
>
> **Short answer.** Yes — with one process per pod, **Kubernetes _is_ the process manager** that Gunicorn's
> master would have been. You don't reproduce `max_requests` inside the pod; you move it **up a level**. The
> clean, native form is a **scheduled rolling restart** (`kubectl rollout restart`), which recycles memory
> gracefully and — unlike a self-exiting worker — **without tripping CrashLoopBackOff**. Details below.
>
> **Verified:** 2026-06-29.

---

## TL;DR

1. **Uvicorn (1 worker) manages nothing but its own process.** "Multiple pods" is the Deployment's
   `replicas`, maintained by the ReplicaSet controller; a crashed/exited container is restarted by the
   **kubelet**; a dead pod/node is rescheduled by the controller `[S1][S3][S8]`. That is the whole point of
   "one process per container, replicate at the cluster level" `[S1][S2]`.
2. **The Gunicorn→Kubernetes translation:** master → **kubelet**; `max_requests` (recycle a worker) →
   **`kubectl rollout restart`** (recycle pods); `max_requests_jitter` (de-sync) → a rolling restart is
   **inherently staggered** via `maxUnavailable`/`maxSurge` `[S4][S5]` *(ours)*.
3. **You _can_ recycle by self-exiting (`uvicorn --limit-max-requests`) — Kubernetes restarts the container
   even on a clean exit 0** (`restartPolicy: Always`, the default) `[S3]`. **But it's a trap:** the kubelet
   can't tell a deliberate recycle from a crash, so frequent exits accrue **CrashLoopBackOff** (10s→20s→…→300s,
   resetting only after **10 min** of clean running) `[S3]`. Recycle more often than ~10 min and your pod
   spends escalating minutes *down*.
4. **Uvicorn has no jitter flag** `[S9]` *(measured: `uvicorn --help`)*, so naive per-pod `--limit-max-requests`
   **synchronizes** all pods → all recycle together → no ready endpoint → **529/530** *(measured A/B in the
   [companion doc](memory-leaks-and-worker-recycling.md): synchronized p99 **1515 ms** vs jittered **27.9 ms**)*.
5. **Recommended for one-worker-per-pod, no Gunicorn:** right-size the **memory limit** (since LangGraph +
   PostgresSaver doesn't actually leak — *measured*), run **≥3 replicas** with a **readiness probe**, and add a
   **CronJob → `kubectl rollout restart`** as the cluster-level "max_requests." Full manifest:
   [`manifests/k8s-uvicorn-1worker-recycle.yaml`](manifests/k8s-uvicorn-1worker-recycle.yaml).

---

## 1. Who maintains what (your intuition, made precise)

You're exactly right: **Uvicorn does not maintain multiple pods.** It runs **one process**. Everything around
that is Kubernetes. Here is the full division of labour *(ours, grounded in the cited docs)*:

| Concern | With Gunicorn-in-pod | With **one Uvicorn worker per pod** |
|---|---|---|
| Run the request handler | 1 of N Gunicorn workers | the single Uvicorn process `[S1]` |
| Keep N **workers** alive | Gunicorn **master** respawns a dead worker | **kubelet** restarts the dead **container** (`restartPolicy: Always`) `[S3]` |
| Keep N **replicas** alive | — (one pod) | **ReplicaSet controller** keeps `replicas` pods; reschedules a lost pod/node `[S8]` |
| Scale out | add workers (`-w`) | **HPA** scales the **pod count** `[S11]` |
| Load-balance | Gunicorn across workers | **Service/kube-proxy** across pods `[S1]` |
| Health-check | — | **readiness/liveness/startup probes** per container `[S6]` |
| Recycle for memory | `max_requests` (+ `jitter`) | **a rolling restart** (§3) — or a self-exit the kubelet restarts (§4) |

> *(ours)* So "multiple pods maintained by Kubernetes, not Uvicorn" is **correct and is the intended design**
> `[S1][S2]`. The job left to you is the one thing the cluster doesn't do automatically: decide **when** to
> recycle a pod for memory, and make sure pods don't all recycle **at the same instant**.

---

## 2. The Gunicorn→Kubernetes translation table

Everything `max_requests` gave you has a cluster-level equivalent — you don't lose it by dropping Gunicorn,
you **relocate** it *(ours)*:

| Gunicorn knob | What it did | Kubernetes equivalent (one worker per pod) |
|---|---|---|
| **master process** | survived workers, held the socket, respawned them | **kubelet** (per-container restarts) + **ReplicaSet** (per-pod) `[S3][S8]` |
| **`--max-requests N`** | kill a worker after N requests → OS reclaims its RAM | **`kubectl rollout restart`** on a schedule (recycle pods) `[S5]`; *or* `uvicorn --limit-max-requests N` self-exit (kubelet restarts — but see §4) `[S3][S9]` |
| **`--max-requests-jitter J`** | stagger restarts so workers don't all die together | a **rolling** restart is staggered by design (`maxUnavailable: 0`, `maxSurge: 1`) `[S4]`; *or* per-pod jitter in the entrypoint (§5) |
| **`--graceful-timeout`** | let the dying worker finish in-flight requests | **`preStop` hook + `terminationGracePeriodSeconds`** (SIGTERM → drain → exit) `[S6]` |
| **`--timeout`** (kill hung worker) | master kills a stuck worker | **liveness probe** restarts a genuinely-stuck pod (keep it conservative) `[S6]` |

---

## 3. The recommended path: a scheduled **rolling restart** (the native `max_requests`)

There is **no built-in "restart after N requests"** in Kubernetes — the HPA scales replica count and probes
restart on *health*, not on a request counter `[S11][S6]`. The idiomatic substitute is a **time-based rolling
restart**, which is the truest "maintained by Kubernetes" recycle *(ours)*:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata: { name: uvicorn-slim-recycle }
spec:
  schedule: "17 */8 * * *"            # every 8h, off the :00 mark so it doesn't herd with other jobs
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          serviceAccountName: uvicorn-slim-recycler     # RBAC: get/list/watch/patch on the deployment
          restartPolicy: Never
          containers:
            - name: kubectl
              image: bitnami/kubectl
              command: ["/bin/sh","-c","kubectl rollout restart deployment/uvicorn-slim && kubectl rollout status deployment/uvicorn-slim --timeout=540s"]
```

Why this is the right tool for one-worker-per-pod *(ours)*:

- **It's graceful and rolling.** `kubectl rollout restart` replaces pods gradually, honouring
  `maxUnavailable: 0` / `maxSurge: 1`, your readiness probe, and your PodDisruptionBudget — so capacity never
  dips and traffic drains off each pod before it stops `[S4][S5][S10]`.
- **It's staggered for free.** Because pods are replaced one-ish at a time, you get Gunicorn-`jitter`
  behaviour **without** a jitter flag — the synchronized-restart 529/530 cannot happen `[S4]` *(ours)*.
- **It does _not_ trip CrashLoopBackOff.** A rollout is an *intentional* replacement, not a container crash, so
  the kubelet's restart-backoff (§4) never enters the picture `[S3]` *(ours)*.
- **No app change, no Gunicorn.** Your pod stays a pure single Uvicorn process `[S1]`.

Pick the schedule from how fast memory actually grows (measure with this repo's probe — §6). If it grows
slowly (or, as we measured, **plateaus**), recycling every 8–24 h is plenty; if you have a real leak, recycle
more often *and fix the leak* `[S3]`.

---

## 4. The trap with `uvicorn --limit-max-requests` on one worker per pod: **CrashLoopBackOff**

It's tempting to just set `uvicorn --limit-max-requests N` and let the worker exit. Mechanically it works —
**`restartPolicy: Always` (the Deployment default) restarts the container even on a clean exit 0** `[S3]`
*(measured: a single Uvicorn worker prints "Maximum request limit exceeded. Terminating process." and exits)*.
So far, so good: the kubelet is your respawner.

**The catch the docs are explicit about:** the kubelet *can't distinguish a deliberate recycle from a crash*.
Every container exit feeds the **restart backoff** `[S3]`:

```
restart #1 → wait 10s   restart #4 → wait 80s
restart #2 → wait 20s   restart #5 → wait 160s
restart #3 → wait 40s   restart #6+ → wait 300s (5-min cap)
        … the timer only RESETS after the container runs 10 minutes clean …
```

> *(ours)* **Consequence:** if your worker recycles *more often than ~10 minutes*, each recycle is treated as
> "another crash," the backoff climbs, the pod is reported **CrashLoopBackOff**, and it spends escalating
> minutes **not running** — the opposite of what you wanted. To use `--limit-max-requests` safely on one
> worker per pod you must size **N so that `N ÷ (your req/s)` ≫ 10 min**, ideally a few times that. That
> fragility is the core reason a **scheduled rolling restart (§3) is the better recycle** for this topology —
> Kubernetes understands a rollout as intentional, but a self-exit as a crash `[S3]`.

If you still want request-count recycling in-pod, treat it as a **backstop sized to fire rarely**, and pair it
with everything in §5.

---

## 5. The synchronization problem (and three ways to de-sync with one worker per pod)

Uvicorn has **no `--limit-max-requests-jitter`** (jitter is Gunicorn-only) `[S9]` *(measured)*. So if every
pod runs the same `--limit-max-requests N` under even load, **every pod hits N at nearly the same moment and
recycles together** → for a beat there's **no ready backend** → the ingress returns **529/530**. We measured
exactly this: synchronized recycling drove **p99 to 1,515 ms (max ~5 s)**; staggering it held **p99 at 27.9 ms**
— same number of recycles, one variable changed *(measured — [companion doc §3](memory-leaks-and-worker-recycling.md))*.

With one worker per pod and no Gunicorn, you de-synchronize one of three ways *(ours)*:

1. **Use a rolling restart (§3).** Staggered by construction — the simplest and best. No per-pod config.
2. **Per-pod jitter in the entrypoint.** Randomize `N` per pod so they drift apart. `$RANDOM` is bash-only;
   use Python (already in your image) for portability:
   ```sh
   N=$(python -c "import random;print(40000+random.randint(0,8000))")
   exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --limit-max-requests "$N"
   ```
3. **App-level jitter.** Each worker picks a randomized recycle target at boot and `os._exit(0)`s when it's
   reached; the kubelet restarts the container. (This repo's [`recycle_app.py`](scripts/recycle_app.py) does
   exactly this — *measured: the supervisor/kubelet respawns a self-exited worker*.) Same 10-min caveat as §4.

In all three, also: **run ≥3 replicas** (one recycling pod is then ≤33% of capacity, not 50%) and **keep a
readiness probe** so a (re)starting pod is pulled from the Service endpoints automatically `[S4][S6]`.

---

## 6. Do you even need to recycle? Measure first

This repo **measured** a real LangGraph + PostgresSaver app over thousands of `invoke`s: process RSS rose a
few MB and then **plateaued** — no unbounded leak from the framework or the checkpointer *(measured —
[companion doc §1](memory-leaks-and-worker-recycling.md))*. If your app behaves the same, the correct primary
fix is **not** recycling at all — it's **a correctly-sized memory limit** so the OOM killer never fires
`[S7]`, with a rolling-restart CronJob as cheap insurance.

```bash
# characterise YOUR app's growth before choosing a recycle cadence:
set LG_PG_URI=postgresql://user:pass@host:5432/yourdb
python research/scripts/langgraph_memory_probe.py --config shared-pool --requests 5000
#  plateaus  -> just size limits.memory; recycle rarely (or never)
#  climbs    -> fix the leak (your node code / an implicit cache); recycle is only a band-aid
```

---

## 7. The complete pattern (copy-paste)

[`manifests/k8s-uvicorn-1worker-recycle.yaml`](manifests/k8s-uvicorn-1worker-recycle.yaml) ties it together —
**one Uvicorn worker per pod, zero Gunicorn**:

- **Deployment**: `command: ["uvicorn","app.main:app",...]` (one process), `replicas: 3`,
  `strategy.rollingUpdate` `maxUnavailable: 0` / `maxSurge: 1`, `requests/limits.memory` sized from the
  plateau, **readiness + liveness + startup** probes, `preStop` drain, topology spread `[S1][S4][S6][S7]`.
- **Service** (ClusterIP) + **HPA** (scales pods on CPU) + **PodDisruptionBudget** (`minAvailable: 50%`)
  `[S11][S10]`.
- **CronJob → `kubectl rollout restart`** + its **ServiceAccount/Role/RoleBinding** (get/list/watch/patch the
  Deployment) — the cluster-level, CrashLoopBackOff-free, auto-staggered `max_requests` `[S5]`.

> *(ours)* **One-line takeaway:** with one Uvicorn worker per pod, you don't rebuild Gunicorn's master inside
> the pod — **you let Kubernetes be the master**: the kubelet respawns the container, the ReplicaSet keeps the
> replicas, and a **scheduled rolling restart** is your `max_requests` (graceful, staggered, no
> CrashLoopBackOff). Keep `--limit-max-requests` only as a rarely-firing backstop, with per-pod jitter and a
> readiness probe so the inevitable recycle is invisible.

---

## Sources

| Tag | Source | Type | Link |
|---|---|---|---|
| **S1** | FastAPI — *FastAPI in Containers* (one process per container; replicate at the cluster level; Uvicorn now manages/restarts its own workers) | Official docs | https://fastapi.tiangolo.com/deployment/docker/ |
| **S2** | FastAPI — *Server Workers* ("on Kubernetes … a single Uvicorn process per container") | Official docs | https://fastapi.tiangolo.com/deployment/server-workers/ |
| **S3** | Kubernetes — *Pod Lifecycle* (`restartPolicy: Always` restarts on any exit incl. 0; kubelet restart backoff 10s→300s; **resets after 10 min** clean) | Official docs | https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/ |
| **S4** | Kubernetes — *Deployments / Rolling Update* (`maxUnavailable`, `maxSurge`; staggered replacement) | Official docs | https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#rolling-update-deployment |
| **S5** | Kubernetes — *kubectl rollout restart* (graceful rolling restart of a Deployment) | Official docs | https://kubernetes.io/docs/reference/kubectl/generated/kubectl_rollout/kubectl_rollout_restart/ |
| **S6** | Kubernetes — *Configure Liveness, Readiness & Startup Probes* (readiness gates Service endpoints; startup gating; keep liveness conservative) | Official docs | https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/ |
| **S7** | Kubernetes — *Resource Management for Pods and Containers* (memory non-compressible → over-limit = OOMKilled) | Official docs | https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/ |
| **S8** | Kubernetes — *ReplicaSet* (controller maintains the desired number of pod replicas) | Official docs | https://kubernetes.io/docs/concepts/workloads/controllers/replicaset/ |
| **S9** | Uvicorn — *Settings* (`--limit-max-requests` "terminating the process"; **no** jitter flag) | Official docs | https://www.uvicorn.org/settings/ |
| **S10** | Kubernetes — *Disruptions / PodDisruptionBudget* | Official docs | https://kubernetes.io/docs/concepts/workloads/pods/disruptions/ |
| **S11** | Kubernetes — *Horizontal Pod Autoscaling* (scales the replica count) | Official docs | https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/ |

> Companion in this folder: the **measured** study [memory-leaks-and-worker-recycling.md](memory-leaks-and-worker-recycling.md)
> (does LangGraph+PostgresSaver leak? how `max_requests` frees memory? the synchronized-restart A/B), and
> [why in-pod Gunicorn workers are discouraged on K8s](why-not-gunicorn-workers-on-kubernetes.md).
