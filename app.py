import streamlit as st
import anthropic
import cv2
import base64
import sqlite3
import os
import json
import re
from datetime import datetime
import tempfile

# Configuración de la página
st.set_page_config(
    page_title="Dónde Carajo Puse Eso",
    page_icon="🧥📍",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# API Key desde secrets
api_key = st.secrets.get("ANTHROPIC_API_KEY")
if not api_key:
    st.error("Falta la API Key en Secrets. Andá a Manage app → Secrets y agregá ANTHROPIC_API_KEY")
    st.stop()

# Modelo actual (2026 - estable y funcional)
MODEL = "claude-sonnet-4-6"

os.makedirs("frames", exist_ok=True)

# Base de datos
def init_db():
    conn = sqlite3.connect('donde_carajo.db')
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS catalogs (id INTEGER PRIMARY KEY, date TEXT)')
    c.execute('''CREATE TABLE IF NOT EXISTS items 
                 (id INTEGER PRIMARY KEY, catalog_id INTEGER, name TEXT, location TEXT, 
                  description TEXT, frame_path TEXT)''')
    conn.commit()
    return conn

conn = init_db()

# Extraer frames
def extract_frames(video_path, num_frames=8):
    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames == 0:
        return [], []

    step = max(1, total_frames // num_frames)
    frames_b64 = []
    frame_paths = []

    for i in range(0, total_frames, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = cap.read()
        if not ret: continue

        idx = len(frame_paths)
        path = f"frames/catalog_{st.session_state.get('current_catalog_id', 0)}/frame_{idx}.jpg"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cv2.imwrite(path, frame)
        frame_paths.append(path)

        _, buffer = cv2.imencode('.jpg', frame)
        frames_b64.append(base64.b64encode(buffer).decode('utf-8'))

        if len(frame_paths) >= num_frames: break

    cap.release()
    return frames_b64, frame_paths

# Limpieza agresiva del JSON
def clean_json(text):
    text = text.strip()

    # Quitar bloques markdown
    text = re.sub(r'```json\s*|\s*```', '', text, flags=re.IGNORECASE)

    # Quitar texto antes del primer { y después del último }
    start = text.find('{')
    if start != -1:
        text = text[start:]
    end = text.rfind('}') + 1
    if end > 0:
        text = text[:end]

    # Quitar comas colgantes antes de ] o }
    text = re.sub(r',\s*([}\]])', r'\1', text)

    # Quitar saltos de línea dentro de strings y espacios extra
    text = re.sub(r'\s+', ' ', text).strip()

    # Último ajuste: comillas dobles mal puestas
    text = re.sub(r'""', '"', text)

    return text

# Analizar con Claude
def analyze_with_claude(frames_b64):
    client = anthropic.Anthropic(api_key=api_key)

    prompt = """Analiza estas imágenes y devuelve SOLO un JSON válido, sin texto adicional, sin explicaciones, sin markdown, sin "aquí tienes", sin código de bloque. SOLO el JSON crudo.

Formato estricto:
{
  "items": [
    {
      "name": "texto",
      "location": "texto",
      "description": "texto",
      "frame_number": número,
      "extra_notes": "texto o null"
    }
  ]
}

Si no hay items: {"items": []}"""

    content = [{"type": "text", "text": prompt}]
    for b64 in frames_b64:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})

    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        temperature=0.0,
        messages=[{"role": "user", "content": content}]
    )

    raw_text = response.content[0].text
    cleaned = clean_json(raw_text)

    # Debug temporal (podés comentarlo después)
    st.write("JSON limpio antes de parsear:")
    st.code(cleaned, language="json")

    try:
        parsed = json.loads(cleaned)
        return parsed
    except json.JSONDecodeError as e:
        st.error(f"Parseo falló. Error: {str(e)}\nPosición: {e.pos}")
        st.code("Raw original:")
        st.code(raw_text)
        st.stop()

# Guardar catálogo
def save_catalog(analysis):
    c = conn.cursor()
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    c.execute("INSERT INTO catalogs (date) VALUES (?)", (date,))
    catalog_id = c.lastrowid

    for item in analysis.get("items", []):
        frame_num = item.get("frame_number", 0)
        frame_path = f"frames/catalog_{catalog_id}/frame_{frame_num}.jpg"

        c.execute("""
            INSERT INTO items (catalog_id, name, location, description, frame_path)
            VALUES (?, ?, ?, ?, ?)
        """, (
            catalog_id,
            item.get("name"),
            item.get("location"),
            item.get("description") or item.get("extra_notes"),
            frame_path
        ))

    conn.commit()
    return catalog_id

