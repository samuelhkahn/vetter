1) We added a test job and a build job
    - the test job tests first installs uv and then confirms that your tests are properly linted, formatted, and unit tests pass with ruff (on the VM)
    - the build job builds the dockerfile with Dockerfile and then runs the container such that it exectures a python commadn
2) we added to CI checks one for test and one for build
    - we added them each to the rulset and now it is required that they both pass for any PR to get merged 
    - the CI runner will never show cached like it does on your local machine because it is spinning up a new VM each time to build and run the docker container
3) Dockerfile
    - the uv syncs are separated because dependencies are installed from the lockfile before the source is copied, so a change to src/ invalidates only the layers below the copy and the dependency install stays cached. Then the second uv sync only installs vetter itself. Name the mechanism: layer caching, top down invalidation.
## Task 04: compose services

**Volumes.** A named volume is persistent storage that Docker manages on the host, outside the container's filesystem. The container mounts it at a path (`/var/lib/postgresql/data`), so the container can be destroyed and recreated and the data is still there. That is why the test table survived `docker compose down` then `up`. Plain `down` removes containers and the network but leaves volumes; `down -v` removes volumes too. Renaming the volume in the compose file does not delete the old one; it just stops mounting it, and the old one sits on disk until you remove it by hand.

**Two Redpanda listeners.** A Kafka broker does not just accept connections; on first contact it tells the client which address to use for everything after. A container on the compose network can resolve `redpanda` but not `localhost` (which means itself); my Mac can resolve `localhost` but not `redpanda`. So the broker runs two listeners, 9092 advertising `redpanda:9092` for containers and 19092 advertising `localhost:19092` for the Mac, and only 19092 is published to the host. A worker container connects to `redpanda:9092`; my consumer test on the Mac connects to `localhost:19092`.

**Postgres is the store, Redpanda is the queue.** Postgres is the source of truth: what we decided about each candidate, joinable and auditable. Redpanda holds alerts in flight, and it does hold them on disk until retention expires, which is what lets a crashed worker resume from its last committed offset. What it never holds is the answer to "why was this object ranked 12 under this release." That lives in Postgres, and a consumer only acknowledges a message after the Postgres write commits.