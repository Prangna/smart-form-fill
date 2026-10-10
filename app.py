import os
import base64
import json
import re
from flask import Flask, request, jsonify, render_template
from groq import Groq
from PIL import Image
import fitz  # PyMuPDF
import io
import psycopg2
from datetime import datetime

app = Flask(__name__)
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
VISION_MODEL = "qwen/qwen3.8-27b"        # for reading images
TEXT_MODEL   = "llama-3.3-70b-versatile"  # for generating HTML/text

# ──────────────────────────────────────────────
# DB INIT
# ──────────────────────────────────────────────
def get_db():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversions (
                id SERIAL PRIMARY KEY,
                filename TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id SERIAL PRIMARY KEY,
                name TEXT,
                message TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("DB init error:", e)

init_db()

# ──────────────────────────────────────────────
# ROUTES
# ──────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/convert", methods=["POST"])
def convert():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    filename = file.filename.lower()
    file_bytes = file.read()

    try:
        # PDF → first page as image
        if filename.endswith(".pdf"):
            pdf_doc = fitz.open(stream=file_bytes, filetype="pdf")
            page = pdf_doc[0]
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("jpeg")
            media_type = "image/jpeg"
        else:
            img = Image.open(io.BytesIO(file_bytes))
            # Convert RGBA/palette/other modes to RGB
            if img.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")
            if max(img.size) > 1600:
                img.thumbnail((1600, 1600), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            img_bytes = buf.getvalue()
            media_type = "image/jpeg"

        b64 = base64.standard_b64encode(img_bytes).decode("utf-8")

        # STEP 1 — Vision model reads image → extracts form structure as JSON
        vision_response = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                    {"type": "text", "text": """Analyse this school proforma/form image carefully.
Extract and return ONLY a JSON object with this structure:
{
  "title": "form title",
  "sections": [
    {
      "heading": "section name or empty string",
      "fields": [
        {"label": "field label", "type": "text|date|number|textarea", "name": "snake_case_id"}
      ]
    }
  ],
  "has_table": true,
  "table_columns": ["Subject", "Marks", "Grade"],
  "table_rows": ["English", "Hindi", "Maths"]
}
Rules:
- Include ALL fields visible in the image
- For tables (like subject/marks tables), use has_table=true and list columns and rows
- name must be unique snake_case (e.g. student_name, roll_number, english_marks)
- Return ONLY valid JSON, no explanation, no markdown fences"""}
                ]
            }],
            max_tokens=900,
            temperature=0.1
        )

        raw_json = vision_response.choices[0].message.content.strip()
        raw_json = re.sub(r'^```[a-z]*\n?', '', raw_json)
        raw_json = re.sub(r'\n?```$', '', raw_json)

        try:
            form_structure = json.loads(raw_json)
        except json.JSONDecodeError:
            # If JSON parsing fails, fall back to direct HTML generation
            form_structure = None

        # STEP 2 — Text model generates full HTML from structure
        if form_structure:
            html_prompt = f"""You are generating a clean, print-ready editable HTML form for a school.

Here is the form structure extracted from a scanned proforma:
{json.dumps(form_structure, indent=2)}

Generate a complete, self-contained HTML document that:
1. Replicates ALL fields and sections from the structure above
2. Uses <input type="text">, <input type="date">, <input type="number">, or <textarea> as specified
3. Gives every input a descriptive name AND id matching the field's "name" value
4. For tables: creates a proper HTML table with headers and one input per cell
5. Has clean, print-friendly inline CSS (green/white school theme, Arial font)
6. Includes a visible Print button at the bottom (calls window.print())
7. Starts with <!DOCTYPE html> and is fully self-contained with all CSS inline

Return ONLY the complete HTML document — no explanation, no markdown fences."""
        else:
            # Fallback: direct prompt without structure
            html_prompt = f"""Generate a complete editable HTML form for a school proforma.
The form should have common school fields: student name, roll number, class, father name, mother name, date of birth, subjects table with marks and grades, CGPA, attendance, remarks.
Make it clean, print-friendly with green/white theme, Arial font.
Include a Print button. Start with <!DOCTYPE html>. Return ONLY HTML."""

        html_response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": html_prompt}],
            max_tokens=4000,
            temperature=0.1
        )

        html = html_response.choices[0].message.content.strip()
        html = re.sub(r'^```[a-z]*\n?', '', html)
        html = re.sub(r'\n?```$', '', html)

        # Log to DB
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("INSERT INTO conversions (filename) VALUES (%s)", (file.filename,))
            conn.commit()
            cur.close()
            conn.close()
        except:
            pass

        return jsonify({"html": html})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/feedback", methods=["POST"])
def feedback():
    data = request.get_json()
    name = data.get("name", "Anonymous")
    message = data.get("message", "")
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO feedback (name, message) VALUES (%s, %s)", (name, message))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/feedback")
def admin_feedback():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT name, message, created_at FROM feedback ORDER BY created_at DESC")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        result = [{"name": r[0], "message": r[1], "time": str(r[2])} for r in rows]
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
