# Why "Gunicorn workers inside the container" is discouraged on Kubernetes

> **Format note (important):** Per request, **every factual line carries an inline source** in the form
> `[S#]`, resolved in the [Sources](#sources) list at the bottom. Click the number → go to the exact page
> and verify the claim yourself. Lines without a tag are our own synthesis/illustration, labelled *(ours)*.
>
> **Scope:** This is about running a **process manager (Gunicorn, or `uvicorn --workers`) with multiple
> workers *inside one container/pod*** on Kubernetes — and why the prevailing guidance is "don't; run one
> process per container and replicate at the cluster level instead," plus the real caveats to that rule.
>
> **Verified:** 2026-06-24.

---

## 1. The headline guidance (from the framework authors)

- FastAPI's official deployment docs state: *"If you have a cluster of machines with Kubernetes, Docker Swarm Mode, Nomad, or another similar complex system to manage distributed containers on multiple machines, then you will probably want to handle replication at the cluster level instead of using a process manager (like Uvicorn with workers) in each container."* [S1]
- The same docs: *"In those cases, you would probably want to build a Docker image from scratch … and running a single Uvicorn process instead of using multiple Uvicorn workers."* [S1]
- And explicitly: *"you would not want to have multiple workers in the container, for example with the `--workers` command line option. You would want to have just a single Uvicorn process per container (but probably multiple containers)."* [S1]
- The stated reason: *"Having another process manager inside the container (as would be with multiple workers) would only add unnecessary complexity that you are most probably already taking care of with your cluster system."* [S1]
- FastAPI's Server Workers page repeats it: *"when running on Kubernetes you will probably not want to use workers and instead run a single Uvicorn process per container."* [S2]
- Historical note from the same docs (why the old Gunicorn+Uvicorn base image was retired): *"The Docker image was created when Uvicorn didn't support managing and restarting dead workers, so it was needed to use Gunicorn with Uvicorn … But now that Uvicorn (and the `fastapi` command) support using `--workers`, there's no reason to use a base Docker image instead of building your own."* [S1]

> *(ours)* So the framework's position is not "Gunicorn is bad" — it is "a second process manager **inside** the pod is redundant on Kubernetes, because Kubernetes already does replication/restarts at the pod level." The same logic applies to `uvicorn --workers`; Gunicorn is just the most common in-pod manager.

---

## 2. Reason 1 — Kubernetes manages pods, not the workers inside them

- A Pod is the smallest deployable unit Kubernetes creates and manages. [S3]
- The Horizontal Pod Autoscaler scales the **number of pods** (replicas) based on metrics — it does not see or scale worker processes inside a container. [S4]
- Liveness/readiness/startup probes operate against the **container** (one endpoint), not against each worker process. [S5]

> *(ours)* Consequence: if you put 4 Gunicorn workers in one pod, Kubernetes still sees **one** unit. It cannot independently restart, reschedule, or health-check an individual worker — Gunicorn does that, invisibly to Kubernetes. You have two managers stacked, and the outer one (Kubernetes) is blind to the inner one's workers.

---

## 3. Reason 2 — multiple workers per pod breaks the autoscaler's metrics

- A team running FastAPI/Pyramid found Gunicorn's load-balancing across multiple worker processes was "confusing the Kubernetes Metrics Server API," because a single Pod had 4 workers whose combined resource use *"would vary greatly according to the types of operations it was handling at the same time."* [S6]
- After switching to **a single Gunicorn worker per Pod**, they *"saw immediate positive results … their HPA started doing its job correctly,"* with *"fewer memory spikes, with each Pod staying close to its average resource usage."* [S6]
- The general principle they drew: *"when using WSGI tools like Gunicorn, be aware of their internal load-balancing features. These can confuse the metrics-server and lead to incorrect scaling decisions."* [S6]

> *(ours)* HPA scales on **average utilisation per pod**. A multi-worker pod's CPU/memory is lumpy (one worker doing a heavy request, three idle), so the per-pod average is noisy → the autoscaler makes poor decisions. One process per pod gives a clean, predictable per-pod signal.

---

## 4. Reason 3 — workers split the container's resources → surprise OOM kills

- One engineer found through testing that *"each worker split its container resources evenly among the group,"* which meant *"if one worker hit its max capacity in a spike of utilization, that would be enough to trigger an OOM kill without utilizing the container's full resources."* [S6]
- After moving to a single worker per container, they *"immediately saw the resource utilization of the container increase."* [S6]
- A separate Docker/Gunicorn guide warns that *"using multiple workers per container also runs the risk of OOM SIGKILLs without logging, making diagnosis of issues much more difficult."* [S7]
- Kubernetes memory is non-compressible: a container exceeding its memory limit is OOM-killed (whereas CPU is throttled, not killed). [S8]

> *(ours)* With multiple workers sharing one container memory limit, a single worker's spike can blow the **whole pod's** limit and trigger an OOM kill that takes down all the workers at once — a larger blast radius than one-process-per-pod, and harder to diagnose.

---

## 5. Reason 4 — health checks become unreliable with multiple workers

- With multiple Gunicorn workers behind one port, a probe request is answered by *whichever* worker picks it up — so a hung worker can go undetected because a healthy worker answers the probe. [S9]
- The inverse failure is also documented: when real requests are queued waiting on a slow dependency, *"the healthcheck request gets queued and ends up taking too long, and Kubernetes marks it as failed,"* and *"simply increasing the worker count might put you back in the same situation tomorrow."* [S9]
- A field report: with a single worker, *"any time a synchronous task was still running, the master process might be accumulating a backlog of other requests, including health checks … there was no way to handle simple requests to a given container."* [S6]

> *(ours)* So multi-worker pods make the probe ambiguous (it doesn't reflect any *specific* worker's health), while a single *synchronous* worker can starve the probe. Neither is ideal — the resolution is in §7 (probe split + per-worker `--timeout`).

---

## 6. The counter-arguments (this is a trade-off, not a commandment)

- The single-worker recommendation has a real downside — the heartbeat problem: *"many of these systems include a heartbeat mechanism that checks whether your server is alive … If you only have one worker, and it's stuck handling a slow query, the heartbeat query will timeout."* [S7]
- So one widely-cited recommendation is **not** exactly one worker, but a small number plus threads: *"start at least two workers, and probably also start a number of threads using the gthread worker backend,"* so *"each worker process can handle multiple queries so long as some of its time is spent waiting."* [S7]
- The same source still rejects *many* workers: *"you should only use one or two workers per container, otherwise you're not properly using the resources allocated to your application."* [S7]
- FastAPI itself lists legitimate exceptions: *"there are special cases where you could want to have a container with several Uvicorn worker processes inside"* — e.g. a *"simple enough"* app on a single server, or a Docker Compose deployment without cluster-level load balancing. [S1]
- And FastAPI's explicit disclaimer: *"none of these are rules written in stone that you have to blindly follow. You can use these ideas to evaluate your own use case and decide what is the best approach."* [S1]

> *(ours)* Net: "one process per pod" is the **default** for Kubernetes, not an absolute law. A 1–2-worker (or worker+threads) pod is a reasonable middle ground when you need a single pod to keep answering probes during a slow request, or to save memory via shared in-pod state.

---

## 7. What to do instead (the recommended pattern)

- Run **one process per container**, replicate with pods, and scale with the HPA — the framework's stated default for clusters. [S1][S2]
- One process per container gives *"a more or less well-defined, stable, and limited amount of memory consumed by each of those containers"* — exactly what makes resource limits and HPA behave. [S1]
- You still use all the cores: *"They would all be identical containers, running the same thing, but each with its own process … That way you would take advantage of parallelization in different cores of the CPU, or even in different machines."* [S1]
- For health checks, **split liveness and readiness**: under a strict back-end dependency, *"the liveness probe passes when the app itself is healthy, but the readiness probe additionally checks that each required back-end service is available."* [S5]
- Keep **liveness conservative**: a liveness probe should fail *only* when a restart is the cure — under overload, *"the liveness probe should pass … because if it fails, k8s will restart the pod, which does not actually help with the problem that there is more work to do than the workers can handle."* [S9]
- Recover individual hung workers with Gunicorn's own `--timeout` (the master kills and replaces a worker that exceeds it) rather than relying on a Kubernetes probe to catch it. [S9][S10]
- Use a **startup probe** so slow initialisation doesn't trip liveness: *"if a startup probe is configured, Kubernetes does not execute liveness or readiness probes until the startup probe succeeds."* [S5]

> *(ours)* In short: let Kubernetes be the manager (pods + HPA + probes), use one process per pod by default, and — if you do run a few workers in a pod — keep Gunicorn's `--timeout`/`--max-requests` for in-pod hygiene the cluster can't provide.

---

## 8. When running Gunicorn workers in a pod IS justified

- **Large shared in-memory asset** (model/cache): Gunicorn's `--preload` loads the app in the master before forking, so workers share read-only memory via copy-on-write. [S10] Loading once and sharing beats N full copies across N thin pods. *(ours)*
- **Keeping the pod responsive to probes during a slow request**: ≥2 workers (or worker+threads) so one busy worker doesn't starve health checks. [S7]
- **Simple/single-server or Docker Compose deployments** (not a cluster): FastAPI explicitly endorses an in-pod process manager here. [S1]
- **In-pod robustness you want regardless**: `--timeout` to kill hung workers and `--max-requests` to recycle and bound memory leaks. [S10]

> *(ours)* Even then: keep several pods (don't collapse to one node-filling pod), so you retain cross-node HA and clean rolling deploys — see the repo's [reference §14](../FINAL_CONFLUENCE_PAGE.md#14-kubernetes-on-powerful-multi-core-nodes-pods-vs-workers).

---

## 9. One-paragraph answer

*(ours, synthesising the cited sources above)* On Kubernetes, the cluster already provides replication,
restarts, health-checking and autoscaling **at the pod level** [S3][S4][S5], so adding a second process
manager with many workers **inside** the pod is redundant and actively harmful: it confuses the HPA's
per-pod metrics [S6], makes one worker's spike able to OOM-kill the whole pod [S6][S7][S8], and renders
health probes ambiguous [S9] — which is why FastAPI's docs say to run **one process per container and
replicate at the cluster level** [S1][S2]. The honest caveat is that exactly one *synchronous* worker can
fail to answer health checks during a slow request [S7][S9], so a 1–2-worker (or worker+threads) pod is a
defensible middle ground [S7], and Gunicorn-in-a-pod is genuinely justified for shared-memory models,
simple/single-server setups, or when you want `--timeout`/`--max-requests` hygiene [S1][S10] — none of
which are absolute rules [S1].

---

## Sources

| Tag | Source | Type | Link |
|---|---|---|---|
| **S1** | FastAPI — *FastAPI in Containers - Docker* (replication at cluster level; one process per container; special cases; "not rules written in stone") | Official docs | https://fastapi.tiangolo.com/deployment/docker/ |
| **S2** | FastAPI — *Server Workers* ("on Kubernetes you will probably not want to use workers … single Uvicorn process per container") | Official docs | https://fastapi.tiangolo.com/deployment/server-workers/ |
| **S3** | Kubernetes — *Pods* (the Pod is the smallest deployable/manageable unit) | Official docs | https://kubernetes.io/docs/concepts/workloads/pods/ |
| **S4** | Kubernetes — *Horizontal Pod Autoscaling* (scales replica count on metrics) | Official docs | https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/ |
| **S5** | Kubernetes — *Configure Liveness, Readiness and Startup Probes* (probe-per-container; liveness/readiness split; startup probe gating) | Official docs | https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/ |
| **S6** | DEV / "Tailored Autoscaling in Kubernetes" (field report: HPA confused by per-pod worker variance; resource-splitting OOM; single worker per pod fixed it) | Eng blog | https://dev.to/check/from-chaos-to-control-the-importance-of-tailored-autoscaling-in-kubernetes-2kpn |
| **S7** | Hynek Schlawack — *Configuring Gunicorn for Docker* (pythonspeed.com): one-or-two workers, heartbeat/health-check problem, gthread threads, OOM-without-logging risk | Eng blog | https://pythonspeed.com/articles/gunicorn-in-docker/ |
| **S8** | Kubernetes — *Resource Management for Pods and Containers* (memory non-compressible → OOM kill; CPU compressible → throttle) | Official docs | https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/ |
| **S9** | Gunicorn issue #2467 — *Kubernetes — dedicated worker for healthcheck?* (probe answered by any worker; liveness should pass under load; use `--timeout`) | Maintainer issue | https://github.com/benoitc/gunicorn/issues/2467 |
| **S10** | Gunicorn — *Settings* (`timeout`, `max-requests`) & *Design* (`preload_app`, pre-fork) | Official docs | https://docs.gunicorn.org/en/stable/settings.html · https://docs.gunicorn.org/en/stable/design.html |

> Every `[S#]` above links to a first-party doc, a maintainer issue, or a clearly-labelled engineering
> blog. Where two sources agree (e.g. the single-worker OOM behaviour [S6][S7]), both are cited so you can
> cross-check. Eng-blog claims are field reports / opinion, not specifications — weigh them accordingly.
