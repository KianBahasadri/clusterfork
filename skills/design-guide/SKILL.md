---
name: design-guide
description: Design guide for user-facing interfaces and high-density operational cockpits.
---

# Design Guide: Dark Cockpit & High-Density Telemetry

Design system specification for serious operational software, telemetry dashboards, observability consoles, and technical command centers.

## 1. Core Philosophy: The Dark Cockpit

* **Visual Calm by Default**: Healthy, nominal, expected, or inactive states visually recede into low-contrast slate/zinc surfaces.
* **Emergence of Meaning**: Prominence corresponds strictly to operational importance. Anomalies, threshold breaches, and required actions emerge clearly from the quiet baseline.
* **No Ambient Noise**: Eliminate decorative borders, heavy gradients, saturated accents, glassmorphic glows, and emoji chrome.

## 2. Information Density & Layout

* **Maximize Data-Ink Ratio**: Maximize telemetry per viewport without cognitive friction.
* **Spatial Closeness**: Keep correlated metrics spatially adjacent (e.g. Ingress QPS, Latency percentiles, and Error rates).
* **Coherent Information Surfaces**: Avoid turning every isolated data point into a rounded card with excessive padding. Use position, grouping, alignment, and hairline borders (`#171d29` / `#21293a`).

## 3. Typography & Tabular Alignment

* **UI Chrome & Labels**: Proportional sans-serif (`system-ui`, `-apple-system`, `Inter`, `Geist Sans`) at 10–11px with medium weight and restrained letter-spacing.
* **Data & Telemetry**: Monospace with tabular numerals (`ui-monospace`, `Geist Mono`, `JetBrains Mono`) strictly for latencies, memory counters, QPS numbers, timestamps, node IDs, and hash values.
* **Controlled Hierarchy**:
  * Micro metadata / Units: 8.5–9.5px uppercase
  * Standard tabular metrics: 10–11px
  * Primary KPI values: 16–18px bold monospace tabular

## 4. Semantic Color Tokens

* **Canvas / Surface Hierarchy**:
  * Canvas Base: `#090b10`
  * Surface Deck: `#0e121a`
  * Elevated Surface: `#141923`
  * Surface Inset: `#07090d`
* **Semantic Status Signals**:
  * **Nominal / Healthy**: Subdued emerald (`#10b981` / background `rgba(16, 185, 129, 0.08)`).
  * **Warning / Anomaly**: High-visibility amber (`#f59e0b` / background `rgba(245, 158, 11, 0.12)`).
  * **Critical / Incident**: Immediate red (`#ef4444` / background `rgba(239, 68, 68, 0.12)`).
  * **Interactive Focus**: Aviation blue (`#3b82f6`).

## 5. Reference Implementation

An interactive operational reference implementation is available in [index.html](file:///home/kian/clusterfork/skills/design-guide/index.html). It includes:
* Master cluster telemetry ribbon with live 1s updates.
* Multi-band synchronized time-series canvas with interactive crosshair inspection.
* 48-node micro topology matrix with zone distribution and hardware saturation stack.
* Sortable and filterable fleet node directory.
* Real-time telemetry log stream with severity filtering.
* Interactive condition simulator (`Nominal Baseline`, `Latency Anomaly`, `Network Drop`).
