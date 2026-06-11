"""
compute_metrics.py
==================
Reads  data/utec_raw_professors.json  (output of utec_scraper.py)
Writes data/nodes.json, data/edges.json, data/network.json

Run:
    python preprocess/compute_metrics.py
"""

import json, os, re, math
import networkx as nx
from networkx.algorithms import community as nx_comm

RAW_PATH = os.path.join("data", "utec_raw_professors.json")

# ──────────────────────────────────────────────────────────────────────────────
#  DEPARTMENT MAPPING
# ──────────────────────────────────────────────────────────────────────────────
DEPT_RULES = [
    # (substring in dept name lowercase → dept_code, display label)
    ("ciencia de computación",          "CS",   "Computer Science"),
    ("computación",                     "CS",   "Computer Science"),
    ("facultad de computación",         "CS",   "Computer Science"),
    ("sistemas y seguridad",            "SYS",  "Systems & Security"),
    ("ingeniería civil",                "CE",   "Civil Eng."),
    ("civil y ambiental",               "CE",   "Civil Eng."),
    ("ingeniería industrial",           "IE",   "Industrial Eng."),
    ("gestión de la ingeniería",        "IE",   "Industrial Eng."),
    ("negocios",                        "BUS",  "Business"),
    ("ingeniería mecánica",             "ME",   "Mechanical Eng."),
    ("mecánica y de la energía",        "ME",   "Mechanical Eng."),
    ("ingeniería electrónica",          "EE",   "Electronics & Mech."),
    ("electrónica y mecatrónica",       "EE",   "Electronics & Mech."),
    ("bioingeniería",                   "BIO",  "Bioengineering"),
    ("química",                         "BIO",  "Bioengineering"),
    ("humanidades",                     "HUM",  "Humanities"),
    ("artes y ciencias sociales",       "HUM",  "Humanities"),
    ("ciencias",                        "SCI",  "Sciences"),
    # Research groups → mapped by theme
    ("analítica de datos",              "DS",   "Data Science"),
    ("dads",                            "DS",   "Data Science"),
    ("inteligencia artificial",         "AI",   "Artificial Intel."),
    ("ginia",                           "AI",   "Artificial Intel."),
    ("robótica",                        "EE",   "Electronics & Mech."),
    ("ric",                             "EE",   "Electronics & Mech."),
    ("energía",                         "ME",   "Mechanical Eng."),
    ("minas",                           "ME",   "Mechanical Eng."),
    ("concreto",                        "CE",   "Civil Eng."),
    ("resucon",                         "CE",   "Civil Eng."),
    ("infraestructura",                 "CE",   "Civil Eng."),
    ("msp",                             "IE",   "Industrial Eng."),
    ("simulación",                      "IE",   "Industrial Eng."),
]

def dept_code_label(dept_name: str) -> tuple[str, str]:
    if not dept_name:
        return ("UNK", "Unknown")
    dn = dept_name.lower()
    for substr, code, label in DEPT_RULES:
        if substr in dn:
            return (code, label)
    return ("UNK", "Unknown")


# ──────────────────────────────────────────────────────────────────────────────
#  STABLE ID from profile URL slug
# ──────────────────────────────────────────────────────────────────────────────
def make_id(profile_url: str) -> str:
    slug = profile_url.rstrip("/").split("/")[-1]
    # URL-decode common accents
    slug = slug.replace("%C3%A9","e").replace("%C3%A1","a") \
               .replace("%C3%AD","i").replace("%C3%B3","o") \
               .replace("%C3%BA","u").replace("%C3%B1","n")
    return slug[:40]


# ──────────────────────────────────────────────────────────────────────────────
#  LOAD & NORMALISE
# ──────────────────────────────────────────────────────────────────────────────
def load_raw() -> list[dict]:
    with open(RAW_PATH, encoding="utf-8") as f:
        raw = json.load(f)

    professors = []
    for p in raw:
        pid          = make_id(p["profile_url"])
        code, label  = dept_code_label(p.get("dept", ""))
        professors.append({
            "id":           pid,
            "name":         p["name"],
            "dept":         p.get("dept", "Unknown"),
            "dept_code":    code,
            "dept_label":   label,
            "areas":        p.get("areas", []),
            "h_index":      p.get("h_index") or 0,
            "pubs":         p.get("pub_count") or 0,
            "citations":    p.get("citations") or 0,
            "email":        p.get("email"),
            "photo_url":    p.get("photo_url"),
            "orcid":        p.get("orcid"),
            "scholar_url":  p.get("scholar_url"),
            "scopus_url":   p.get("scopus_url"),
            "linkedin_url": p.get("linkedin_url"),
            "profile_url":  p["profile_url"],
            "groups":       p.get("groups", []),
            "role":         p.get("role"),
            "bio":          p.get("bio"),
            # raw collaborator URLs — resolved to IDs after building url_map
            "_collab_urls": [c["profile_url"] for c in p.get("collaborators", [])],
        })
    return professors


