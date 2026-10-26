---
id: guide-power-dependencies
title: Power dependencies and cascading failure
kind: guidance
roles: water, coordinator
version: 1.1
status: active
updated: 2026-09-10
provenance: Authored for the TerraShield POC by the project team; fictional guidance.
license: Project-internal demonstration text
---
## Single points of failure
In Harbor Vale both pump stations and the water works draw power from the Marsh Lane substation. If the substation fails, all three lose function even when they are dry, and pumping stops in Harbor Flats and Riverside.

## Breaking the cascade
Protecting the substation before its flood hour prevents the cascade at its root. A backup generator keeps one dependent asset running and is a fallback when the substation cannot be protected in time. Sequence matters: an intervention completed after the supply fails only limits further damage.
