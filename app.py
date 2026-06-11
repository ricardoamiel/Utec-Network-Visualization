"""
app.py  –  UTECVis: Faculty Collaboration Network (Intelligent Adapter Layer)
"""
from flask import Flask, render_template, jsonify, request, Response
import json
import os

try:
    import requests as req_lib
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

app = Flask(__name__)
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

def _load(filename):
    with open(os.path.join(DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/network")
def api_network():
    """
    Carga tu network.json y normaliza dinámicamente cualquier discrepancia 
    de nombres de columnas o tipos de datos para que D3 lo renderice de inmediato.
    """
    try:
        data = _load("network.json")
    except (FileNotFoundError, json.JSONDecodeError):
        # Fallback: Si no encuentra network.json, intenta unir los archivos sueltos
        try:
            nodes_raw = _load("nodes.json")
            edges_raw = _load("edges.json")
            data = {"nodes": nodes_raw, "edges": edges_raw}
        except FileNotFoundError as e:
            return jsonify({"error": f"Faltan archivos en 'data/'. No se encontró: {e.filename}"}), 404

    # Extraer listas de nodos y enlaces soportando mayúsculas/minúsculas de tu script
    raw_nodes = data.get("nodes", data.get("Nodes", []))
    raw_edges = data.get("edges", data.get("Edges", []))
    
    node_map = {}
    
    # 1. Normalizar propiedades de los Nodos (Evita que queden como undefined/NaN)
    for n in raw_nodes:
        # Detecta variantes comunes de IDs (id, ProfessorID, id_professor, etc.)
        nid = str(n.get("id", n.get("ProfessorID", n.get("id_professor", n.get("id_profesor", "")))))
        if not nid:
            continue
            
        name = n.get("name", n.get("Name", "Profesor Anónimo"))
        dept = str(n.get("dept_code", n.get("Department", n.get("departamento", "UNK")))).upper()
        
        # Homologación de códigos de departamento cortos para que coincidan con los colores del CSS
        if "COMP" in dept or "CS" in dept or "CIENCIA" in dept: dept_code = "CS"
        elif "ELEC" in dept or "EE" in dept or "ELECTRON" in dept: dept_code = "EE"
        elif "MECH" in dept or "ME" in dept or "MECAN" in dept: dept_code = "ME"
        elif "CIVIL" in dept or "CE" in dept: dept_code = "CE"
        elif "ENV" in dept or "AMBI" in dept or "VOL" in dept: dept_code = "ENV"
        elif "IND" in dept or "IE" in dept: dept_code = "IE"
        elif "MATH" in dept or "MAT" in dept: dept_code = "MATH"
        elif "PHYS" in dept or "FIS" in dept: dept_code = "PHY"
        else: dept_code = "UNK"

        node_map[nid] = {
            "id": nid,
            "name": name,
            "dept_code": dept_code,
            #"email": n.get("email", ""),                     # <-- AGREGAR ESTE
            #"role": n.get("role", "Docente Investigador"),   # <-- AGREGAR ESTE
            #"groups": n.get("groups", []),                   # <-- AGREGAR ESTE
            "areas": n.get("areas", n.get("ResearchAreas", n.get("lineas_investigacion", []))),
            "h_index": int(n.get("h_index", 0)),
            "pubs": int(n.get("pubs", n.get("publications", 0))),
            "photo_url": n.get("photo_url", n.get("PhotoUrl", "")),
            "community": int(n.get("community", 0)),
            "betweenness_centrality": float(n.get("betweenness_centrality", 0.0)),
            "pagerank": float(n.get("pagerank", 0.04)),
            "closeness_centrality": float(n.get("closeness_centrality", 0.0)),
            "degree": 0,             # Inicializado para conteo dinámico
            "weighted_degree": 0
        }

    # 2. Normalizar Enlaces (Evita errores de comparación estricta de D3)
    processed_edges = []
    for e in raw_edges:
        src = e.get("source", e.get("Source", e.get("id_profesor_1", e.get("id_professor_1", ""))))
        tgt = e.get("target", e.get("Target", e.get("id_profesor_2", e.get("id_professor_2", ""))))
        
        # Desempaquetar en caso de que D3 ya haya guardado sub-objetos en ejecuciones previas
        if isinstance(src, dict) and "id" in src: src = src["id"]
        if isinstance(tgt, dict) and "id" in tgt: tgt = tgt["id"]
        
        src, tgt = str(src), str(tgt)
        w = int(e.get("weight", e.get("Weight", 1)))

        if src in node_map and tgt in node_map:
            # Calcular grados de adyacencia en tiempo real si faltaran en el JSON
            node_map[src]["degree"] += 1
            node_map[src]["weighted_degree"] += w
            node_map[tgt]["degree"] += 1
            node_map[tgt]["weighted_degree"] += w

            s_dept = node_map[src]["dept_code"]
            t_dept = node_map[tgt]["dept_code"]

            processed_edges.append({
                "source": src,
                "target": tgt,
                "weight": w,
                "is_cross_dept": s_dept != t_dept,
                "source_dept": s_dept,
                "target_dept": t_dept
            })

    # Si tu archivo network.json ya traía métricas precalculadas reales, las preservamos
    for nid, node in node_map.items():
        orig = next((n for n in raw_nodes if str(n.get("id", n.get("ProfessorID", ""))) == nid), {})
        if "degree" in orig: node["degree"] = int(orig["degree"])
        if "weighted_degree" in orig: node["weighted_degree"] = int(orig["weighted_degree"])

    # 3. Agrupamiento por comunidades por defecto si vienen vacías
    depts_list = list(set(n["dept_code"] for n in node_map.values()))
    for n in node_map.values():
        if n["community"] == 0:
            n["community"] = depts_list.index(n["dept_code"])

    nodes_list = list(node_map.values())

    return jsonify({
        "nodes": nodes_list,
        "edges": processed_edges,
        "num_nodes": len(nodes_list),
        "num_edges": len(processed_edges),
        "num_communities": data.get("num_communities", len(depts_list)),
        "predicted_links": data.get("predicted_links", [])
    })

@app.route("/api/nodes")
def api_nodes():
    return jsonify(_load("nodes.json"))

@app.route("/api/edges")
def api_edges():
    return jsonify(_load("edges.json"))

@app.route("/api/photo")
def proxy_photo():
    """Evita problemas de CORS al cargar las imágenes desde cris.utec.edu.pe"""
    if not _HAS_REQUESTS:
        return Response("pip install requests", status=503)
    url = request.args.get("url", "")
    if not url or "cris.utec.edu.pe" not in url:
        return Response(status=400)
    try:
        r = req_lib.get(url, timeout=6, headers={"User-Agent": "Mozilla/5.0 Chrome/124"})
        return Response(r.content, content_type=r.headers.get("content-type", "image/jpeg"))
    except Exception:
        return Response(status=404)

if __name__ == "__main__":
    app.run(debug=True, port=5001, threaded=True)