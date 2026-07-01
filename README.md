# 📊 Pricing Analyzer (UAE Competitive Pricing Intelligence Prototype)

## Overview

The **Pricing Analyzer** is a prototype data pipeline designed to scrape/extract and compare restaurant menu pricing across food delivery platforms in the UAE.

This project focuses on building a **scalable competitive pricing intelligence system** that can:

- Extract structured menu and pricing data from food delivery platforms  
- Normalize and store pricing across multiple restaurant branches  
- Enable future cross-platform and cross-branch price comparison accurately 

Currently, the system is in **Phase 1 (Talabat integration)**, with extensions planned for additional platforms.

---

## Project Objective

To build a **modular and extensible scraping + analytics pipeline** that enables:

- Competitive price analysis across delivery platforms  
- Branch-level menu extraction for restaurants in UAE  
- Future expansion into multi-platform price intelligence  

---

## 🏗️ System Architecture

The system follows a modular pipeline design:

```
config/ → Platform configs, URLs, rate limits
scrapers/ → Platform-specific scraping logic
parsers/ → JSON/HTML parsing logic per platform
models/ → SQLite database schema & persistence layer
normalizer/ → Cross-branch item matching & price standardization
comparator/ → Price delta computation engine
output/ → Reporting & export layer (CSV-ready outputs)
main.py → Pipeline orchestrator
```

---

## ⚙️ Methodology & Approach

The system was built using an **Agile-inspired iterative development approach**, focusing on:

- Rapid prototyping  
- Real-time debugging using browser **DevTools**
- Incremental pipeline validation per module  
- Continuous refinement based on live scraping results  

Each phase was validated before expanding to the next component.

---

## 🔍 Data Extraction Strategy

### Phase 1: Talabat (Completed)

#### Approach Used
- Reverse-engineered Talabat web pages using Chrome DevTools  
- Identified embedded structured JSON (`initialMenuState`)  
- Extracted:
  - Branch information  
  - Menu items  
  - Pricing details  
  - Category structure  

#### Key Insight
Talabat stores menu data inside a structured JSON object inside the page response rather than requiring full API reverse engineering.

#### Result
- 4 branches successfully scraped  
- 862 menu items extracted  
- Data stored in SQLite database with price snapshots  
<img width="366" height="156" alt="image" src="https://github.com/user-attachments/assets/af4caa5e-7a45-452b-8a82-d3a10a55705f" />

---

## ⚠️ Challenges & Failure Points (Phase 1)

### 1. Incorrect JSON Path Mapping
- Initial parser targeted incorrect structure (`pageData`)
- Actual structure was:

``` 
initialMenuState.menuData.items 
```


✔ Fix: Updated parser to align with real runtime structure

---

### 2. Missing Branch Data (Critical Issue)
- Some branches returned empty menu data
- Root cause:
- Missing `aid` (area ID / delivery zone parameter)

✔ Fix:
- Added correct `aid` parameters to branch URLs  
- Restored full menu extraction across all branches  

---

### 3. Data Variability Across Branches
- Same restaurant showed different item counts per branch:
- Meadows → 162 items  
- DIP → 327 items  
- Al Barsha → 124 items  

✔ Insight:
Menu availability is **branch-dependent**, requiring branch-level comparison instead of brand-level aggregation.

---
### Talabat JSON Extraction
1. Open Talabat restaurant page  
2. Right-click → Inspect  
3. Open Network tab  
4. Filter → Doc  
5. Open response  
6. Search:
   - `initialMenuState`
   - `menuData`
   - `menu`
   - `restaurant`

 <img width="1920" height="896" alt="image" src="https://github.com/user-attachments/assets/f04b0223-c068-4dec-8eee-1cf1555bef2c" />
  
---
## 🚧 Phase 2: Noon Integration (Resolved Approach)

### Initial Approach
- Attempted browser automation using Playwright
- Expected similar structure to Talabat

### Failure Point
- Chromium navigation error:

```
net::ERR_HTTP2_PROTOCOL_ERROR
```

### Root Cause
Noon Food does not rely on DOM-embedded data like Talabat.

---


## ⚠️ Ethical / Compliance Note

This project is intended for:

- Educational purposes  
- Data engineering practice  
- Market research simulation  

It only uses:
- Publicly accessible endpoints  
- Non-authenticated (“guest”) APIs  
- No login or bypass mechanisms  
