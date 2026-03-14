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

# ====================== CONFIG ======================
st.set_page_config(page_title="Dónde Carajo Puse Eso", page_icon="🧥📍", layout="wide")

# API Key desde Secrets
api_key = st.secrets.get("ANTHROPIC_API_KEY")
if not api_key:
    st.error("Falta la API Key en Secrets → Manage app → Secrets")
    st.stop()

MODEL = "claude-sonnet-4-6"   # ← Modelo correcto y actual (marzo 2026)

os.makedirs("frames", exist_ok=True)

# ====================== DB ======================
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

# ====================== FUNCIONES ======================
def extract_frames(video_path, num_frames=8):
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, total // num_frames)
    frames_b64 = []
    frame_paths = []

    for i in range(0, total, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = cap.read()
        if not ret: continue

        idx = len(frame_paths)
        path = f"frames/catalog_{st.session_state.get('current_catalog_id', 0)}/frame_{idx}.jpg"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cv2.imwrite(path, frame)
        frame_paths.append(path)

        _, buffer = cv2.imencode('.jpg', frame)
        frames_b64.append(base64.b64encode(buffer).decode())
        if len(frame_paths) >= num_frames: break

    cap.release()
    return frames_b64, frame_paths

def clean_json(text):
    """Limpia todo lo que Claude pueda agregar"""
    text = text.strip()
    # Quitar markdown
    text = re.sub(r'```json
    text = re.sub(r'```\s*$', '', text)
    # Quitar texto antes del primer {
    if '{' in text:
        text = text[text.find('{'):]
    # Quitar texto después del último }
    if '}' in text:
        text = text[:text.rfind('}') + 1]
    return text.strip()

def analyze_with_claude(frames_b64):
    client = anthropic.Anthropic(api_key=api_key)

    prompt = """Eres un organizador obsesivo. Analiza las imágenes y devuelve ÚNICAMENTE un JSON válido.
NO agregues texto antes, después, ni explicaciones. NO uses markdown. Solo el JSON exacto.

Formato requerido:
{
  "items": [
    {
      "name": "camisa roja a rayas",
      "location": "estante superior, segunda percha desde la izquierda",
      "description": "...",
      "frame_number": 2,
      "extra_notes": "..."
    }
  ]
}

Devuelve SOLO el JSON."""

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

    try:
        return json.loads(cleaned)
    except Exception:
        st.error("Claude no devolvió JSON puro. Mostrando respuesta cruda:")
        st.code(raw_text, language="json")
        st.stop()

# (El resto de funciones save_catalog, get_all_items, search_items se mantienen igual que antes, pero con MODEL fijo)

# ====================== APP ======================
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
                analysis = analyze_with_claude(frames_b64)
                catalog_id = save_catalog(analysis)   # (usa la función que ya tenías)
                st.session_state.current_catalog_id = catalog_id

                st.success("✅ ¡Catalogado!")
                st.json(analysis)

                st.subheader("Frames analizados")
                for i, p in enumerate(frame_paths):
                    if os.path.exists(p):
                        st.image(p, caption=f"Frame {i}", width=300)
            finally:
                try: os.unlink(video_path)
                except: pass

# (Los otros tabs quedan igual que en la versión anterior que te di)

st.caption("App anti-olvido • Hecha con amor para los que perdemos todo ❤️")
