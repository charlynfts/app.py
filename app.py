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

# Leer API Key desde secrets (obligatorio en Streamlit Cloud)
api_key = st.secrets.get("ANTHROPIC_API_KEY")
if not api_key:
    st.error("Falta la Anthropic API Key.\n\nAndá a Manage app → Secrets y agregá:\nANTHROPIC_API_KEY = \"sk-ant-...\"")
    st.stop()

# Modelo actual (marzo 2026 - usa el más reciente estable)
MODEL = "claude-sonnet-4-6"   # ← Si falla, probá "claude-sonnet-latest"

# Crear carpeta para frames
os.makedirs("frames", exist_ok=True)

# Inicializar base de datos (sin guardar path del video)
def init_db():
    conn = sqlite3.connect('donde_carajo.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS catalogs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            catalog_id INTEGER,
            name TEXT,
            location TEXT,
            description TEXT,
            frame_path TEXT,
            FOREIGN KEY(catalog_id) REFERENCES catalogs(id)
        )
    ''')
    conn.commit()
    return conn

conn = init_db()

# Extraer frames clave del video
def extract_frames(video_path, num_frames=8):
    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames == 0:
        return [], []

    step = max(1, total_frames // num_frames)
    frames_base64 = []
    frame_paths = []

    for i in range(0, total_frames, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = cap.read()
        if not ret:
            continue

        idx = len(frame_paths)
        frame_path = f"frames/catalog_{st.session_state.get('current_catalog_id', 0)}/frame_{idx}.jpg"
        os.makedirs(os.path.dirname(frame_path), exist_ok=True)
        cv2.imwrite(frame_path, frame)
        frame_paths.append(frame_path)

        _, buffer = cv2.imencode('.jpg', frame)
        b64 = base64.b64encode(buffer).decode('utf-8')
        frames_base64.append(b64)

        if len(frame_paths) >= num_frames:
            break

    cap.release()
    return frames_base64, frame_paths

# Limpieza robusta de la respuesta de Claude
def clean_json(text):
    text = text.strip()
    
    # Eliminar bloques markdown comunes
    text = re.sub(r'^```json\s*', '', text, flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r'^```$', '', text, flags=re.MULTILINE | re.IGNORECASE)
    
    # Quitar texto antes del primer {
    start_idx = text.find('{')
    if start_idx != -1:
        text = text[start_idx:]
    
    # Quitar texto después del último }
    end_idx = text.rfind('}')
    if end_idx != -1:
        text = text[:end_idx + 1]
    
    # Limpiar saltos y espacios extra
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# Analizar con Claude
def analyze_with_claude(frames_b64):
    client = anthropic.Anthropic(api_key=api_key)

    prompt = """Analizá estas imágenes y devolveme SOLO un JSON válido. Nada más.
No agregues texto, explicaciones, markdown ni código. Solo el JSON.

Formato exacto:
{
  "items": [
    {
      "name": "nombre de la prenda u objeto",
      "location": "ubicación precisa",
      "description": "descripción breve",
      "frame_number": número del frame (0 a n-1),
      "extra_notes": "notas adicionales si las hay"
    }
  ]
}

Devuelve SOLO el JSON."""

    content = [{"type": "text", "text": prompt}]
    for b64 in frames_b64:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": b64
            }
        })

    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        temperature=0.0,
        messages=[{"role": "user", "content": content}]
    )

    raw_text = response.content[0].text
    cleaned_text = clean_json(raw_text)

    try:
        return json.loads(cleaned_text)
    except json.JSONDecodeError as e:
        st.error(f"No pude parsear el JSON de Claude.\nError: {str(e)}\n\nRespuesta cruda que devolvió Claude:")
        st.code(raw_text, language="json")
        st.stop()

# Guardar en DB
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

# Búsqueda con Claude
def search_items(query):
    items = get_all_items()
    if not items:
        return "No hay nada catalogado todavía, boludo."

    history = []
    for name, loc, desc, frame, date, cat_id in items:
        history.append({"fecha": date, "prenda": name, "ubicacion": loc, "desc": desc, "foto": frame})

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

# ====================== INTERFAZ ======================
st.title("🧥 Dónde Carajo Puse Eso")
st.markdown("**La app anti-olvido definitiva** – Filmá tu placard y nunca más busques como loco")

tab1, tab2, tab3, tab4 = st.tabs(["📸 Catalogar", "🔍 Buscar", "📜 Historial", "❓ ¿Qué me falta?"])

with tab1:
    st.subheader("Filmá o subí video de tu placard/cajón")
    uploaded_video = st.file_uploader("Subí video MP4 (10-30 segundos ideal)", type=["mp4", "mov"])

    num_frames = st.slider("Cantidad de frames a analizar", 4, 12, 8)

    if uploaded_video and st.button("🚀 Catalogar ahora", type="primary"):
        with st.spinner("Procesando video + Claude..."):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                tmp.write(uploaded_video.getvalue())
                video_path = tmp.name

            try:
                frames_b64, frame_paths = extract_frames(video_path, num_frames)

                if not frames_b64:
                    st.error("No se pudieron extraer frames. Probá otro video.")
                else:
                    analysis = analyze_with_claude(frames_b64)
                    catalog_id = save_catalog(analysis)
                    st.session_state.current_catalog_id = catalog_id

                    st.success("✅ Catalogado con éxito!")
                    st.json(analysis)

                    st.subheader("Frames analizados")
                    for i, path in enumerate(frame_paths):
                        if os.path.exists(path):
                            st.image(path, caption=f"Frame {i}", width=300)
            finally:
                try:
                    os.unlink(video_path)
                except:
                    pass

with tab2:
    st.subheader("¿Qué carajo estás buscando?")
    search_query = st.text_input("Ej: jean negro, billetera, camisa roja...")

    if search_query and st.button("Buscar con Claude"):
        with st.spinner("Buscando..."):
            result = search_items(search_query)
            st.markdown(result)

with tab3:
    st.subheader("Tus catálogos anteriores")
    items = get_all_items()

    if not items:
        st.info("Todavía no catalogaste nada.")
    else:
        for name, location, desc, frame_path, date, cat_id in items:
            col1, col2 = st.columns([3, 1])
            with col1:
                st.write(f"**{name}** – {location}")
                st.caption(f"{desc} | {date}")
            with col2:
                if frame_path and os.path.exists(frame_path):
                    st.image(frame_path, width=150)
            st.divider()

with tab4:
    st.subheader("Comparar con catalogación anterior")

    c = conn.cursor()
    c.execute("SELECT id, date FROM catalogs ORDER BY date DESC")
    catalogs = c.fetchall()

    if len(catalogs) < 1:
        st.warning("Necesitás al menos un catálogo anterior")
    else:
        old_catalog = st.selectbox(
            "Elegí el catálogo viejo",
            [f"{date} (ID: {cid})" for cid, date in catalogs]
        )
        old_id = int(old_catalog.split("ID: ")[1].strip(")"))

        new_video = st.file_uploader("Subí video NUEVO del mismo lugar", type=["mp4", "mov"], key="new_video")

        if new_video and st.button("Comparar y ver qué desapareció"):
            with st.spinner("Analizando diferencia..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                    tmp.write(new_video.getvalue())
                    temp_path = tmp.name

                try:
                    frames_b64, _ = extract_frames(temp_path, 8)
                    new_analysis = analyze_with_claude(frames_b64)

                    c.execute("SELECT name, location, description FROM items WHERE catalog_id = ?", (old_id,))
                    old_items = c.fetchall()

                    client = anthropic.Anthropic(api_key=api_key)
                    compare_prompt = f"""Catálogo VIEJO:
{json.dumps([{"name": n, "ubicacion": l, "desc": d} for n,l,d in old_items], ensure_ascii=False)}

Catálogo NUEVO:
{json.dumps(new_analysis.get("items", []), ensure_ascii=False)}

Decime claramente:
- Qué prendas/objetos del viejo ya NO aparecen
- Cuáles cambiaron de lugar
- Sugerencias graciosas tipo "boludo, la perdiste de nuevo"

Respondé en argentino puro."""

                    response = client.messages.create(
                        model=MODEL,
                        max_tokens=1000,
                        messages=[{"role": "user", "content": compare_prompt}]
                    )
                    st.markdown("### Resultado de la comparación")
                    st.markdown(response.content[0].text)

                finally:
                    try:
                        os.unlink(temp_path)
                    except:
                        pass

st.caption("App anti-olvido argentina ❤️ – Si querés mejoras decime y la tuneamos")