# FIFA 21 native data

This directory is the tracked home for deterministic provenance, join audits,
and quarantine reports generated from FIFA 21's native databases—not machine
logs, Frostbite caches, or raw extracted game archives. The normalized player
catalog itself is promoted to `localfut/runtime-source/localfut21/players.json`,
where the server already loads it.

The read-only extraction workspace remains under `localfut/runtime/mined/` and
is intentionally ignored. A catalog generation step promotes only the derived
artifacts needed by the local server into this directory. Generated artifacts
must record hashes for their native database and metadata inputs so another
developer can verify their provenance without committing copyrighted game
files.

Website-scraped IDs are not accepted as a source for this directory.