# ──────────────────────────────────────────────────────────────────────────────
#  BUILD GRAPH
# ──────────────────────────────────────────────────────────────────────────────
def build_graph(professors: list[dict]):
    # URL → ID map (handle trailing-slash and encoding variants)
    url_map = {}
    for p in professors:
        url_map[p["profile_url"]]             = p["id"]
        url_map[p["profile_url"].rstrip("/")] = p["id"]

    G = nx.Graph()
    for p in professors:
        G.add_node(p["id"], **{k: v for k, v in p.items()
                               if not k.startswith("_")})

    edge_dict: dict[tuple, int] = {}
    for p in professors:
        for curl in p["_collab_urls"]:
            cid = url_map.get(curl) or url_map.get(curl.rstrip("/"))
            if cid and cid != p["id"]:
                key = tuple(sorted([p["id"], cid]))
                edge_dict[key] = edge_dict.get(key, 0) + 1

    # Weight: 2 if both mention each other, 1 if only one side
    for (s, t), w in edge_dict.items():
        G.add_edge(s, t, weight=w)

    # Fallback: if graph has very few edges, add shared-area edges
    if G.number_of_edges() < 10:
        print("⚠  Few real collaboration edges — adding shared-keyword edges as fallback.")
        for i, a in enumerate(professors):
            for b in professors[i+1:]:
                shared = set(a["areas"]) & set(b["areas"])
                if len(shared) >= 1:
                    key = tuple(sorted([a["id"], b["id"]]))
                    if key not in edge_dict:
                        G.add_edge(a["id"], b["id"], weight=len(shared))
                        edge_dict[key] = len(shared)

    return G, edge_dict


# ──────────────────────────────────────────────────────────────────────────────
#  COMPUTE & EXPORT
# ──────────────────────────────────────────────────────────────────────────────
def compute_and_export():
    professors = load_raw()
    print(f"Loaded {len(professors)} professors.")

    G, edge_dict = build_graph(professors)
    print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges.")

    # ── Centrality metrics ────────────────────────────────────────────────────
    degree_c   = nx.degree_centrality(G)
    between_c  = nx.betweenness_centrality(G, weight="weight", normalized=True)
    closeness  = nx.closeness_centrality(G)
    pr         = nx.pagerank(G, weight="weight", alpha=0.85)
    try:
        eigen = nx.eigenvector_centrality(G, weight="weight", max_iter=1000)
    except nx.PowerIterationFailedConvergence:
        eigen = {n: 0.0 for n in G.nodes}

    # ── Communities ───────────────────────────────────────────────────────────
    communities = list(nx_comm.greedy_modularity_communities(G, weight="weight"))
    comm_map    = {}
    for i, comm in enumerate(communities):
        for node in comm:
            comm_map[node] = i

    # ── Link prediction (Adamic-Adar on cross-dept non-edges) ─────────────────
    prof_map  = {p["id"]: p for p in professors}
    non_edges = list(nx.non_edges(G))
    aa_scores = {(u, v): s for u, v, s in nx.adamic_adar_index(G, ebunch=non_edges)}
    cn_scores = {(u, v): len(list(nx.common_neighbors(G, u, v))) for u, v in non_edges}

    predicted = sorted(
        [{"source": u, "target": v,
          "score": round(s, 4),
          "common_neighbors": cn_scores.get((u, v), 0),
          "source_dept": prof_map[u]["dept_code"],
          "target_dept": prof_map[v]["dept_code"]}
         for (u, v), s in aa_scores.items()
         if prof_map[u]["dept_code"] != prof_map[v]["dept_code"] and s > 0],
        key=lambda x: -x["score"]
    )[:15]

    # ── Build nodes.json ──────────────────────────────────────────────────────
    nodes_out = []
    for p in professors:
        nid = p["id"]
        nodes_out.append({
            **{k: v for k, v in p.items() if not k.startswith("_")},
            "degree":                   G.degree(nid),
            "weighted_degree":          sum(d["weight"] for _, _, d in G.edges(nid, data=True)),
            "degree_centrality":        round(degree_c[nid], 4),
            "betweenness_centrality":   round(between_c[nid], 4),
            "closeness_centrality":     round(closeness[nid], 4),
            "pagerank":                 round(pr[nid], 5),
            "eigenvector_centrality":   round(eigen[nid], 4),
            "community":                comm_map.get(nid, 0),
        })

    # ── Build edges.json ──────────────────────────────────────────────────────
    edges_out = [
        {"source": s, "target": t, "weight": w,
         "source_dept": prof_map[s]["dept_code"],
         "target_dept": prof_map[t]["dept_code"],
         "is_cross_dept": prof_map[s]["dept_code"] != prof_map[t]["dept_code"]}
        for (s, t), w in edge_dict.items()
    ]

    network = {
        "nodes":           nodes_out,
        "edges":           edges_out,
        "predicted_links": predicted,
        "num_nodes":       G.number_of_nodes(),
        "num_edges":       G.number_of_edges(),
        "num_communities": len(communities),
    }

    os.makedirs("data", exist_ok=True)
    for fname, obj in [("nodes.json", nodes_out),
                        ("edges.json", edges_out),
                        ("network.json", network)]:
        with open(os.path.join("data", fname), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        print(f"✓ data/{fname}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\nTop 5 by degree:")
    for n in sorted(nodes_out, key=lambda x: -x["degree"])[:5]:
        print(f"  {n['name']:<35} degree={n['degree']}  bet={n['betweenness_centrality']}")
    print(f"\n{len(communities)} communities detected.")
    print(f"Top predicted link: {predicted[0] if predicted else 'none'}")


if __name__ == "__main__":
    compute_and_export()