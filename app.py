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
import pandas as pd

app = Flask(__name__)
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

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
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("DB init error:", e)

init_db()

# ──────────────────────────────────────────────
# CBSE GRADING
# ──────────────────────────────────────────────
CBSE_GRADE_POINTS = {
    'A1': 10, 'A2': 9, 'B1': 8, 'B2': 7,
    'C1': 6,  'C2': 5, 'D':  4, 'E1': 3, 'E2': 2
}

def marks_to_grade(marks):
    try:
        m = float(marks)
        if m >= 91: return 'A1'
        if m >= 81: return 'A2'
        if m >= 71: return 'B1'
        if m >= 61: return 'B2'
        if m >= 51: return 'C1'
        if m >= 41: return 'C2'
        if m >= 33: return 'D'
        if m >= 21: return 'E1'
        return 'E2'
    except:
        return ''

def grade_to_points(grade):
    if isinstance(grade, str):
        return CBSE_GRADE_POINTS.get(grade.strip().upper(), 0)
    return 0

def calc_cgpa(subject_marks_dict):
    """Average grade points of best 5 subjects."""
    points = []
    for subj, val in subject_marks_dict.items():
        if val is None or str(val).strip() == '':
            continue
        gp = grade_to_points(str(val))
        if gp == 0:
            grade = marks_to_grade(val)
            gp = grade_to_points(grade)
        if gp > 0:
            points.append(gp)
    if not points:
        return None
    top5 = sorted(points, reverse=True)[:5]
    return round(sum(top5) / len(top5), 2)


# ──────────────────────────────────────────────
# ROUTES
# ──────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/convert", methods=["POST"])
def convert():
    """Convert uploaded proforma image/PDF to editable HTML form via Groq vision."""
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
            # Resize if too large
            img = Image.open(io.BytesIO(file_bytes))
            if max(img.size) > 1600:
                img.thumbnail((1600, 1600), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            img_bytes = buf.getvalue()
            media_type = "image/jpeg"

        b64 = base64.standard_b64encode(img_bytes).decode("utf-8")

        prompt = """You are converting a school proforma/form into an editable HTML form.

Analyse this proforma carefully and generate a complete, clean HTML form that:
1. Replicates the exact structure and all fields of the original
2. Uses <input type="text"> for text fields, <input type="date"> for dates, <input type="number"> for marks/numbers
3. Gives every input a descriptive name AND id attribute matching the field label (e.g. name="student_name" id="student_name")
4. Preserves tables, sections, and layout as closely as possible
5. Is print-friendly with proper styling
6. Includes a visible "Print" button at the bottom

Return ONLY the complete HTML document — no explanation, no markdown fences.
The HTML must start with <!DOCTYPE html> and be fully self-contained with inline CSS."""

        response = client.chat.completions.create(
            model=MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                    {"type": "text", "text": prompt}
                ]
            }],
            max_tokens=4096,
            temperature=0.1
        )

        html = response.choices[0].message.content.strip()
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


@app.route("/parse-excel", methods=["POST"])
def parse_excel():
    """Parse uploaded Excel/CSV and return headers + all rows."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    filename = file.filename.lower()
    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(file, dtype=str)
        elif filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(file, dtype=str)
        else:
            return jsonify({"error": "Please upload .xlsx, .xls or .csv"}), 400

        df = df.dropna(how='all')
        df.columns = [str(c).strip() for c in df.columns]
        df = df.fillna('')

        return jsonify({
            "headers": list(df.columns),
            "rows": df.to_dict(orient='records'),
            "total": len(df)
        })
    except Exception as e:
        return jsonify({"error": f"Could not read file: {str(e)}"}), 500


@app.route("/map-fields", methods=["POST"])
def map_fields():
    """Use Groq to map Excel column headers → form field names intelligently."""
    data = request.get_json()
    excel_headers = data.get("excel_headers", [])
    form_fields = data.get("form_fields", [])

    if not excel_headers:
        return jsonify({"error": "No Excel headers provided"}), 400

    prompt = f"""You are helping auto-fill a school proforma form from an Excel/CSV sheet.

EXCEL COLUMNS available: {json.dumps(excel_headers)}

FORM FIELDS to fill: {json.dumps(form_fields)}

Your job:
1. Map each FORM FIELD to the most likely EXCEL COLUMN that contains its data.
2. Use fuzzy/semantic matching: "Pupil Name"→"Student Name", "Roll No"→"Roll Number", "Eng"→"English", etc.
3. Identify which Excel columns contain SUBJECT MARKS or GRADES (for CGPA calculation).
4. Identify which column is the STUDENT NAME identifier for the dropdown.
5. Identify which column is the ROLL NUMBER (if present).

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  "mapping": {{
    "<form_field_name>": "<excel_column_name_or_null>"
  }},
  "subject_columns": ["col1", "col2"],
  "student_name_column": "<column_name>",
  "roll_column": "<column_name_or_null>"
}}

Rules:
- If no good match for a form field, use null
- subject_columns = all columns that look like subject marks/grades (Eng, Hindi, Math, Science, SST, Computer, etc.)
- Be generous with subject detection"""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.1
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r'^```[a-z]*\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)
        result = json.loads(raw)
        return jsonify(result)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Could not parse AI response: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/calc-cgpa", methods=["POST"])
def calc_cgpa_route():
    """Calculate CGPA + per-subject grade details for a student row."""
    data = request.get_json()
    student_row = data.get("student_row", {})
    subject_columns = data.get("subject_columns", [])

    subject_data = {col: student_row.get(col, '') for col in subject_columns}
    cgpa = calc_cgpa(subject_data)

    subject_details = {}
    for subj, val in subject_data.items():
        if val and str(val).strip():
            v = str(val).strip()
            if v.replace('.', '').isdigit():
                grade = marks_to_grade(float(v))
            else:
                grade = v.upper()
            gp = grade_to_points(grade)
            subject_details[subj] = {
                "marks": val,
                "grade": grade,
                "grade_point": gp
            }

    return jsonify({
        "cgpa": cgpa,
        "subject_details": subject_details
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
