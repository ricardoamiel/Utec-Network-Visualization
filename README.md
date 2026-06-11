# UTECVis — Faculty Collaboration Network

**DS5343 · Visualización de Datos · 2026-1**  
A D3.js + Flask network visualization of UTEC faculty research collaborations.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. (Optional) Re-generate metrics — pre-computed data already included
python preprocess/compute_metrics.py

# 3. Run the Flask app
python app.py

# 4. Open in browser
http://127.0.0.1:5001
```

---

## Project Structure

```
utecvis/
├── app.py                     # Flask application
├── requirements.txt
├── data/
│   ├── network.json           # Full graph (nodes + edges + predicted links)
│   ├── nodes.json             # Professors with pre-computed metrics
│   └── edges.json             # Collaboration edges with weights
├── preprocess/
│   └── compute_metrics.py     # networkx metrics computation (run once)
├── templates/
│   └── index.html             # D3.js visualization
└── scraping/
    ├── utec_scraper.py        # UTEC website scraper (reference code — do not run automatically)
    └── scholar_scraper.py     # Google Scholar scraper (reference code — do not run automatically)
```

---

## Visualization Tasks

| # | Task | Visual Encoding |
|---|------|----------------|
| T1 | **Most Prolific Collaborators** — Who has the highest degree centrality? | Node size ∝ degree; ranked sidebar list |
| T2 | **Bridge Professors** — Who are the critical intermediaries between departments? | Dashed halo ∝ betweenness; ranked sidebar list |
| T3 | **Department Connectivity** — Which departments collaborate most across boundaries? | Node colour = dept; cross-dept edge count in sidebar |
| T4 | **Research Area Hubs** — Which research areas generate cross-departmental links? | Click area → highlight matching professors |
| T5 | **Predicted Future Links** — Which new collaborations are most likely? | Adamic–Adar score; highlighted dashed edge on click |

---

## Network Metrics (computed with `networkx`)

| Metric | Description |
|--------|-------------|
| **Degree centrality** | `degree / (n-1)` — number of direct collaborators |
| **Betweenness centrality** | Fraction of shortest paths passing through a node |
| **Closeness centrality** | Inverse of average shortest-path distance |
| **PageRank** | Google's PageRank adapted for collaboration networks (α = 0.85) |
| **Eigenvector centrality** | Influence accounting for neighbour influence |
| **Community** | Greedy modularity optimisation (`greedy_modularity_communities`) |
| **Link prediction** | Adamic–Adar index on non-existing cross-dept edges |

---

## Interaction & Animation (Visualization Mantra)

- **Overview**: Force-directed layout shows all 32 professors, coloured by department
- **Zoom & Filter**: Pan/zoom (scroll or buttons), department filter bar, search bar
- **Details on Demand**: Click any node → full profile panel appears on the right

Events used: `mouseover`, `mouseout`, `click`, drag events, zoom events.

---

## Scraping (Reference Only)

The scraping scripts in `scraping/` are provided as reference code.  
**Do not run them automatically** — respect `robots.txt` and server load.

- `utec_scraper.py`: Crawls UTEC faculty directory pages
- `scholar_scraper.py`: Retrieves h-index, citations, publications from Google Scholar (includes anti-detection measures)