# Obtener items
def get_all_items():
    c = conn.cursor()
    c.execute("""
        SELECT i.name, i.location, i.description, i.frame_path, c.date, c.id
        FROM items i JOIN catalogs c ON i.catalog_id = c.id
        ORDER BY c.date DESC
    """)
    return c.fetchall()

# Búsqueda
def search_items(query):
    items = get_all_items()
    if not items:
        return "No hay nada catalogado todavía."

    history = [{"fecha": date, "prenda": name, "ubicacion": loc, "desc": desc, "foto": frame} 
               for name, loc, desc, frame, date, _ in items]

    client = anthropic.Anthropic(api_key=api_key)
    prompt = f"""Usuario pregunta: "{query}"

Historial:
{json.dumps(history, indent=2, ensure_ascii=False)}

Respondé claro y en argentino:
- Dónde está
- Fecha
- Foto si corresponde
- Si no aparece, sugerí catalogar de nuevo"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

# Interfaz
st.title("🧥 Dónde Carajo Puse Eso")
st.markdown("**La app anti-olvido definitiva**")

tab1, tab2, tab3, tab4 = st.tabs(["📸 Catalogar", "🔍 Buscar", "📜 Historial", "❓ ¿Qué me falta?"])

with tab1:
    uploaded_video = st.file_uploader("Subí video MP4", type=["mp4", "mov"])
    num_frames = st.slider("Frames a analizar", 4, 12, 8)

    if uploaded_video and st.button("🚀 Catalogar ahora", type="primary"):
        with st.spinner("Procesando..."):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                tmp.write(uploaded_video.getvalue())
                video_path = tmp.name

            try:
                frames_b64, frame_paths = extract_frames(video_path, num_frames)
                if not frames_b64:
                    st.error("No se pudieron extraer frames.")
                else:
                    analysis = analyze_with_claude(frames_b64)
                    catalog_id = save_catalog(analysis)
                    st.session_state.current_catalog_id = catalog_id

                    st.success("✅ Catalogado!")
                    st.json(analysis)

                    st.subheader("Frames")
                    for i, p in enumerate(frame_paths):
                        if os.path.exists(p):
                            st.image(p, caption=f"Frame {i}", width=300)
            finally:
                try: os.unlink(video_path)
                except: pass

with tab2:
    search_query = st.text_input("¿Qué buscás?")
    if search_query and st.button("Buscar"):
        result = search_items(search_query)
        st.markdown(result)

with tab3:
    items = get_all_items()
    if not items:
        st.info("Nada catalogado aún.")
    else:
        for name, loc, desc, frame, date, _ in items:
            col1, col2 = st.columns([3,1])
            col1.write(f"**{name}** – {loc}")
            col1.caption(f"{desc} | {date}")
            if frame and os.path.exists(frame):
                col2.image(frame, width=150)

with tab4:
    c = conn.cursor()
    c.execute("SELECT id, date FROM catalogs ORDER BY date DESC")
    catalogs = c.fetchall()

    if not catalogs:
        st.warning("Necesitás un catálogo anterior")
    else:
        old = st.selectbox("Catálogo viejo", [f"{d} (ID: {i})" for i,d in catalogs])
        old_id = int(old.split("ID: ")[1].strip(")"))

        new_video = st.file_uploader("Video nuevo", type=["mp4", "mov"], key="compare")

        if new_video and st.button("Comparar"):
            with st.spinner("Comparando..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                    tmp.write(new_video.getvalue())
                    temp_path = tmp.name

                try:
                    frames_b64, _ = extract_frames(temp_path, 8)
                    new_analysis = analyze_with_claude(frames_b64)

                    c.execute("SELECT name, location, description FROM items WHERE catalog_id = ?", (old_id,))
                    old_items = c.fetchall()

                    client = anthropic.Anthropic(api_key=api_key)
                    prompt = f"""VIEJO: {json.dumps([{"name":n,"ubicacion":l,"desc":d} for n,l,d in old_items])}

NUEVO: {json.dumps(new_analysis.get("items", []))}

Decime qué falta, qué se movió y algún comentario gracioso en argentino."""

                    resp = client.messages.create(model=MODEL, max_tokens=1000, messages=[{"role": "user", "content": prompt}])
                    st.markdown("### Resultado")
                    st.markdown(resp.content[0].text)

                finally:
                    try: os.unlink(temp_path)
                    except: pass

st.caption("App para olvidadizos ❤️ – Si falla algo, decime")